import os
import sqlite3
import tempfile
from datetime import datetime

from flask import (
    Blueprint, abort, after_this_request, current_app, flash, redirect, render_template, request, send_file, url_for,
)
from flask_login import current_user

from ..extensions import db
from ..forms import Field, apply_form
from ..models import TASK_PHASES, Project, ProjectTemplate, TemplateTask, User
from ..services import compute_steps, creates_cycle, renumber

bp = Blueprint("settings", __name__, url_prefix="/settings")

TEMPLATE_FIELDS = [
    Field("name", "Template name", required=True),
    Field("active", "Active (available for new projects)", "checkbox"),
    Field("description", "Description", "textarea"),
]

TEMPLATE_TASK_FIELDS = [
    Field("name", "Task", required=True),
    Field("phase", "Phase", "select", required=True, choices=TASK_PHASES),
    Field("duration_days", "Typical duration (days)", "int"),
    Field("description", "Description / instructions", "textarea",
          help="Copied into the task notes when a project is created."),
]

USER_FIELDS = [
    Field("username", "Username", required=True),
    Field("full_name", "Full name", required=True),
    Field("email", "Email", "email"),
    Field("is_admin", "Admin (can manage users, delete projects, download backups)", "checkbox"),
    Field("active", "Active (can log in)", "checkbox"),
]


def _require_admin():
    if not current_user.is_admin:
        abort(403)


@bp.get("/")
def index():
    return render_template(
        "settings/index.html",
        templates=ProjectTemplate.query.order_by(ProjectTemplate.name).all(),
        users=User.query.order_by(User.full_name).all(),
    )


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
@bp.route("/templates/new", methods=["GET", "POST"])
def template_new():
    tpl = ProjectTemplate(active=True)
    errors = []
    if request.method == "POST":
        errors = apply_form(tpl, TEMPLATE_FIELDS, request.form)
        if not errors and ProjectTemplate.query.filter_by(name=tpl.name).first():
            errors.append("A template with that name already exists.")
        if not errors:
            copy_from = request.form.get("copy_from", type=int)
            db.session.add(tpl)
            src = db.session.get(ProjectTemplate, copy_from) if copy_from else None
            if src:
                mapping = {}
                for st in src.tasks:
                    nt = TemplateTask(name=st.name, phase=st.phase, position=st.position,
                                      duration_days=st.duration_days, description=st.description)
                    tpl.tasks.append(nt)
                    mapping[st.id] = nt
                for st in src.tasks:
                    mapping[st.id].depends_on = [mapping[d.id] for d in st.depends_on]
            db.session.commit()
            flash("Template created.", "success")
            return redirect(url_for("settings.template_edit", id=tpl.id))
    return render_template(
        "settings/template_new.html", fields=TEMPLATE_FIELDS, obj=tpl, errors=errors,
        submitted=request.form if request.method == "POST" else None,
        templates=ProjectTemplate.query.order_by(ProjectTemplate.name).all(),
    )


@bp.route("/templates/<int:id>", methods=["GET", "POST"])
def template_edit(id):
    tpl = db.get_or_404(ProjectTemplate, id)
    errors = []
    if request.method == "POST":
        with db.session.no_autoflush:
            errors = apply_form(tpl, TEMPLATE_FIELDS, request.form)
            if not errors and ProjectTemplate.query.filter(
                ProjectTemplate.name == tpl.name, ProjectTemplate.id != id
            ).first():
                errors.append("A template with that name already exists.")
        if errors:
            db.session.rollback()
        else:
            db.session.commit()
            flash("Template saved.", "success")
            return redirect(url_for("settings.template_edit", id=id))
    in_use = Project.query.filter_by(template_id=id).count()
    return render_template(
        "settings/template_edit.html", tpl=tpl, fields=TEMPLATE_FIELDS, errors=errors,
        submitted=request.form if request.method == "POST" else None,
        steps=compute_steps(tpl.tasks), in_use=in_use,
    )


@bp.post("/templates/<int:id>/delete")
def template_delete(id):
    tpl = db.get_or_404(ProjectTemplate, id)
    if Project.query.filter_by(template_id=id).count():
        flash("Projects use this template. Mark it inactive instead.", "error")
        return redirect(url_for("settings.template_edit", id=id))
    db.session.delete(tpl)
    db.session.commit()
    flash("Template deleted.", "success")
    return redirect(url_for("settings.index"))


@bp.post("/templates/<int:id>/arrange/<mode>")
def template_arrange(id, mode):
    """Quickly set a phase to fully serial (each task waits on the one before)
    or fully parallel (no dependencies inside the phase)."""
    tpl = db.get_or_404(ProjectTemplate, id)
    phase = request.form.get("phase", "project")
    tasks = sorted([t for t in tpl.tasks if t.phase == phase], key=lambda t: t.position)
    ids = {t.id for t in tasks}
    for i, t in enumerate(tasks):
        outside = [d for d in t.depends_on if d.id not in ids]
        if mode == "serial":
            t.depends_on = outside + ([tasks[i - 1]] if i else [])
        elif mode == "parallel":
            t.depends_on = outside
        else:
            abort(400)
    db.session.commit()
    flash(f"{dict(TASK_PHASES)[phase]} tasks set to {'serial' if mode == 'serial' else 'parallel'}.", "success")
    return redirect(url_for("settings.template_edit", id=id) + "#tasks")


