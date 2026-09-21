from flask import Flask, jsonify
from flask_wtf.csrf import CSRFProtect

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

    # Import blueprints after the app/extensions have been created.
    # This also avoids circular import problems.
    from app.api import api
    from app.auth import auth_bp, session_actor_provider
    from app.routes import main

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

    from app.errors import ApiError

    @app.errorhandler(ApiError)
    def handle_api_error(error):
        # An API validation failure must never leave pending ORM
        # mutations that could be flushed by a later request using
        # the same scoped session.
        db.session.rollback()

        return jsonify(
            error.to_dict()
        ), error.status_code

    return app