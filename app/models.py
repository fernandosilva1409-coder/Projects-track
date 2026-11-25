from datetime import date, datetime, timezone
from decimal import Decimal

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Choice lists (value, label). Values are stored in the database.
# ---------------------------------------------------------------------------
PROJECT_STATUSES = [
    ("pre_project", "Pre-project"),
    ("active", "Active"),
    ("on_hold", "On hold"),
    ("live", "Live / hypercare"),
    ("closed", "Closed"),
    ("cancelled", "Cancelled"),
]
OPEN_PROJECT_STATUSES = ("pre_project", "active", "on_hold", "live")
TASK_STATUSES = [
    ("todo", "To do"),
    ("in_progress", "In progress"),
    ("blocked", "Blocked"),
    ("done", "Done"),
    ("na", "Not applicable"),
]
FINISHED_TASK_STATUSES = ("done", "na")
TASK_PHASES = [("pre", "Pre-project"), ("project", "Project")]
INVOICE_KINDS = [("invoice", "Invoice"), ("credit_note", "Credit note")]
INVOICE_STATUSES = [
    ("planned", "Planned"),
    ("to_issue", "To issue"),
    ("issued", "Issued"),
    ("sent", "Sent to customer"),
    ("paid", "Paid"),
    ("cancelled", "Cancelled"),
]
ONBOARDING_STATUSES = [
    ("not_started", "Not started"),
    ("in_progress", "In progress"),
    ("done", "Done"),
    ("na", "N/A"),
]
ENVIRONMENTS = [("INT", "INT"), ("PROD", "PROD"), ("OTHER", "Other")]
CURRENCIES = [("EUR", "EUR"), ("CHF", "CHF"), ("USD", "USD"), ("GBP", "GBP")]
ATTACHMENT_CATEGORIES = [
    ("contract", "Contract"),
    ("invoice", "Invoice / credit note"),
    ("documentation", "Project documentation"),
    ("other", "Other"),
]


def label(choices, value):
    return dict(choices).get(value, value or "")


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    full_name = db.Column(db.String(120))
    email = db.Column(db.String(200))
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)

    @property
    def is_active(self):
        return self.active

    @property
    def display_name(self):
        return self.full_name or self.username

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __str__(self):
        return self.display_name


# ---------------------------------------------------------------------------
# Customers and contacts
# ---------------------------------------------------------------------------
class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), unique=True, nullable=False)
    vat_number = db.Column(db.String(60))
    address = db.Column(db.Text)
    country = db.Column(db.String(80))
    website = db.Column(db.String(300))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=utcnow)

    contacts = db.relationship(
        "Contact", backref="customer", cascade="all, delete-orphan", order_by="Contact.name"
    )
    projects = db.relationship("Project", backref="customer", order_by="Project.created_at.desc()")
    budgets = db.relationship(
        "CustomerBudget", backref="customer", cascade="all, delete-orphan", order_by="CustomerBudget.year.desc()"
    )

    @property
    def invoice_contacts(self):
        return [c for c in self.contacts if c.is_invoice_contact]

    def __str__(self):
        return self.name


class Contact(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customer.id"), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(120))
    email = db.Column(db.String(200))
    phone = db.Column(db.String(60))
    is_invoice_contact = db.Column(db.Boolean, default=False, nullable=False)
    notes = db.Column(db.Text)

    def __str__(self):
        return self.name


class CustomerBudget(db.Model):
    """Budget agreed with / expected from a customer for one calendar year."""

    __table_args__ = (db.UniqueConstraint("customer_id", "year", "currency", name="uq_budget_customer_year"),)

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customer.id"), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    currency = db.Column(db.String(3), default="EUR", nullable=False)
    man_days = db.Column(db.Numeric(10, 2))
    notes = db.Column(db.Text)

    def __str__(self):
        return f"Budget {self.year}"


# ---------------------------------------------------------------------------
# Project templates (configured in Settings)
# ---------------------------------------------------------------------------
template_task_deps = db.Table(
    "template_task_deps",
    db.Column("task_id", db.Integer, db.ForeignKey("template_task.id", ondelete="CASCADE"), primary_key=True),
    db.Column("depends_on_id", db.Integer, db.ForeignKey("template_task.id", ondelete="CASCADE"), primary_key=True),
)


class ProjectTemplate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), unique=True, nullable=False)
    description = db.Column(db.Text)
    active = db.Column(db.Boolean, default=True, nullable=False)

    tasks = db.relationship(
        "TemplateTask",
        backref="template",
        cascade="all, delete-orphan",
        order_by="(TemplateTask.phase.desc(), TemplateTask.position)",
    )

    def __str__(self):
        return self.name


