import io
from datetime import date

from app.extensions import db
from app.models import AuditLog, Invoice, Project, ProjectTask, ProjectTemplate
from tests.conftest import new_id


def test_login_required(app):
    c = app.test_client()
    assert c.get("/").status_code == 302
    assert c.post("/login", data={"username": "fernando", "password": "wrong"}).status_code == 200
    assert c.get("/healthz").data == b"ok"


def _create_project(client, app, template_name="CSDB Multiclient & Kubernetes"):
    r = client.post("/customers/new", data={"name": "Acme Pharma", "country": "Portugal", "cats_number": "CATS-4711"})
    cid = new_id(r)
    with app.app_context():
        tid = ProjectTemplate.query.filter_by(name=template_name).one().id
    r = client.post("/projects/new", data={"customer_id": cid, "template_id": tid, "name": "Acme CSDB",
                                            "start_date": "2026-12-01"})
    assert r.status_code == 302, r.data
    return cid, new_id(r)


def test_full_flow(client, app):
    cid, pid = _create_project(client, app)
    with app.app_context():
        p = db.session.get(Project, pid)
        assert len(p.tasks) == 19  # 3 pre-project + 16 PM tasks
        kickoff = next(t for t in p.tasks if t.name == "Kick off")
        assert [d.name for d in kickoff.depends_on] == ["Create CATS projects"]
        first = next(t for t in p.tasks if t.name == "Contract signed and details uploaded")
        assert first.is_ready and not kickoff.is_ready
        first_id, kickoff_id = first.id, kickoff.id

    assert b"CATS-4711" in client.get(f"/customers/{cid}").data
    assert b"CATS-4711" in client.get(f"/projects/{pid}").data

    # every page renders
    for url in ["/", "/customers/", f"/customers/{cid}", "/projects/", f"/projects/{pid}", "/invoices",
                "/history", "/analytics", "/settings/templates", "/settings/users", f"/projects/{pid}/history", "/invoices.csv", "/settings/password",
                f"/projects/tasks/{kickoff_id}/edit", f"/projects/{pid}/tasks/new", "/settings/users/new",
                "/settings/templates/new"]:
        assert client.get(url).status_code == 200, url

    # task status change records completion and history
    client.post(f"/projects/tasks/{first_id}/status", data={"status": "done"})
    with app.app_context():
        t = db.session.get(ProjectTask, first_id)
        assert t.status == "done" and t.completed_date == date.today()
        assert AuditLog.query.filter_by(entity="ProjectTask", action="update", entity_id=first_id).count() == 1

    # contacts, contract, cmo, serial, invoice, credit note
    assert client.post(f"/customer/{cid}/contacts/new", data={"name": "Ana", "email": "ana@example.com",
                                                             "is_invoice_contact": "on"}).status_code == 302
    assert client.post(f"/project/{pid}/contracts/new", data={"reference": "C-1", "effort_man_days": "20",
                                                             "man_day_rate": "1000,50", "currency": "EUR"}).status_code == 302
    assert client.post(f"/project/{pid}/cmos/new", data={"name": "CMO A", "int_status": "in_progress",
                                                        "prod_status": "not_started"}).status_code == 302
    assert client.post(f"/project/{pid}/serials/new", data={"serial": "SN-1", "environment": "PROD"}).status_code == 302
    assert client.post(f"/project/{pid}/invoices/new", data={"kind": "invoice", "status": "issued", "number": "1",
                                                            "amount": "5000", "currency": "EUR"}).status_code == 302
    with app.app_context():
        inv_id = Invoice.query.filter_by(number="1").one().id
    assert client.post(f"/project/{pid}/invoices/new", data={"kind": "credit_note", "status": "issued",
                                                            "number": "CN1", "amount": "500", "currency": "EUR",
                                                            "related_invoice_id": str(inv_id)}).status_code == 302
    # invalid input is rejected and nothing saved
    r = client.post(f"/project/{pid}/invoices/new", data={"kind": "invoice", "status": "issued", "amount": "abc"})
    assert r.status_code == 200 and b"not valid" in r.data

    with app.app_context():
        p = db.session.get(Project, pid)
        assert p.contract_value == 20010
        assert p.net_invoiced == 4500
        assert p.remaining_to_invoice == 15510
    assert client.get(f"/projects/{pid}").status_code == 200
    assert b"CN1" in client.get("/invoices.csv").data

    # file upload / download
    r = client.post(f"/projects/{pid}/files", data={"file": (io.BytesIO(b"pdf"), "contract.pdf"),
                                                    "category": "contract"}, content_type="multipart/form-data")
    assert r.status_code == 302
    page = client.get(f"/projects/{pid}").data
    assert b"contract.pdf" in page

    # notes
    client.post(f"/projects/{pid}/notes", data={"body": "Kick-off planned"})
    assert b"Kick-off planned" in client.get(f"/projects/{pid}").data

    # history page shows changes
    assert b"man day rate" in client.get(f"/projects/{pid}/history").data


