import os
import uuid
from datetime import date

from flask import (
    Blueprint, abort, current_app, flash, redirect, render_template, request, send_from_directory, url_for,
)
from flask_login import current_user
from werkzeug.utils import secure_filename

from ..extensions import db
from ..forms import Field, apply_form
from ..models import (
    ATTACHMENT_CATEGORIES, OPEN_PROJECT_STATUSES, PROJECT_STATUSES, TASK_PHASES, TASK_STATUSES,
    Attachment, AuditLog, Customer, Note, Project, ProjectTask, ProjectTemplate,
)
from ..services import compute_steps, create_project_from_template, creates_cycle, renumber, user_choices

bp = Blueprint("projects", __name__, url_prefix="/projects")


def _customer_choices(ctx=None):
    return [(str(c.id), c.name) for c in Customer.query.order_by(Customer.name)]


def _template_choices(ctx=None):
    return [(str(t.id), t.name) for t in ProjectTemplate.query.filter_by(active=True).order_by(ProjectTemplate.name)]


DETAIL_FIELDS = [
    Field("customer_id", "Customer", "fk", required=True, choices=_customer_choices),
    Field("name", "Project name", required=True),
    Field("status", "Status", "select", required=True, choices=PROJECT_STATUSES),
    Field("project_manager_id", "Project manager", "fk", choices=user_choices),
    Field("start_date", "Start date", "date"),
    Field("target_go_live", "Target go-live", "date"),
    Field("go_live_date", "Actual go-live", "date"),
    Field("controlling_project_ref", "Controlling project ref.",
          help="Reference of the project created by the controlling team."),
    Field("sharepoint_url", "SharePoint folder", "url"),
    Field("jira_url", "Jira project", "url"),
    Field("description", "Description", "textarea"),
]

NEW_FIELDS = [
    Field("customer_id", "Customer", "fk", required=True, choices=_customer_choices),
    Field("template_id", "Product / template", "fk", required=True, choices=_template_choices,
          help="The template's tasks and dependencies are copied into the project. You can adjust them afterwards."),
    Field("name", "Project name", required=True),
    Field("project_manager_id", "Project manager", "fk", choices=user_choices),
    Field("start_date", "Start date", "date"),
    Field("target_go_live", "Target go-live", "date"),
    Field("description", "Description", "textarea"),
]

TASK_FIELDS = [
    Field("name", "Task", required=True),
    Field("phase", "Phase", "select", required=True, choices=TASK_PHASES),
    Field("status", "Status", "select", required=True, choices=TASK_STATUSES),
    Field("assignee_id", "Assignee", "fk", choices=user_choices),
    Field("due_date", "Due date", "date"),
    Field("completed_date", "Completed on", "date"),
    Field("notes", "Notes", "textarea"),
]


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------
@bp.get("/")
def index():
    status = request.args.get("status", "open")
    template_id = request.args.get("template_id", type=int)
    pm_id = request.args.get("pm_id", type=int)
    q = (request.args.get("q") or "").strip()
    query = Project.query.join(Customer)
    if status == "open":
        query = query.filter(Project.status.in_(OPEN_PROJECT_STATUSES))
    elif status and status != "all":
        query = query.filter(Project.status == status)
    if template_id:
        query = query.filter(Project.template_id == template_id)
    if pm_id:
        query = query.filter(Project.project_manager_id == pm_id)
    if q:
        query = query.filter(db.or_(Project.name.ilike(f"%{q}%"), Customer.name.ilike(f"%{q}%")))
    projects = query.order_by(Customer.name, Project.name).all()
    return render_template(
        "projects/index.html", projects=projects, status=status, template_id=template_id, pm_id=pm_id, q=q,
        templates=ProjectTemplate.query.order_by(ProjectTemplate.name).all(), users=user_choices(),
    )


@bp.route("/new", methods=["GET", "POST"])
def new():
    project = Project(customer_id=request.args.get("customer_id", type=int), project_manager_id=current_user.id)
    errors = []
    if request.method == "POST":
        errors = apply_form(project, NEW_FIELDS, request.form)
        if not errors:
            project.created_by_id = current_user.id
            project.template = db.session.get(ProjectTemplate, project.template_id)
            create_project_from_template(project)
            db.session.add(project)
            db.session.commit()
            flash("Project created with the template's tasks.", "success")
            return redirect(url_for("projects.view", id=project.id))
    return render_template(
        "simple_form.html", fields=NEW_FIELDS, obj=project, errors=errors, heading="New project",
        submitted=request.form if request.method == "POST" else None, cancel_url=url_for("projects.index"),
    )