class TemplateTask(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(db.Integer, db.ForeignKey("project_template.id"), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    phase = db.Column(db.String(20), default="project", nullable=False)
    position = db.Column(db.Integer, default=0, nullable=False)
    duration_days = db.Column(db.Integer)
    description = db.Column(db.Text)

    depends_on = db.relationship(
        "TemplateTask",
        secondary=template_task_deps,
        primaryjoin=id == template_task_deps.c.task_id,
        secondaryjoin=id == template_task_deps.c.depends_on_id,
        order_by="TemplateTask.position",
    )

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------
project_task_deps = db.Table(
    "project_task_deps",
    db.Column("task_id", db.Integer, db.ForeignKey("project_task.id", ondelete="CASCADE"), primary_key=True),
    db.Column("depends_on_id", db.Integer, db.ForeignKey("project_task.id", ondelete="CASCADE"), primary_key=True),
)


class Project(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customer.id"), nullable=False)
    template_id = db.Column(db.Integer, db.ForeignKey("project_template.id"))
    name = db.Column(db.String(200), nullable=False)
    status = db.Column(db.String(20), default="pre_project", nullable=False)
    project_manager_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    start_date = db.Column(db.Date)
    target_go_live = db.Column(db.Date)
    go_live_date = db.Column(db.Date)
    controlling_project_ref = db.Column(db.String(120))
    sharepoint_url = db.Column(db.String(500))
    jira_url = db.Column(db.String(500))
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))

    template = db.relationship("ProjectTemplate")
    project_manager = db.relationship("User", foreign_keys=[project_manager_id])
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    tasks = db.relationship(
        "ProjectTask",
        backref="project",
        cascade="all, delete-orphan",
        order_by="(ProjectTask.phase.desc(), ProjectTask.position)",
    )
    contracts = db.relationship("Contract", backref="project", cascade="all, delete-orphan", order_by="Contract.signed_date")
    cmos = db.relationship("CMO", backref="project", cascade="all, delete-orphan", order_by="CMO.name")
    serials = db.relationship("SerialNumber", backref="project", cascade="all, delete-orphan", order_by="SerialNumber.serial")
    invoices = db.relationship(
        "Invoice", backref="project", cascade="all, delete-orphan", order_by="Invoice.issue_date",
        foreign_keys="Invoice.project_id",
    )
    notes = db.relationship("Note", backref="project", cascade="all, delete-orphan", order_by="Note.created_at.desc()")
    attachments = db.relationship(
        "Attachment", backref="project", cascade="all, delete-orphan", order_by="Attachment.uploaded_at.desc()"
    )

    @property
    def status_label(self):
        return label(PROJECT_STATUSES, self.status)

    def tasks_in(self, phase):
        return [t for t in self.tasks if t.phase == phase]

    @property
    def progress(self):
        relevant = [t for t in self.tasks if t.status != "na"]
        if not relevant:
            return 0
        return round(100 * sum(1 for t in relevant if t.status == "done") / len(relevant))

    @property
    def is_open(self):
        return self.status in OPEN_PROJECT_STATUSES

    # -- money ----------------------------------------------------------
    @property
    def contract_value(self):
        return sum((c.total_value or Decimal(0)) for c in self.contracts)

    @property
    def contracted_days(self):
        return sum((c.effort_man_days or Decimal(0)) for c in self.contracts)

    def _sum_invoices(self, kind):
        return sum(
            (i.amount or Decimal(0)) for i in self.invoices
            if i.kind == kind and i.status in ("issued", "sent", "paid")
        )

    @property
    def invoiced_amount(self):
        return self._sum_invoices("invoice")

    @property
    def credited_amount(self):
        return self._sum_invoices("credit_note")

    @property
    def net_invoiced(self):
        return self.invoiced_amount - self.credited_amount

    @property
    def paid_amount(self):
        return sum(
            (i.signed_amount or Decimal(0)) for i in self.invoices if i.status == "paid"
        )

    @property
    def remaining_to_invoice(self):
        return self.contract_value - self.net_invoiced

    @property
    def currency(self):
        for c in self.contracts:
            if c.currency:
                return c.currency
        return "EUR"

    def __str__(self):
        return self.name


class ProjectTask(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("project.id"), nullable=False)
    template_task_id = db.Column(db.Integer, db.ForeignKey("template_task.id", ondelete="SET NULL"))
    name = db.Column(db.String(200), nullable=False)
    phase = db.Column(db.String(20), default="project", nullable=False)
    position = db.Column(db.Integer, default=0, nullable=False)
    status = db.Column(db.String(20), default="todo", nullable=False)
    assignee_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    due_date = db.Column(db.Date)
    completed_date = db.Column(db.Date)
    notes = db.Column(db.Text)

    assignee = db.relationship("User")
    depends_on = db.relationship(
        "ProjectTask",
        secondary=project_task_deps,
        primaryjoin=id == project_task_deps.c.task_id,
        secondaryjoin=id == project_task_deps.c.depends_on_id,
        order_by="ProjectTask.position",
    )

    @property
    def status_label(self):
        return label(TASK_STATUSES, self.status)

    @property
    def is_finished(self):
        return self.status in FINISHED_TASK_STATUSES

    @property
    def waiting_on(self):
        return [d for d in self.depends_on if not d.is_finished]

    @property
    def is_ready(self):
        return not self.is_finished and not self.waiting_on

    @property
    def is_overdue(self):
        return bool(self.due_date and not self.is_finished and self.due_date < date.today())

    def __str__(self):
        return self.name


class Contract(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("project.id"), nullable=False)
    reference = db.Column(db.String(120))
    signed_date = db.Column(db.Date)
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    scope = db.Column(db.Text)
    effort_man_days = db.Column(db.Numeric(10, 2))
    man_day_rate = db.Column(db.Numeric(12, 2))
    currency = db.Column(db.String(3), default="EUR")
    fixed_price = db.Column(db.Numeric(12, 2))
    payment_terms = db.Column(db.String(200))
    sharepoint_url = db.Column(db.String(500))
    notes = db.Column(db.Text)

    @property
    def total_value(self):
        if self.fixed_price is not None:
            return self.fixed_price
        if self.effort_man_days is not None and self.man_day_rate is not None:
            return Decimal(self.effort_man_days) * Decimal(self.man_day_rate)
        return None

    def __str__(self):
        return self.reference or f"Contract #{self.id}"


class CMO(db.Model):
    """Contract manufacturing organisation / partner to onboard."""

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("project.id"), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    location = db.Column(db.String(200))
    identifier = db.Column(db.String(120))
    contact_name = db.Column(db.String(200))
    contact_email = db.Column(db.String(200))
    contact_phone = db.Column(db.String(60))
    int_status = db.Column(db.String(20), default="not_started")
    prod_status = db.Column(db.String(20), default="not_started")
    notes = db.Column(db.Text)

    def __str__(self):
        return self.name


class SerialNumber(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("project.id"), nullable=False)
    serial = db.Column(db.String(200), nullable=False)
    description = db.Column(db.String(300))
    environment = db.Column(db.String(10))
    issued_date = db.Column(db.Date)
    notes = db.Column(db.Text)

    def __str__(self):
        return self.serial


class Invoice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("project.id"), nullable=False)
    kind = db.Column(db.String(20), default="invoice", nullable=False)
    number = db.Column(db.String(80))
    description = db.Column(db.String(300))
    issue_date = db.Column(db.Date)
    due_date = db.Column(db.Date)
    paid_date = db.Column(db.Date)
    man_days = db.Column(db.Numeric(10, 2))
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    currency = db.Column(db.String(3), default="EUR")
    status = db.Column(db.String(20), default="planned", nullable=False)
    related_invoice_id = db.Column(db.Integer, db.ForeignKey("invoice.id", ondelete="SET NULL"))
    notes = db.Column(db.Text)

    related_invoice = db.relationship("Invoice", remote_side=[id], foreign_keys=[related_invoice_id])

    @property
    def kind_label(self):
        return label(INVOICE_KINDS, self.kind)

    @property
    def status_label(self):
        return label(INVOICE_STATUSES, self.status)

    @property
    def signed_amount(self):
        if self.amount is None:
            return None
        return -self.amount if self.kind == "credit_note" else self.amount

    @property
    def is_overdue(self):
        return bool(self.due_date and self.status in ("issued", "sent") and self.due_date < date.today())

    def __str__(self):
        prefix = "CN" if self.kind == "credit_note" else "INV"
        if self.number and self.number.upper().startswith(prefix):
            return self.number
        return f"{prefix} {self.number or '#' + str(self.id)}"


class Note(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("project.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=utcnow)
    body = db.Column(db.Text, nullable=False)

    user = db.relationship("User")

    def __str__(self):
        return (self.body or "")[:60]


class Attachment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("project.id"), nullable=False)
    category = db.Column(db.String(20), default="other")
    description = db.Column(db.String(300))
    original_name = db.Column(db.String(300), nullable=False)
    stored_name = db.Column(db.String(100), nullable=False, unique=True)
    size = db.Column(db.Integer)
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    uploaded_at = db.Column(db.DateTime, default=utcnow)

    uploaded_by = db.relationship("User")

    def __str__(self):
        return self.original_name


class AuditLog(db.Model):
    """Append-only history of every change made in the app."""

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=utcnow, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"))
    action = db.Column(db.String(10), nullable=False)  # create / update / delete
    entity = db.Column(db.String(50), nullable=False, index=True)
    entity_id = db.Column(db.Integer)
    project_id = db.Column(db.Integer, index=True)  # no FK: keep history after deletes
    customer_id = db.Column(db.Integer, index=True)
    summary = db.Column(db.String(300))
    changes = db.Column(db.Text)  # JSON {field: [old, new]}

    user = db.relationship("User")
