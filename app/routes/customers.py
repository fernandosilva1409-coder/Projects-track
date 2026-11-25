from flask import Blueprint, flash, redirect, render_template, request, url_for

from ..extensions import db
from ..forms import Field, apply_form
from ..models import AuditLog, Customer, Project

bp = Blueprint("customers", __name__, url_prefix="/customers")

FIELDS = [
    Field("name", "Customer name", required=True),
    Field("vat_number", "VAT number"),
    Field("country", "Country"),
    Field("website", "Website", "url"),
    Field("address", "Address (invoicing)", "textarea"),
    Field("notes", "Notes", "textarea"),
]


@bp.get("/")
def index():
    q = (request.args.get("q") or "").strip()
    query = Customer.query
    if q:
        query = query.filter(Customer.name.ilike(f"%{q}%"))
    return render_template("customers/index.html", customers=query.order_by(Customer.name).all(), q=q)


def _form(customer, heading, delete_url=None):
    errors = []
    if request.method == "POST":
        errors = apply_form(customer, FIELDS, request.form)
        if not errors:
            with db.session.no_autoflush:
                dup = Customer.query.filter(Customer.name == customer.name, Customer.id != customer.id).first()
            if dup:
                errors.append("A customer with that name already exists.")
                db.session.rollback()
        if not errors:
            db.session.add(customer)
            db.session.commit()
            flash("Customer saved.", "success")
            return redirect(url_for("customers.view", id=customer.id))
    return render_template(
        "simple_form.html", fields=FIELDS, obj=customer, errors=errors, heading=heading,
        submitted=request.form if request.method == "POST" else None,
        cancel_url=url_for("customers.view", id=customer.id) if customer.id else url_for("customers.index"),
        delete_url=delete_url,
    )


@bp.route("/new", methods=["GET", "POST"])
def new():
    return _form(Customer(), "New customer")


@bp.route("/<int:id>/edit", methods=["GET", "POST"])
def edit(id):
    c = db.get_or_404(Customer, id)
    return _form(c, f"Edit {c.name}", url_for("customers.delete", id=id))


@bp.get("/<int:id>")
def view(id):
    c = db.get_or_404(Customer, id)
    project_ids = [p.id for p in c.projects]
    history = (
        AuditLog.query.filter(db.or_(AuditLog.customer_id == id, AuditLog.project_id.in_(project_ids or [-1])))
        .order_by(AuditLog.created_at.desc()).limit(30).all()
    )
    return render_template("customers/view.html", customer=c, history=history)


@bp.post("/<int:id>/delete")
def delete(id):
    c = db.get_or_404(Customer, id)
    if Project.query.filter_by(customer_id=id).count():
        flash("This customer still has projects. Delete or move them first.", "error")
        return redirect(url_for("customers.view", id=id))
    db.session.delete(c)
    db.session.commit()
    flash("Customer deleted. It stays visible in the history.", "success")
    return redirect(url_for("customers.index"))
