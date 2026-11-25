"""Automatic audit trail.

Every insert, update and delete made through the ORM is written to the
``audit_log`` table with the user who made it and the old/new values.
"""
import json
from datetime import date, datetime
from decimal import Decimal

from flask import has_request_context
from flask_login import current_user
from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from .models import AuditLog, Customer, Project, utcnow

SKIP_ENTITIES = {"AuditLog"}
SKIP_FIELDS = {"id", "created_at"}
SECRET_FIELDS = {"password_hash"}

ENTITY_LABELS = {
    "ProjectTask": "Task",
    "TemplateTask": "Template task",
    "ProjectTemplate": "Project template",
    "SerialNumber": "Serial number",
    "CMO": "CMO",
}


def entity_label(name):
    return ENTITY_LABELS.get(name, name)


def _ser(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _user_id():
    try:
        if has_request_context() and current_user.is_authenticated:
            return current_user.id
    except Exception:  # pragma: no cover - defensive
        pass
    return None


def _entry(obj, action):
    insp = inspect(obj)
    mapper = insp.mapper
    name = mapper.class_.__name__
    if name in SKIP_ENTITIES:
        return None
    changes = {}
    for attr in mapper.column_attrs:
        key = attr.key
        if key in SKIP_FIELDS:
            continue
        if action == "update":
            hist = insp.attrs[key].history
            if not hist.has_changes():
                continue
            old = hist.deleted[0] if hist.deleted else None
            new = hist.added[0] if hist.added else None
            if old == new:
                continue
        else:
            val = insp.dict.get(key)
            if val in (None, "") or (action == "create" and val is False):
                continue
            old, new = (None, val) if action == "create" else (val, None)
        if key in SECRET_FIELDS:
            old, new = ("***" if old else None), ("***" if new else None)
        changes[key] = [_ser(old), _ser(new)]
    if action == "update":
        for rel in mapper.relationships:
            if rel.secondary is None:
                continue
            hist = insp.attrs[rel.key].history
            if hist.added or hist.deleted:
                changes[rel.key] = [[str(x) for x in hist.deleted], [str(x) for x in hist.added]]
        if not changes:
            return None

    project_id = insp.dict.get("id") if isinstance(obj, Project) else insp.dict.get("project_id")
    customer_id = insp.dict.get("id") if isinstance(obj, Customer) else insp.dict.get("customer_id")
    try:
        summary = str(obj)
    except Exception:  # pragma: no cover - str() hitting an expired attribute
        summary = f"{name} #{insp.dict.get('id')}"
    return {
        "created_at": utcnow(),
        "user_id": _user_id(),
        "action": action,
        "entity": name,
        "entity_id": insp.dict.get("id"),
        "project_id": project_id,
        "customer_id": customer_id,
        "summary": summary[:300],
        "changes": json.dumps(changes, default=str),
    }


def _before_flush(session, flush_context, instances):
    pending = []
    for obj in list(session.dirty):
        if session.is_modified(obj, include_collections=True):
            e = _entry(obj, "update")
            if e:
                pending.append(e)
    for obj in list(session.deleted):
        e = _entry(obj, "delete")
        if e:
            pending.append(e)
    session.info["_audit_pending"] = pending


def _after_flush(session, flush_context):
    rows = session.info.pop("_audit_pending", [])
    new_projects = {o.id for o in session.new if isinstance(o, Project)}
    for obj in list(session.new):
        # tasks copied from the template when a project is created: one entry for the project is enough
        if type(obj).__name__ == "ProjectTask" and obj.project_id in new_projects:
            continue
        e = _entry(obj, "create")
        if e:
            rows.append(e)
    if rows:
        session.connection().execute(AuditLog.__table__.insert(), rows)


def init_audit():
    if not event.contains(Session, "before_flush", _before_flush):
        event.listen(Session, "before_flush", _before_flush)
        event.listen(Session, "after_flush", _after_flush)