def test_dependency_cycle_rejected(client, app):
    _cid, pid = _create_project(client, app, "Platbricks Health Care Suite")
    with app.app_context():
        p = db.session.get(Project, pid)
        cats = next(t for t in p.tasks if t.name == "Create CATS projects")
        kickoff = next(t for t in p.tasks if t.name == "Kick off")
        cats_id, kickoff_id = cats.id, kickoff.id
    r = client.post(f"/projects/tasks/{cats_id}/edit", data={"name": "Create CATS projects", "phase": "project",
                                                            "status": "todo", "depends_on": [str(kickoff_id)]})
    assert r.status_code == 200 and b"loop" in r.data


def test_template_serial_parallel(client, app):
    with app.app_context():
        tpl = ProjectTemplate.query.filter_by(name="Platbricks Health Care Suite").one()
        tid = tpl.id
    client.post(f"/settings/templates/{tid}/arrange/parallel", data={"phase": "project"})
    with app.app_context():
        tpl = db.session.get(ProjectTemplate, tid)
        kickoff = next(t for t in tpl.tasks if t.name == "Kick off")
        assert kickoff.depends_on == []
        cats = next(t for t in tpl.tasks if t.name == "Create CATS projects")
        assert [d.name for d in cats.depends_on] == ["Create project with controlling team"]  # cross-phase kept
    client.post(f"/settings/templates/{tid}/arrange/serial", data={"phase": "project"})
    with app.app_context():
        tpl = db.session.get(ProjectTemplate, tid)
        kickoff = next(t for t in tpl.tasks if t.name == "Kick off")
        assert [d.name for d in kickoff.depends_on] == ["Create CATS projects"]
    assert client.get(f"/settings/templates/{tid}").status_code == 200


def test_user_management(client, app):
    r = client.post("/settings/users/new", data={"username": "Antonio", "full_name": "Antonio", "active": "on",
                                                 "password": "password1"})
    assert r.status_code == 302
    c2 = app.test_client()
    assert c2.post("/login", data={"username": "antonio", "password": "password1"}).status_code == 302
    assert c2.get("/settings/users/new").status_code == 403
    assert c2.get("/settings/backup").status_code == 403
    assert client.get("/settings/backup").status_code == 200
    assert client.get("/settings/").status_code == 302


