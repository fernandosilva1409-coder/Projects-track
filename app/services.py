"""Business logic: task dependency graph and project creation."""
from .extensions import db
from .models import Project, ProjectTask, User


def compute_steps(tasks):
    """Return {task.id: step}. Step 1 tasks have no open prerequisites inside
    the list; tasks sharing a step number can run in parallel."""
    ids = {t.id for t in tasks}
    memo = {}

    def step(t, stack=()):
        if t.id in memo:
            return memo[t.id]
        if t.id in stack:  # defensive: cycles are rejected on save
            return 1
        deps = [d for d in t.depends_on if d.id in ids]
        s = 1 + max((step(d, stack + (t.id,)) for d in deps), default=0)
        memo[t.id] = s
        return s

    for t in tasks:
        step(t)
    return memo


def creates_cycle(task, new_deps):
    """True if making ``task`` depend on ``new_deps`` would create a loop."""
    seen = set()
    stack = list(new_deps)
    while stack:
        cur = stack.pop()
        if cur is task or cur.id == task.id:
            return True
        if cur.id in seen:
            continue
        seen.add(cur.id)
        stack.extend(cur.depends_on)
    return False


def create_project_from_template(project: Project):
    """Copy the template's tasks and dependencies into the project."""
    if not project.template:
        return
    mapping = {}
    for tt in project.template.tasks:
        pt = ProjectTask(
            name=tt.name, phase=tt.phase, position=tt.position,
            template_task_id=tt.id, notes=tt.description,
        )
        project.tasks.append(pt)
        mapping[tt.id] = pt
    for tt in project.template.tasks:
        mapping[tt.id].depends_on = [mapping[d.id] for d in tt.depends_on if d.id in mapping]


def user_choices(ctx=None):
    users = User.query.filter_by(active=True).order_by(User.full_name, User.username).all()
    return [(str(u.id), u.display_name) for u in users]


def renumber(tasks):
    for i, t in enumerate(sorted(tasks, key=lambda t: (t.phase != "pre", t.position, t.id)), start=1):
        t.position = i
    db.session.flush()


def budget_overview(year, customers=None, template_id=None):
    """Per customer and currency for one year: budget, contracted, invoiced
    (net), paid, planned and what is left of the budget.

    - contracted: value of contracts signed in the year (start date if no signing date)
    - invoiced: issued/sent/paid invoices minus credit notes, by issue date
    - paid: invoices paid in the year (paid date, else issue date)
    - planned: planned / to-issue invoices with an issue date in the year
    """
    from collections import defaultdict
    from decimal import Decimal

    from .models import Customer, CustomerBudget

    if customers is None:
        customers = Customer.query.order_by(Customer.name).all()
    rows = {}

    def row(c, cur):
        key = (c.id, cur)
        if key not in rows:
            rows[key] = {"customer": c, "currency": cur, "budget": None, "budget_days": None,
                         **{k: Decimal(0) for k in ("contracted", "contracted_days", "invoiced", "paid", "planned")}}
        return rows[key]

    for c in customers:
        for b in c.budgets:
            if b.year == year:
                r = row(c, b.currency or "EUR")
                r["budget"] = (r["budget"] or 0) + b.amount
                if b.man_days is not None:
                    r["budget_days"] = (r["budget_days"] or 0) + b.man_days
        for p in c.projects:
            if p.status == "cancelled" or (template_id and p.template_id != template_id):
                continue
            for k in p.contracts:
                d = k.signed_date or k.start_date
                if d and d.year == year and k.total_value is not None:
                    r = row(c, k.currency or "EUR")
                    r["contracted"] += k.total_value
                    r["contracted_days"] += k.effort_man_days or 0
            for i in p.invoices:
                cur = i.currency or "EUR"
                if i.status in ("issued", "sent", "paid") and i.issue_date and i.issue_date.year == year:
                    row(c, cur)["invoiced"] += i.signed_amount
                if i.status == "paid":
                    d = i.paid_date or i.issue_date
                    if d and d.year == year:
                        row(c, cur)["paid"] += i.signed_amount
                if i.status in ("planned", "to_issue") and i.issue_date and i.issue_date.year == year:
                    row(c, cur)["planned"] += i.signed_amount

    out = []
    for r in rows.values():
        b = r["budget"]
        r["forecast"] = r["invoiced"] + r["planned"]
        r["remaining"] = (b - r["forecast"]) if b is not None else None
        r["pct_invoiced"] = round(100 * r["invoiced"] / b) if b else None
        r["pct_forecast"] = round(100 * r["forecast"] / b) if b else None
        out.append(r)
    out.sort(key=lambda r: (r["customer"].name.lower(), r["currency"]))

    totals = defaultdict(lambda: defaultdict(Decimal))
    for r in out:
        t = totals[r["currency"]]
        for k in ("budget", "contracted", "invoiced", "paid", "planned", "forecast"):
            t[k] += r[k] or 0
        t["remaining"] = t["budget"] - t["forecast"]
    return out, dict(totals)


