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
    """Personal page: only my projects (PM or with tasks assigned to me), and
    only tasks assigned to me or not assigned to anyone."""
    me = current_user.id
    all_open = (
        Project.query.join(Customer).filter(Project.status.in_(OPEN_PROJECT_STATUSES))
        .order_by(Customer.name, Project.name).all()
    )
    projects = [
        p for p in all_open
        if p.project_manager_id == me or any(t.assignee_id == me and not t.is_finished for t in p.tasks)
    ]
    ids = [p.id for p in projects] or [-1]
    tasks = (
        ProjectTask.query.filter(ProjectTask.project_id.in_(ids), ProjectTask.status.notin_(("done", "na")))
        .filter(db.or_(ProjectTask.assignee_id == me, ProjectTask.assignee_id.is_(None)))
        .order_by(ProjectTask.due_date.is_(None), ProjectTask.due_date, ProjectTask.position).all()
    )
    my_tasks = [t for t in tasks if t.assignee_id == me]
    unassigned = [t for t in tasks if t.assignee_id is None]
    ready = [t for t in tasks if t.is_ready and t.status == "todo"]
    overdue = [t for t in tasks if t.is_overdue]
    blocked = [t for t in tasks if t.status == "blocked"]
    next_steps = {p.id: [t for t in tasks if t.project_id == p.id and t.is_ready][:3] for p in projects}

    soon = date.today() + timedelta(days=14)
    to_issue = (
        Invoice.query.filter(Invoice.project_id.in_(ids), Invoice.status.in_(("planned", "to_issue")))
        .filter(db.or_(Invoice.issue_date.is_(None), Invoice.issue_date <= soon))
        .order_by(Invoice.issue_date.is_(None), Invoice.issue_date).all()
    )
    unpaid = (Invoice.query.filter(Invoice.project_id.in_(ids), Invoice.status.in_(("issued", "sent")))
              .order_by(Invoice.due_date).all())
    others_tasks = [t.id for t in ProjectTask.query.filter(
        ProjectTask.project_id.in_(ids), ProjectTask.assignee_id.isnot(None), ProjectTask.assignee_id != me)]
    recent = (AuditLog.query.filter(AuditLog.project_id.in_(ids))
              .filter(db.not_(db.and_(AuditLog.entity == "ProjectTask", AuditLog.entity_id.in_(others_tasks or [-1]))))
              .order_by(AuditLog.created_at.desc()).limit(12).all())
    return render_template(
        "main/dashboard.html", projects=projects, my_tasks=my_tasks, unassigned=unassigned, ready=ready,
        overdue=overdue, blocked=blocked, to_issue=to_issue, unpaid=unpaid, recent=recent, next_steps=next_steps,
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


@bp.get("/analytics")
def analytics():
    from collections import Counter, defaultdict
    from decimal import Decimal

    from ..models import PROJECT_STATUSES, ProjectTemplate

    template_id = request.args.get("template_id", type=int)
    pq = Project.query.join(Customer)
    if template_id:
        pq = pq.filter(Project.template_id == template_id)
    projects = pq.order_by(Customer.name, Project.name).all()
    open_projects = [p for p in projects if p.is_open]

    status_counts = Counter(p.status for p in projects)
    by_status = [(v, t, status_counts.get(v, 0)) for v, t in PROJECT_STATUSES]
    product_counts = Counter(p.template.name if p.template else "No template" for p in open_projects)

    money = defaultdict(lambda: defaultdict(Decimal))
    for p in projects:
        if p.status == "cancelled":
            continue
        m = money[p.currency]
        m["contract"] += p.contract_value
        m["net_invoiced"] += p.net_invoiced
        m["paid"] += p.paid_amount
        if p.is_open:
            m["remaining"] += p.remaining_to_invoice
    project_ids = {p.id for p in projects}
    invoices = [i for i in Invoice.query.all() if i.project_id in project_ids]
    for i in invoices:
        if i.status in ("issued", "sent"):
            money[i.currency or "EUR"]["outstanding"] += i.signed_amount
        if i.status in ("planned", "to_issue"):
            money[i.currency or "EUR"]["planned"] += i.signed_amount
    main_currency = max(money, key=lambda c: money[c]["contract"]) if money else "EUR"

    # net invoiced per month of the selected year, main currency
    today = date.today()
    year = request.args.get("year", today.year, type=int)
    per_month = {(year, m): Decimal(0) for m in range(1, 13)}
    for i in invoices:
        if i.issue_date and i.status in ("issued", "sent", "paid") and (i.currency or "EUR") == main_currency:
            k = (i.issue_date.year, i.issue_date.month)
            if k in per_month:
                per_month[k] += i.signed_amount
    month_max = max([v for v in per_month.values()] + [Decimal(1)])
    monthly = [(date(y, m, 1), v) for (y, m), v in per_month.items()]

    # open tasks per person
    open_ids = [p.id for p in open_projects] or [-1]
    tasks = ProjectTask.query.filter(ProjectTask.project_id.in_(open_ids),
                                     ProjectTask.status.notin_(("done", "na"))).all()
    load = Counter(t.assignee.display_name if t.assignee else "Unassigned" for t in tasks)
    overdue_by = Counter(t.assignee.display_name if t.assignee else "Unassigned" for t in tasks if t.is_overdue)
    workload = sorted(((n, c, overdue_by.get(n, 0)) for n, c in load.items()), key=lambda x: -x[1])

    horizon = today + timedelta(days=90)
    go_lives = sorted(
        [p for p in open_projects if p.target_go_live and not p.go_live_date and p.target_go_live <= horizon],
        key=lambda p: p.target_go_live,
    )
    done_tasks = ProjectTask.query.filter(ProjectTask.project_id.in_(open_ids), ProjectTask.status == "done").count()
    all_tasks = ProjectTask.query.filter(ProjectTask.project_id.in_(open_ids), ProjectTask.status != "na").count()

    from ..services import budget_overview, budget_years

    budget_rows, budget_totals = budget_overview(year, template_id=template_id)
    signed = [k for p in projects if p.status != "cancelled" for k in p.contracts
              if (k.signed_date or k.start_date) and (k.signed_date or k.start_date).year == year]
    year_stats = {
        "contracts": len(signed),
        "started": sum(1 for p in projects if p.start_date and p.start_date.year == year),
        "went_live": sum(1 for p in projects if p.go_live_date and p.go_live_date.year == year),
        "planned_go_live": sum(1 for p in open_projects if p.target_go_live and p.target_go_live.year == year
                               and not p.go_live_date),
    }

    return render_template(
        "main/analytics.html", year=year, years=budget_years([year]), budget_rows=budget_rows,
        budget_totals=budget_totals, year_stats=year_stats, projects=projects, open_projects=open_projects, by_status=by_status,
        product_counts=product_counts.most_common(), money=money, main_currency=main_currency,
        monthly=monthly, month_max=month_max, workload=workload, go_lives=go_lives,
        task_pct=round(100 * done_tasks / all_tasks) if all_tasks else 0, open_task_count=len(tasks),
        templates=ProjectTemplate.query.order_by(ProjectTemplate.name).all(), template_id=template_id,
    )