@bp.get("/<int:id>")
def view(id):
    p = db.get_or_404(Project, id)
    steps = compute_steps(p.tasks)
    history = AuditLog.query.filter_by(project_id=id).order_by(AuditLog.created_at.desc()).limit(15).all()
    return render_template(
        "projects/view.html", p=p, steps=steps, history=history, users=user_choices(),
        categories=ATTACHMENT_CATEGORIES,
    )


@bp.route("/<int:id>/edit", methods=["GET", "POST"])
def edit(id):
    p = db.get_or_404(Project, id)
    errors = []
    if request.method == "POST":
        errors = apply_form(p, DETAIL_FIELDS, request.form)
        if not errors:
            db.session.commit()
            flash("Project saved.", "success")
            return redirect(url_for("projects.view", id=id))
    return render_template(
        "simple_form.html", fields=DETAIL_FIELDS, obj=p, errors=errors, heading=f"Edit {p.name}",
        submitted=request.form if request.method == "POST" else None,
        cancel_url=url_for("projects.view", id=id),
        delete_url=url_for("projects.delete", id=id) if current_user.is_admin else None,
        delete_warning="This deletes the project with all its tasks, contracts, invoices and files.",
    )


@bp.post("/<int:id>/delete")
def delete(id):
    if not current_user.is_admin:
        abort(403)
    p = db.get_or_404(Project, id)
    stored = [a.stored_name for a in p.attachments]
    db.session.delete(p)
    db.session.commit()
    for name in stored:
        _remove_file(name)
    flash("Project deleted. Its history is kept.", "success")
    return redirect(url_for("projects.index"))


@bp.get("/<int:id>/history")
def history(id):
    p = db.get_or_404(Project, id)
    page = request.args.get("page", 1, type=int)
    logs = AuditLog.query.filter_by(project_id=id).order_by(AuditLog.created_at.desc()).paginate(page=page, per_page=50)
    return render_template("main/history.html", logs=logs, project=p, filters={})


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------
def _apply_status(task, status):
    task.status = status
    if status == "done" and not task.completed_date:
        task.completed_date = date.today()
    if status != "done":
        task.completed_date = None


@bp.post("/tasks/<int:id>/status")
def task_status(id):
    t = db.get_or_404(ProjectTask, id)
    status = request.form.get("status")
    if status not in dict(TASK_STATUSES):
        abort(400)
    if status in ("in_progress", "done") and t.waiting_on:
        flash(f"Note: '{t.name}' still waits on {', '.join(d.name for d in t.waiting_on)}.", "warning")
    _apply_status(t, status)
    if "assignee_id" in request.form:
        aid = request.form.get("assignee_id")
        t.assignee_id = int(aid) if aid else None
    db.session.commit()
    return redirect(url_for("projects.view", id=t.project_id) + f"#task-{t.id}")


def _task_form(project, task, heading, delete_url=None):
    others = [o for o in project.tasks if o.id != task.id]
    errors = []
    if request.method == "POST":
        old_status, old_completed = task.status, task.completed_date
        errors = apply_form(task, TASK_FIELDS, request.form)
        dep_ids = {int(x) for x in request.form.getlist("depends_on")}
        deps = [o for o in others if o.id in dep_ids]
        if not errors and task.id and creates_cycle(task, deps):
            errors.append("Those prerequisites would create a loop (a task waiting on itself).")
        if errors:
            db.session.rollback()
        else:
            if task.status != old_status and task.completed_date == old_completed:
                _apply_status(task, task.status)
            task.depends_on = deps
            if not task.id:
                task.project_id = project.id
                task.position = max((o.position for o in others), default=0) + 1
                db.session.add(task)
            db.session.commit()
            flash("Task saved.", "success")
            return redirect(url_for("projects.view", id=project.id) + f"#task-{task.id}")
    selected = (
        {int(x) for x in request.form.getlist("depends_on")} if request.method == "POST"
        else {d.id for d in task.depends_on}
    )
    return render_template(
        "projects/task_form.html", fields=TASK_FIELDS, obj=task, project=project, others=others,
        selected=selected, errors=errors, heading=heading,
        submitted=request.form if request.method == "POST" else None,
        cancel_url=url_for("projects.view", id=project.id) + "#tasks", delete_url=delete_url,
    )