def test_budgets(client, app):
    cid, pid = _create_project(client, app)
    assert client.post(f"/customer/{cid}/budgets/new", data={"year": "2026", "amount": "100000",
                                                            "currency": "EUR"}).status_code == 302
    r = client.post(f"/customer/{cid}/budgets/new", data={"year": "2026", "amount": "5", "currency": "EUR"})
    assert r.status_code == 200 and b"already" in r.data
    client.post(f"/project/{pid}/contracts/new", data={"signed_date": "2026-03-01", "fixed_price": "60000",
                                                      "currency": "EUR"})
    client.post(f"/project/{pid}/invoices/new", data={"kind": "invoice", "status": "paid", "amount": "30000",
                                                     "currency": "EUR", "issue_date": "2026-04-01"})
    client.post(f"/project/{pid}/invoices/new", data={"kind": "credit_note", "status": "issued", "amount": "1000",
                                                     "currency": "EUR", "issue_date": "2026-05-01"})
    client.post(f"/project/{pid}/invoices/new", data={"kind": "invoice", "status": "planned", "amount": "20000",
                                                     "currency": "EUR", "issue_date": "2026-12-01"})
    with app.app_context():
        from app.services import budget_overview
        rows, totals = budget_overview(2026)
        r = rows[0]
        assert r["budget"] == 100000 and r["contracted"] == 60000
        assert r["invoiced"] == 29000 and r["paid"] == 30000 and r["planned"] == 20000
        assert r["remaining"] == 51000 and r["pct_invoiced"] == 29
    page = client.get("/analytics?year=2026").data
    assert b"budget vs actual" in page and b"51" in page
    assert client.get(f"/customers/{cid}").status_code == 200


def test_dependencies_block_and_status_sync(client, app):
    _cid, pid = _create_project(client, app, "Platbricks Health Care Suite")
    with app.app_context():
        p = db.session.get(Project, pid)
        pre = sorted(p.tasks_in("pre"), key=lambda t: t.position)
        pre_ids = [t.id for t in pre]
        kickoff_id = next(t.id for t in p.tasks if t.name == "Kick off")
    # cannot complete a task whose prerequisites are open
    client.post(f"/projects/tasks/{pre_ids[1]}/status", data={"status": "done"})
    r = client.post(f"/projects/tasks/{kickoff_id}/edit", data={"name": "Kick off", "phase": "project",
                                                               "status": "done", "depends_on": []})
    with app.app_context():
        assert db.session.get(ProjectTask, pre_ids[1]).status == "todo"
    # but editing deps away in the same save is allowed
    assert r.status_code == 302
    # finishing the pre-project checklist moves the project to active
    for tid in pre_ids:
        client.post(f"/projects/tasks/{tid}/status", data={"status": "done"})
    with app.app_context():
        p = db.session.get(Project, pid)
        assert all(t.status == "done" for t in p.tasks_in("pre"))
        assert p.status == "active"


def test_bulk_assign(client, app):
    _cid, pid = _create_project(client, app)
    with app.app_context():
        from app.models import User
        uid = User.query.first().id
        ids = [t.id for t in db.session.get(Project, pid).tasks][:4]
    r = client.post(f"/projects/{pid}/tasks/bulk", data={"task_ids": [str(i) for i in ids],
                                                         "bulk_assignee_id": str(uid), "bulk_due_date": "2027-01-15"})
    assert r.status_code == 302
    with app.app_context():
        for i in ids:
            t = db.session.get(ProjectTask, i)
            assert t.assignee_id == uid and t.due_date == date(2027, 1, 15)
    client.post(f"/projects/{pid}/tasks/bulk", data={"task_ids": [str(ids[0])], "bulk_assignee_id": "none"})
    with app.app_context():
        assert db.session.get(ProjectTask, ids[0]).assignee_id is None


def test_my_work_shows_only_mine_and_unassigned(client, app):
    _cid, pid = _create_project(client, app)
    client.post("/settings/users/new", data={"username": "antonio", "full_name": "Antonio", "active": "on",
                                             "password": "password1"})
    with app.app_context():
        from app.models import User
        me = User.query.filter_by(username="fernando").one()
        other = User.query.filter_by(username="antonio").one()
        p = db.session.get(Project, pid)
        p.project_manager_id = other.id
        tasks = sorted(p.tasks, key=lambda t: t.position)
        tasks[0].assignee_id = other.id   # Antonio's task
        tasks[1].assignee_id = me.id      # mine
        names = (tasks[0].name, tasks[1].name, tasks[2].name)
        me_id = me.id
        db.session.commit()
    page = client.get("/").data.decode()
    assert names[1] in page and names[2] in page  # mine and unassigned
    assert names[0] not in page                   # Antonio's
    # a project where I'm not PM and have no tasks does not appear
    with app.app_context():
        t = db.session.get(Project, pid).tasks
        for x in t:
            if x.assignee_id == me_id:
                x.assignee_id = None
        db.session.commit()
    assert "Acme CSDB" not in client.get("/").data.decode()


