from flask import Flask, app, jsonify

from app.extensions import db, migrate

from app.auth import session_actor_provider

app.config["CURRENT_ACTOR_PROVIDER"] = session_actor_provider

def create_app(config=None):
    app = Flask(__name__)
    app.config.from_object("app.config.Config")
    if config:
        app.config.update(config)

    db.init_app(app)
    migrate.init_app(app, db)

    # Import and register routes
    from app.api import api
    from app.routes import main

    app.register_blueprint(main)
    app.register_blueprint(api, url_prefix="/api")

    from app.errors import ApiError

    @app.errorhandler(ApiError)
    def handle_api_error(error):
        # An API validation failure must never leave pending ORM mutations that
        # could be flushed by a later request using the same scoped session.
        db.session.rollback()
        return jsonify(error.to_dict()), error.status_code

    return app
