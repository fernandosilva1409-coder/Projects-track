import csv
import io
import json
from datetime import date, timedelta

from flask import Blueprint, Response, render_template, request
from flask_login import current_user

from ..extensions import db
from ..models import (
    INVOICE_KINDS, INVOICE_STATUSES, OPEN_PROJECT_STATUSES, AuditLog, Customer, Invoice, Project, ProjectTask, User,
)

bp = Blueprint("main", __name__)


@bp.get("/")
def dashboard():
    open_projects = (
        Project.query.join(Customer).filter(Project.status.in_(OPEN_PROJECT_STATUSES))
        .order_by(Customer.name, Project.name).all()
    )
    open_ids = [p.id for p in open_projects] or [-1]
    open_tasks = (
        ProjectTask.query.filter(ProjectTask.project_id.in_(open_ids), ProjectTask.status.notin_(("done", "na")))
        .order_by(ProjectTask.due_date.is_(None), ProjectTask.due_date, ProjectTask.position).all()
    )
    my_tasks = [t for t in open_tasks if t.assignee_id == current_user.id]
    ready = [t for t in open_tasks if t.is_ready and t.status == "todo"]
    overdue = [t for t in open_tasks if t.is_overdue]
    blocked = [t for t in open_tasks if t.status == "blocked"]

    soon = date.today() + timedelta(days=14)
    to_issue = (
        Invoice.query.filter(Invoice.status.in_(("planned", "to_issue")))
        .filter(db.or_(Invoice.issue_date.is_(None), Invoice.issue_date <= soon))
        .order_by(Invoice.issue_date.is_(None), Invoice.issue_date).all()
    )
    unpaid = Invoice.query.filter(Invoice.status.in_(("issued", "sent"))).order_by(Invoice.due_date).all()
    recent = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(12).all()
    return render_template(
        "main/dashboard.html", projects=open_projects, my_tasks=my_tasks, ready=ready, overdue=overdue,
        blocked=blocked, to_issue=to_issue, unpaid=unpaid, recent=recent,
    )


def _invoice_query():
    q = Invoice.query.join(Project).join(Customer)
    f = {
        "status": request.args.get("status", ""),
        "kind": request.args.get("kind", ""),
        "year": request.args.get("year", ""),
        "customer_id": request.args.get("customer_id", type=int),
    }
    if f["status"] == "open":
        q = q.filter(Invoice.status.in_(("planned", "to_issue", "issued", "sent")))
    elif f["status"]:
        q = q.filter(Invoice.status == f["status"])
    if f["kind"]:
        q = q.filter(Invoice.kind == f["kind"])
    if f["year"].isdigit():
        y = int(f["year"])
        q = q.filter(Invoice.issue_date >= date(y, 1, 1), Invoice.issue_date <= date(y, 12, 31))
    if f["customer_id"]:
        q = q.filter(Project.customer_id == f["customer_id"])
    return q.order_by(Invoice.issue_date.desc().nulls_last(), Invoice.id.desc()), f


@bp.get("/invoices")
def invoices():
    q, f = _invoice_query()
    rows = q.all()
    totals = {}
    for i in rows:
        if i.status == "cancelled":
            continue
        t = totals.setdefault(i.currency or "EUR", {"invoiced": 0, "credited": 0, "paid": 0, "open": 0})
        if i.kind == "invoice":
            t["invoiced"] += i.amount
        else:
            t["credited"] += i.amount
        if i.status == "paid":
            t["paid"] += i.signed_amount
        elif i.status in ("issued", "sent"):
            t["open"] += i.signed_amount
    years = sorted({i.issue_date.year for i in Invoice.query.all() if i.issue_date}, reverse=True)
    return render_template(
        "main/invoices.html", invoices=rows, filters=f, totals=totals, years=years,
        customers=Customer.query.order_by(Customer.name).all(),
        kinds=INVOICE_KINDS, statuses=INVOICE_STATUSES,
    )


@bp.get("/invoices.csv")
def invoices_csv():
    q, _f = _invoice_query()
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow(["Customer", "Project", "Type", "Number", "Description", "Status", "Issue date", "Due date",
                "Paid date", "Man-days", "Amount", "Signed amount", "Currency", "Credit note for"])
    for i in q.all():
        w.writerow([
            i.project.customer.name, i.project.name, i.kind_label, i.number or "", i.description or "",
            i.status_label, i.issue_date or "", i.due_date or "", i.paid_date or "", i.man_days or "",
            i.amount, i.signed_amount, i.currency or "", str(i.related_invoice) if i.related_invoice else "",
        ])
    return Response(
        "﻿" + out.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=invoices-{date.today()}.csv"},
    )


@bp.get("/history")
def history():
    page = request.args.get("page", 1, type=int)
    f = {"entity": request.args.get("entity", ""), "user_id": request.args.get("user_id", type=int)}
    q = AuditLog.query
    if f["entity"]:
        q = q.filter(AuditLog.entity == f["entity"])
    if f["user_id"]:
        q = q.filter(AuditLog.user_id == f["user_id"])
    logs = q.order_by(AuditLog.created_at.desc()).paginate(page=page, per_page=50)
    entities = [r[0] for r in db.session.query(AuditLog.entity).distinct().order_by(AuditLog.entity)]
    return render_template(
        "main/history.html", logs=logs, project=None, filters=f, entities=entities,
        users=User.query.order_by(User.full_name).all(),
    )


@bp.app_template_filter("changes")
def changes_filter(raw):
    try:
        return json.loads(raw or "{}")
    except ValueError:
        return {}
