"""Default project templates. Used once, when the database has no templates.

Each task is (name, phase, [names of tasks it depends on]). Tasks with no
dependency on each other can run in parallel. Everything can be changed later
in Settings > Project templates.
"""
from .extensions import db
from .models import ProjectTemplate, TemplateTask

PRE_PROJECT = [
    ("Contract signed and details uploaded", "pre", []),
    ("Create project with controlling team", "pre", ["Contract signed and details uploaded"]),
    ("Upload contract to SharePoint", "pre", ["Contract signed and details uploaded"]),
]

CSDB = PRE_PROJECT + [
    ("Create CATS projects", "project", ["Create project with controlling team"]),
    ("Kick off", "project", ["Create CATS projects"]),
    ("IP whitelist for INT and PROD environment", "project", ["Kick off"]),
    ("Handover project documentation", "project", ["Kick off"]),
    ("Create INT environment with IQ plan", "project", ["Kick off"]),
    ("Create Jira account for organization and users", "project", ["Kick off"]),
    ("Training session", "project", ["Create INT environment with IQ plan", "Create Jira account for organization and users"]),
    ("Master data config (INT)", "project", ["Training session"]),
    ("Partner onboarding INT", "project", ["Master data config (INT)", "IP whitelist for INT and PROD environment"]),
    ("EU Hub onboarding INT", "project", ["Master data config (INT)", "IP whitelist for INT and PROD environment"]),
    ("Partner qualification", "project", ["Partner onboarding INT"]),
    ("EU qualification", "project", ["EU Hub onboarding INT"]),
    ("PROD environment - Go Live", "project", ["Partner qualification", "EU qualification"]),
    ("Master data config (PROD)", "project", ["PROD environment - Go Live"]),
    ("Configure partners and EU onboarding (PROD)", "project", ["Master data config (PROD)"]),
    ("Jira and project finalization handover", "project", ["Configure partners and EU onboarding (PROD)"]),
]

PLATBRICKS = PRE_PROJECT + [
    ("Create CATS projects", "project", ["Create project with controlling team"]),
    ("Kick off", "project", ["Create CATS projects"]),
    ("Create INT environment", "project", ["Kick off"]),
    ("Handover project documentation", "project", ["Kick off"]),
    ("Create Jira account for organization and users", "project", ["Kick off"]),
    ("Training session", "project", ["Create INT environment", "Create Jira account for organization and users"]),
    ("Master data config (INT)", "project", ["Training session"]),
    ("NMVS onboarding (INT)", "project", ["Master data config (INT)"]),
    ("Qualification", "project", ["NMVS onboarding (INT)"]),
    ("PROD environment - Go Live", "project", ["Qualification"]),
    ("Master data config (PROD)", "project", ["PROD environment - Go Live"]),
    ("NMVS PROD onboarding", "project", ["Master data config (PROD)"]),
    ("Jira and project finalization handover", "project", ["NMVS PROD onboarding"]),
]

DEFAULT_TEMPLATES = {
    "CSDB Multiclient & Kubernetes": ("CSDB Multiclient & Kubernetes implementation.", CSDB),
    "Platbricks Health Care Suite": ("Platbricks Health Care Suite implementation (NMVS).", PLATBRICKS),
}


def seed_templates():
    if ProjectTemplate.query.count():
        return False
    for name, (description, tasks) in DEFAULT_TEMPLATES.items():
        tpl = ProjectTemplate(name=name, description=description)
        db.session.add(tpl)
        by_name = {}
        for pos, (task_name, phase, _deps) in enumerate(tasks, start=1):
            t = TemplateTask(name=task_name, phase=phase, position=pos)
            tpl.tasks.append(t)
            by_name[task_name] = t
        for task_name, _phase, deps in tasks:
            by_name[task_name].depends_on = [by_name[d] for d in deps]
    db.session.commit()
    return True