def budget_years(extra=()):
    from datetime import date

    from .models import CustomerBudget, Invoice

    years = {date.today().year, *extra}
    years |= {y for (y,) in CustomerBudget.query.with_entities(CustomerBudget.year).distinct()}
    years |= {d.year for (d,) in Invoice.query.with_entities(Invoice.issue_date).filter(Invoice.issue_date.isnot(None))}
    return sorted(years, reverse=True)


STARTED_STATUSES = ("in_progress", "done")


def blocking_prerequisites(task, status, deps=None):
    """Prerequisites that stop ``task`` from moving to ``status``."""
    if status not in STARTED_STATUSES:
        return []
    deps = task.depends_on if deps is None else deps
    return [d for d in deps if not d.is_finished]


MANUAL_STATUSES = ("on_hold", "cancelled")  # set by people, never changed automatically


def is_go_live_task(task):
    n = task.name.lower().replace("-", " ")
    return "go live" in n or "golive" in n


def computed_status(project):
    """Status that follows from the tasks:
    - Pre-project: pre-project checklist open and no project task started
    - Active: work in progress
    - Live / hypercare: the go-live task is done (or a go-live date is set)
    - Closed: every task is done or not applicable
    """
    tasks = project.tasks
    if tasks and all(t.is_finished for t in tasks):
        return "closed"
    pre = project.tasks_in("pre")
    work = project.tasks_in("project")
    pre_done = all(t.is_finished for t in pre)
    work_started = any(t.status in STARTED_STATUSES for t in work)
    if pre and not pre_done and not work_started:
        return "pre_project"
    if project.go_live_date or any(is_go_live_task(t) and t.status == "done" for t in work):
        return "live"
    return "active"


def sync_project_status(project):
    """Bring the project status in line with its tasks (unless it is on hold
    or cancelled). Also fills the go-live date from the go-live task.
    Returns the new status if it changed, else None."""
    for t in project.tasks:
        if is_go_live_task(t) and t.status == "done" and not project.go_live_date:
            project.go_live_date = t.completed_date
    if project.status in MANUAL_STATUSES:
        return None
    new = computed_status(project)
    if new != project.status:
        project.status = new
        return new
    return None


def set_status_manually(project, status):
    """Validate a status chosen by a person. Returns a list of errors."""
    open_tasks = [t.name for t in project.tasks if not t.is_finished]
    if status == "closed" and open_tasks:
        shown = ", ".join(open_tasks[:5]) + (f" (+{len(open_tasks) - 5} more)" if len(open_tasks) > 5 else "")
        return [f"Cannot close: {len(open_tasks)} task(s) still open: {shown}."]
    if status in MANUAL_STATUSES:
        project.status = status
        return []
    # any other choice means "follow the tasks"
    project.status = computed_status(project)
    return []


def import_customers(names, template, open_last=0, manager=None, created_by=None):
    """Create customers (if missing), each with a project from ``template``
    named after the customer. All tasks are marked done except the last
    ``open_last``. Customers that already have a project from this template
    are skipped, so it is safe to run twice. Returns one message per name.
    The caller commits."""
    from datetime import date

    from .models import Customer

    messages = []
    seen = set()
    for name in (n.strip() for n in names):
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        customer = Customer.query.filter(db.func.lower(Customer.name) == name.lower()).first()
        created = customer is None
        if created:
            customer = Customer(name=name)
            db.session.add(customer)
            db.session.flush()
        if any(p.template_id == template.id for p in customer.projects):
            messages.append(f"{customer.name}: already has a {template.name} project, skipped")
            continue
        project = Project(customer_id=customer.id, template=template, name=customer.name,
                          project_manager=manager, created_by=created_by,
                          status="pre_project", start_date=date.today())
        create_project_from_template(project)
        db.session.add(project)
        ordered = sorted(project.tasks, key=lambda t: (t.phase != "pre", t.position))
        cut = max(len(ordered) - max(open_last, 0), 0)
        for t in ordered[:cut]:
            t.status, t.completed_date = "done", date.today()
        sync_project_status(project)
        db.session.flush()
        still_open = ", ".join(t.name for t in ordered[cut:]) or "none"
        messages.append(f"{customer.name}: {'customer and ' if created else ''}project created; open: {still_open}")
    return messages
