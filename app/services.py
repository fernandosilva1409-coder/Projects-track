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
