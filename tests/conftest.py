import re

import pytest

from app import create_app
from app.extensions import db
from app.models import User
from app.seed import seed_templates


@pytest.fixture
def app(tmp_path):
    app = create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
        "UPLOAD_DIR": str(tmp_path / "uploads"),
    })
    with app.app_context():
        db.create_all()
        seed_templates()
        u = User(username="fernando", full_name="Fernando", is_admin=True)
        u.set_password("secret123")
        db.session.add(u)
        db.session.commit()
    yield app


@pytest.fixture
def client(app):
    c = app.test_client()
    r = c.post("/login", data={"username": "fernando", "password": "secret123"})
    assert r.status_code == 302
    return c


def new_id(response):
    return int(re.search(r"/(\d+)(?:#.*)?$", response.headers["Location"]).group(1))
