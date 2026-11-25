import os
from decimal import Decimal

import click
from flask import Flask, redirect, request, url_for
from flask_login import current_user

from .extensions import csrf, db, login_manager, migrate


def create_app(test_config=None):
    app = Flask(__name__)

    data_dir = os.environ.get("DATA_DIR") or os.path.join(app.root_path, "..", "instance")
    data_dir = os.path.abspath(data_dir)
    os.makedirs(data_dir, exist_ok=True)

    production = os.environ.get("APP_ENV") == "production"
    secret = os.environ.get("SECRET_KEY")
    if production and not secret:
        raise RuntimeError("SECRET_KEY must be set in production")

    app.config.update(
        SECRET_KEY=secret or "dev-only-secret-change-me",
        SQLALCHEMY_DATABASE_URI=os.environ.get("DATABASE_URL") or f"sqlite:///{os.path.join(data_dir, 'tracker.db')}",
        DATA_DIR=data_dir,
        UPLOAD_DIR=os.path.join(data_dir, "uploads"),
        MAX_CONTENT_LENGTH=50 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=production,
        REMEMBER_COOKIE_SECURE=production,
        REMEMBER_COOKIE_DURATION=60 * 60 * 24 * 14,
        WTF_CSRF_TIME_LIMIT=None,
    )
    if test_config:
        app.config.update(test_config)
    os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db, render_as_batch=True)
    csrf.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"

    from . import models  # noqa: F401  (register models)
    from .audit import init_audit

    init_audit()

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(models.User, int(user_id))

    from .routes import auth, customers, main, projects, records, settings

    for bp in (auth.bp, main.bp, customers.bp, projects.bp, records.bp, settings.bp):
        app.register_blueprint(bp)

    @app.before_request
    def require_login():
        if request.endpoint in ("auth.login", "static", "health") or request.endpoint is None:
            return None
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login", next=request.path))
        return None

    @app.get("/healthz", endpoint="health")
    def health():
        return "ok"

    register_template_helpers(app)
    register_cli(app)
    return app


def register_template_helpers(app):
    from datetime import date

    from . import models
    from .audit import entity_label

    @app.template_filter("money")
    def money(value, currency=""):
        if value is None or value == "":
            return ""
        value = Decimal(value)
        s = f"{value:,.2f}".replace(",", " ")
        return f"{s} {currency}".strip()

    @app.template_filter("num")
    def num(value):
        if value is None:
            return ""
        value = Decimal(value)
        return f"{value.normalize():f}" if value == value.to_integral() else f"{value:.2f}"

    @app.template_filter("d")
    def fmt_date(value):
        if not value:
            return ""
        return value.strftime("%d %b %Y")

    @app.template_filter("dt")
    def fmt_dt(value):
        if not value:
            return ""
        return value.strftime("%d %b %Y %H:%M") + " UTC"

    # globals are visible inside imported macros too
    app.jinja_env.globals.update(M=models, label=models.label, entity_label=entity_label)

    @app.context_processor
    def inject():
        return {"today": date.today()}


def register_cli(app):
    from .models import User
    from .seed import seed_templates

    @app.cli.command("seed")
    def seed_cmd():
        """Create default templates and the first admin (from env) if missing."""
        if seed_templates():
            click.echo("Default project templates created.")
        username = os.environ.get("ADMIN_USERNAME")
        password = os.environ.get("ADMIN_PASSWORD")
        if username and password and not User.query.count():
            u = User(username=username, full_name=os.environ.get("ADMIN_NAME") or username, is_admin=True)
            u.set_password(password)
            db.session.add(u)
            db.session.commit()
            click.echo(f"Admin user '{username}' created.")

    @app.cli.command("create-user")
    @click.argument("username")
    @click.option("--name", default=None, help="Full name")
    @click.option("--admin", is_flag=True, help="Give admin rights")
    @click.password_option()
    def create_user(username, name, admin, password):
        """Create a user (or reset the password of an existing one)."""
        u = User.query.filter_by(username=username).first()
        if u is None:
            u = User(username=username)
            db.session.add(u)
        u.full_name = name or u.full_name or username
        u.is_admin = admin or u.is_admin
        u.active = True
        u.set_password(password)
        db.session.commit()
        click.echo(f"User '{username}' saved.")