def _template_task_form(tpl, task, heading, delete_url=None):
    others = [o for o in tpl.tasks if o.id != task.id]
    errors = []
    if request.method == "POST":
        errors = apply_form(task, TEMPLATE_TASK_FIELDS, request.form)
        dep_ids = {int(x) for x in request.form.getlist("depends_on")}
        deps = [o for o in others if o.id in dep_ids]
        if not errors and task.id and creates_cycle(task, deps):
            errors.append("Those prerequisites would create a loop (a task waiting on itself).")
        if errors:
            db.session.rollback()
        else:
            task.depends_on = deps
            if not task.id:
                task.template_id = tpl.id
                task.position = max((o.position for o in others), default=0) + 1
                db.session.add(task)
            db.session.commit()
            flash("Template task saved.", "success")
            return redirect(url_for("settings.template_edit", id=tpl.id) + "#tasks")
    selected = (
        {int(x) for x in request.form.getlist("depends_on")} if request.method == "POST"
        else {d.id for d in task.depends_on}
    )
    return render_template(
        "projects/task_form.html", fields=TEMPLATE_TASK_FIELDS, obj=task, project=None, others=others,
        selected=selected, errors=errors, heading=heading,
        submitted=request.form if request.method == "POST" else None,
        cancel_url=url_for("settings.template_edit", id=tpl.id) + "#tasks", delete_url=delete_url,
        template_mode=True,
    )


@bp.route("/templates/<int:id>/tasks/new", methods=["GET", "POST"])
def template_task_new(id):
    tpl = db.get_or_404(ProjectTemplate, id)
    return _template_task_form(tpl, TemplateTask(phase=request.args.get("phase", "project")), "New template task")


@bp.route("/template-tasks/<int:id>", methods=["GET", "POST"])
def template_task_edit(id):
    t = db.get_or_404(TemplateTask, id)
    return _template_task_form(t.template, t, f"Edit template task: {t.name}",
                               url_for("settings.template_task_delete", id=id))


@bp.post("/template-tasks/<int:id>/delete")
def template_task_delete(id):
    t = db.get_or_404(TemplateTask, id)
    tpl_id = t.template_id
    for other in t.template.tasks:
        if t in other.depends_on:
            other.depends_on.remove(t)
    db.session.delete(t)
    db.session.commit()
    flash("Template task deleted. Existing projects keep their copy.", "success")
    return redirect(url_for("settings.template_edit", id=tpl_id) + "#tasks")


@bp.post("/template-tasks/<int:id>/move/<direction>")
def template_task_move(id, direction):
    t = db.get_or_404(TemplateTask, id)
    siblings = sorted([x for x in t.template.tasks if x.phase == t.phase], key=lambda x: x.position)
    i = siblings.index(t)
    j = i - 1 if direction == "up" else i + 1
    if 0 <= j < len(siblings):
        siblings[i].position, siblings[j].position = siblings[j].position, siblings[i].position
        db.session.commit()
    return redirect(url_for("settings.template_edit", id=t.template_id) + f"#tt-{t.id}")


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
def _user_form(user, heading):
    _require_admin()
    errors = []
    if request.method == "POST":
        with db.session.no_autoflush:
            errors = apply_form(user, USER_FIELDS, request.form)
            if not errors:
                user.username = user.username.lower()
                if User.query.filter(User.username == user.username, User.id != user.id).first():
                    errors.append("That username is taken.")
        password = request.form.get("password") or ""
        if not user.id and len(password) < 8:
            errors.append("Set a password of at least 8 characters.")
        elif password and len(password) < 8:
            errors.append("Passwords need at least 8 characters.")
        if user.id == current_user.id and (not user.is_admin or not user.active):
            errors.append("You cannot remove your own admin rights or deactivate yourself.")
        if errors:
            db.session.rollback()
        else:
            if password:
                user.set_password(password)
            db.session.add(user)
            db.session.commit()
            flash("User saved.", "success")
            return redirect(url_for("settings.index") + "#users")
    return render_template(
        "settings/user_form.html", fields=USER_FIELDS, obj=user, errors=errors, heading=heading,
        submitted=request.form if request.method == "POST" else None,
    )


@bp.route("/users/new", methods=["GET", "POST"])
def user_new():
    return _user_form(User(active=True), "New user")


@bp.route("/users/<int:id>", methods=["GET", "POST"])
def user_edit(id):
    _require_admin()
    u = db.get_or_404(User, id)
    return _user_form(u, f"Edit user {u.username}")


@bp.route("/password", methods=["GET", "POST"])
def password():
    if request.method == "POST":
        if not current_user.check_password(request.form.get("current", "")):
            flash("Your current password is wrong.", "error")
        elif len(request.form.get("new", "")) < 8:
            flash("The new password needs at least 8 characters.", "error")
        elif request.form.get("new") != request.form.get("confirm"):
            flash("The two new passwords do not match.", "error")
        else:
            current_user.set_password(request.form["new"])
            db.session.commit()
            flash("Password changed.", "success")
            return redirect(url_for("main.dashboard"))
    return render_template("settings/password.html")


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------
@bp.get("/backup")
def backup():
    _require_admin()
    uri = current_app.config["SQLALCHEMY_DATABASE_URI"]
    if not uri.startswith("sqlite:///"):
        abort(400)
    src_path = uri.replace("sqlite:///", "", 1)
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(tmp)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()

    @after_this_request
    def _cleanup(response):
        try:
            os.remove(tmp)
        except OSError:
            pass
        return response

    name = f"tracker-backup-{datetime.now():%Y%m%d-%H%M}.db"
    return send_file(tmp, as_attachment=True, download_name=name)
