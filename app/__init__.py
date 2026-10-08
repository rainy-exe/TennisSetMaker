from flask import Flask, jsonify, request
from flask_wtf.csrf import CSRFError, CSRFProtect
from sqlalchemy import event

from app.extensions import db, migrate

# Create CSRF object here so auth.py can import it with:
# from app import csrf
csrf = CSRFProtect()


def create_app(config=None):
    app = Flask(__name__)

    # Load default configuration
    app.config.from_object("app.config.Config")

    # Allow tests or other environments to override configuration
    if config:
        app.config.update(config)

    # Initialise Flask extensions
    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    with app.app_context():
        if db.engine.dialect.name == "sqlite" and app.config.get(
            "SQLITE_FOREIGN_KEYS", True
        ):

            @event.listens_for(db.engine, "connect")
            def enforce_sqlite_foreign_keys(connection, _):
                cursor = connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

    # Import blueprints after the app/extensions have been created.
    # This also avoids circular import problems.
    from app.api import api
    from app.auth import auth_bp, session_actor_provider
    from app.routes import main
    from app.team import team

    # Connect permissions.py to the logged-in User.
    #
    # Keep an explicitly supplied provider if one was passed through
    # config, which is useful for testing.
    if app.config.get("CURRENT_ACTOR_PROVIDER") is None:
        app.config["CURRENT_ACTOR_PROVIDER"] = session_actor_provider

    # Register routes
    app.register_blueprint(main)
    app.register_blueprint(auth_bp)
    app.register_blueprint(api, url_prefix="/api")
    app.register_blueprint(team, url_prefix="/api")

    @app.after_request
    def private_response_headers(response):
        if request.blueprint in {"api", "team", "auth", "main"}:
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(CSRFError)
    def handle_csrf_error(error):
        return jsonify({"error": "csrf_failed", "message": error.description}), 400

    from app.errors import ApiError

    @app.errorhandler(ApiError)
    def handle_api_error(error):
        # An API validation failure must never leave pending ORM
        # mutations that could be flushed by a later request using
        # the same scoped session.
        db.session.rollback()

        return jsonify(error.to_dict()), error.status_code

    return app