def test_add_customers_command(app):
    runner = app.test_cli_runner()
    names = ["Puleo", "Petrone - Euromed Pharma Services"]
    r = runner.invoke(args=["add-customers", *names, "--open-last", "1", "--pm", "fernando"])
    assert r.exit_code == 0, r.output
    r2 = runner.invoke(args=["add-customers", *names, "--open-last", "1"])
    assert "skipped" in r2.output
    with app.app_context():
        from app.models import Customer
        for n in names:
            c = Customer.query.filter_by(name=n).one()
            assert len(c.projects) == 1
            p = c.projects[0]
            open_tasks = [t.name for t in p.tasks if t.status != "done"]
            assert open_tasks == ["Jira and project finalization handover"]
            assert p.status == "live" and p.project_manager.username == "fernando"
            assert p.go_live_date is not None


def test_import_customers_page(client, app):
    with app.app_context():
        tid = ProjectTemplate.query.filter_by(name="Platbricks Health Care Suite").one().id
    assert client.get("/customers/import").status_code == 200
    data = {"names": "Puleo\nEuromed Pharma\n\nPuleo", "template_id": str(tid), "pm_id": "", "progress": "last1"}
    r = client.post("/customers/import", data=data)
    assert r.status_code == 200 and b"project created" in r.data
    r = client.post("/customers/import", data=data)
    assert b"skipped" in r.data
    with app.app_context():
        from app.models import Customer
        assert Customer.query.count() == 2
        for c in Customer.query.all():
            assert [t.name for t in c.projects[0].tasks if t.status != "done"] == ["Jira and project finalization handover"]


def test_project_status_lifecycle(client, app):
    _cid, pid = _create_project(client, app, "Platbricks Health Care Suite")
    with app.app_context():
        p = db.session.get(Project, pid)
        assert p.status == "pre_project"
        ordered = sorted(p.tasks, key=lambda t: (t.phase != "pre", t.position))
        ids = [t.id for t in ordered]
        golive = next(i for t, i in zip(ordered, ids) if "Go Live" in t.name)
    # cannot close with open tasks
    r = client.post(f"/projects/{pid}/status", data={"status": "closed"}, follow_redirects=True)
    assert b"Cannot close" in r.data
    # on hold is manual and sticky
    client.post(f"/projects/{pid}/status", data={"status": "on_hold"})
    client.post(f"/projects/tasks/{ids[0]}/status", data={"status": "done"})
    with app.app_context():
        assert db.session.get(Project, pid).status == "on_hold"
    client.post(f"/projects/{pid}/status", data={"status": "auto"})
    # complete tasks in order: pre -> active -> live -> closed
    for i in ids[1:]:
        client.post(f"/projects/tasks/{i}/status", data={"status": "done"})
        with app.app_context():
            p = db.session.get(Project, pid)
            if i == ids[3]:
                assert p.status == "active"
            if i == golive:
                assert p.status == "live" and p.go_live_date
    with app.app_context():
        assert db.session.get(Project, pid).status == "closed"
    # reopening a task reopens the project
    client.post(f"/projects/tasks/{ids[-1]}/status", data={"status": "todo"})
    with app.app_context():
        assert db.session.get(Project, pid).status == "live"
    # edit form also refuses Closed with open tasks
    r = client.post(f"/projects/{pid}/edit", data={"customer_id": str(_cid), "name": "X", "status": "closed"})
    assert r.status_code == 200 and b"Cannot close" in r.data