@bp.route("/<int:id>/tasks/new", methods=["GET", "POST"])
def task_new(id):
    p = db.get_or_404(Project, id)
    return _task_form(p, ProjectTask(phase=request.args.get("phase", "project"), status="todo"), "New task")


@bp.route("/tasks/<int:id>/edit", methods=["GET", "POST"])
def task_edit(id):
    t = db.get_or_404(ProjectTask, id)
    return _task_form(t.project, t, f"Edit task: {t.name}", url_for("projects.task_delete", id=id))


@bp.post("/tasks/<int:id>/delete")
def task_delete(id):
    t = db.get_or_404(ProjectTask, id)
    pid = t.project_id
    for other in t.project.tasks:
        if t in other.depends_on:
            other.depends_on.remove(t)
    db.session.delete(t)
    db.session.commit()
    flash("Task deleted.", "success")
    return redirect(url_for("projects.view", id=pid) + "#tasks")


@bp.post("/tasks/<int:id>/move/<direction>")
def task_move(id, direction):
    t = db.get_or_404(ProjectTask, id)
    siblings = sorted(t.project.tasks_in(t.phase), key=lambda x: x.position)
    i = siblings.index(t)
    j = i - 1 if direction == "up" else i + 1
    if 0 <= j < len(siblings):
        siblings[i].position, siblings[j].position = siblings[j].position, siblings[i].position
        db.session.commit()
    return redirect(url_for("projects.view", id=t.project_id) + f"#task-{t.id}")


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------
@bp.post("/<int:id>/notes")
def note_add(id):
    p = db.get_or_404(Project, id)
    body = (request.form.get("body") or "").strip()
    if body:
        db.session.add(Note(project_id=p.id, user_id=current_user.id, body=body))
        db.session.commit()
    return redirect(url_for("projects.view", id=id) + "#notes")


@bp.post("/notes/<int:id>/delete")
def note_delete(id):
    n = db.get_or_404(Note, id)
    if n.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    pid = n.project_id
    db.session.delete(n)
    db.session.commit()
    return redirect(url_for("projects.view", id=pid) + "#notes")


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------
def _remove_file(stored_name):
    path = os.path.join(current_app.config["UPLOAD_DIR"], stored_name)
    if os.path.exists(path):
        os.remove(path)


@bp.post("/<int:id>/files")
def file_upload(id):
    p = db.get_or_404(Project, id)
    f = request.files.get("file")
    if not f or not f.filename:
        flash("Choose a file to upload.", "error")
        return redirect(url_for("projects.view", id=id) + "#files")
    original = secure_filename(f.filename) or "file"
    ext = os.path.splitext(original)[1][:10]
    stored = f"{uuid.uuid4().hex}{ext}"
    path = os.path.join(current_app.config["UPLOAD_DIR"], stored)
    f.save(path)
    category = request.form.get("category")
    db.session.add(Attachment(
        project_id=p.id, original_name=original, stored_name=stored, size=os.path.getsize(path),
        category=category if category in dict(ATTACHMENT_CATEGORIES) else "other",
        description=(request.form.get("description") or "").strip() or None,
        uploaded_by_id=current_user.id,
    ))
    db.session.commit()
    flash("File uploaded.", "success")
    return redirect(url_for("projects.view", id=id) + "#files")


@bp.get("/files/<int:id>")
def file_download(id):
    a = db.get_or_404(Attachment, id)
    return send_from_directory(
        current_app.config["UPLOAD_DIR"], a.stored_name, as_attachment=True, download_name=a.original_name,
    )


@bp.post("/files/<int:id>/delete")
def file_delete(id):
    a = db.get_or_404(Attachment, id)
    pid, stored = a.project_id, a.stored_name
    db.session.delete(a)
    db.session.commit()
    _remove_file(stored)
    flash("File deleted.", "success")
    return redirect(url_for("projects.view", id=pid) + "#files")
