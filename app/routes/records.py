"""Generic add / edit / delete for the simple records hanging off a customer
or a project (contacts, contracts, CMOs, serial numbers, invoices)."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from ..extensions import db
from ..forms import Field
from ..forms import apply_form
from ..models import (
    CMO, CURRENCIES, ENVIRONMENTS, INVOICE_KINDS, INVOICE_STATUSES, ONBOARDING_STATUSES,
    Contact, Contract, Customer, CustomerBudget, Invoice, Project, SerialNumber,
)

bp = Blueprint("records", __name__)


def _related_invoice_choices(project):
    if project is None:
        return []
    return [(str(i.id), f"{i} ({i.issue_date or 'no date'}, {i.amount} {i.currency})")
            for i in project.invoices if i.kind == "invoice"]


def _project_currency(project):
    return {"currency": project.currency}


def _validate_budget(obj, parent):
    if obj.year is not None and not 2000 <= obj.year <= 2100:
        return ["Year must be between 2000 and 2100."]
    with db.session.no_autoflush:
        dup = CustomerBudget.query.filter(
            CustomerBudget.customer_id == parent.id, CustomerBudget.year == obj.year,
            CustomerBudget.currency == obj.currency, CustomerBudget.id != obj.id,
        ).first()
    return [f"There is already a {obj.year} budget in {obj.currency} for this customer. Edit that one."] if dup else []


def _budget_defaults(customer):
    from datetime import date

    return {"year": date.today().year, "currency": "EUR"}


ENTITIES = {
    "budgets": dict(
        model=CustomerBudget, parent="customer", title="Yearly budget", defaults=_budget_defaults,
        validate=_validate_budget, fields=[
            Field("year", "Year", "int", required=True),
            Field("amount", "Budget amount (excl. VAT)", "decimal", required=True),
            Field("currency", "Currency", "select", required=True, choices=CURRENCIES),
            Field("man_days", "Budgeted man-days", "decimal"),
            Field("notes", "Notes", "textarea", help="e.g. PO number, what the budget covers"),
        ]),
    "contacts": dict(
        model=Contact, parent="customer", title="Contact", fields=[
            Field("name", "Name", required=True),
            Field("role", "Role / function"),
            Field("email", "Email", "email"),
            Field("phone", "Phone"),
            Field("is_invoice_contact", "Invoice contact", "checkbox",
                  help="Tick if invoices and credit notes go to this person."),
            Field("notes", "Notes", "textarea"),
        ]),
    "contracts": dict(
        model=Contract, parent="project", title="Contract", fields=[
            Field("reference", "Contract / PO reference"),
            Field("signed_date", "Signed on", "date"),
            Field("start_date", "Start date", "date"),
            Field("end_date", "End date", "date"),
            Field("effort_man_days", "Effort (man-days)", "decimal"),
            Field("man_day_rate", "Man-day rate", "decimal"),
            Field("currency", "Currency", "select", choices=CURRENCIES),
            Field("fixed_price", "Fixed price", "decimal",
                  help="Leave empty for time & material: value = effort x rate."),
            Field("payment_terms", "Payment terms", help="e.g. 30 days, 50% at kick-off / 50% at go-live"),
            Field("sharepoint_url", "SharePoint link to the contract", "url", wide=True),
            Field("scope", "Scope", "textarea"),
            Field("notes", "Notes", "textarea"),
        ]),
    "cmos": dict(
        model=CMO, parent="project", title="CMO / partner", fields=[
            Field("name", "Name", required=True),
            Field("location", "Site / country"),
            Field("identifier", "Identifier", help="GLN, company or partner ID"),
            Field("contact_name", "Contact name"),
            Field("contact_email", "Contact email", "email"),
            Field("contact_phone", "Contact phone"),
            Field("int_status", "INT onboarding", "select", choices=ONBOARDING_STATUSES),
            Field("prod_status", "PROD onboarding", "select", choices=ONBOARDING_STATUSES),
            Field("notes", "Notes", "textarea"),
        ]),
    "serials": dict(
        model=SerialNumber, parent="project", title="Serial number", fields=[
            Field("serial", "Serial number", required=True),
            Field("description", "Description / product"),
            Field("environment", "Environment", "select", choices=ENVIRONMENTS),
            Field("issued_date", "Issued on", "date"),
            Field("notes", "Notes", "textarea"),
        ]),
    "invoices": dict(
        model=Invoice, parent="project", title="Invoice / credit note", defaults=_project_currency, fields=[
            Field("kind", "Type", "select", required=True, choices=INVOICE_KINDS),
            Field("status", "Status", "select", required=True, choices=INVOICE_STATUSES),
            Field("number", "Number"),
            Field("description", "Description", help="e.g. Milestone 1 - kick-off, or reason for credit note"),
            Field("amount", "Amount (excl. VAT)", "decimal", required=True,
                  help="Always positive. Credit notes are subtracted automatically."),
            Field("currency", "Currency", "select", choices=CURRENCIES),
            Field("man_days", "Man-days covered", "decimal"),
            Field("issue_date", "Issue date (or planned)", "date"),
            Field("due_date", "Due date", "date"),
            Field("paid_date", "Paid on", "date"),
            Field("related_invoice_id", "Credit note for invoice", "fk", choices=_related_invoice_choices,
                  help="Only for credit notes."),
            Field("notes", "Notes", "textarea"),
        ]),
}

PARENTS = {"customer": (Customer, "customers.view"), "project": (Project, "projects.view")}


def _entity(kind):
    ent = ENTITIES.get(kind)
    if not ent:
        abort(404)
    return ent


def _parent_url(ent, obj_or_parent_id, kind):
    _model, endpoint = PARENTS[ent["parent"]]
    return url_for(endpoint, id=obj_or_parent_id) + f"#{kind}"


@bp.route("/<parent>/<int:pid>/<kind>/new", methods=["GET", "POST"])
def new(parent, pid, kind):
    ent = _entity(kind)
    if ent["parent"] != parent:
        abort(404)
    parent_obj = db.get_or_404(PARENTS[parent][0], pid)
    obj = ent["model"]()
    for k, v in (ent.get("defaults", lambda p: {})(parent_obj)).items():
        setattr(obj, k, v)
    errors = []
    if request.method == "POST":
        errors = apply_form(obj, ent["fields"], request.form, parent_obj)
        if not errors and ent.get("validate"):
            errors = ent["validate"](obj, parent_obj)
        if not errors:
            setattr(obj, f"{parent}_id", pid)
            db.session.add(obj)
            db.session.commit()
            flash(f"{ent['title']} added.", "success")
            return redirect(_parent_url(ent, pid, kind))
    return render_template(
        "record_form.html", ent=ent, obj=obj, parent_obj=parent_obj, errors=errors,
        submitted=request.form if request.method == "POST" else None,
        cancel_url=_parent_url(ent, pid, kind), heading=f"New {ent['title'].lower()}",
    )


@bp.route("/<kind>/<int:id>/edit", methods=["GET", "POST"])
def edit(kind, id):
    ent = _entity(kind)
    obj = db.get_or_404(ent["model"], id)
    pid = getattr(obj, f"{ent['parent']}_id")
    parent_obj = db.session.get(PARENTS[ent["parent"]][0], pid)
    errors = []
    if request.method == "POST":
        errors = apply_form(obj, ent["fields"], request.form, parent_obj)
        if not errors and ent.get("validate"):
            errors = ent["validate"](obj, parent_obj)
            if errors:
                db.session.rollback()
        if not errors:
            db.session.commit()
            flash(f"{ent['title']} saved.", "success")
            return redirect(_parent_url(ent, pid, kind))
    return render_template(
        "record_form.html", ent=ent, obj=obj, parent_obj=parent_obj, errors=errors,
        submitted=request.form if request.method == "POST" else None,
        cancel_url=_parent_url(ent, pid, kind), heading=f"Edit {ent['title'].lower()}",
        delete_url=url_for("records.delete", kind=kind, id=id),
    )


@bp.post("/<kind>/<int:id>/delete")
def delete(kind, id):
    ent = _entity(kind)
    obj = db.get_or_404(ent["model"], id)
    pid = getattr(obj, f"{ent['parent']}_id")
    db.session.delete(obj)
    db.session.commit()
    flash(f"{ent['title']} deleted. It stays visible in the history.", "success")
    return redirect(_parent_url(ent, pid, kind))
