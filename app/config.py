import os


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "development-only-change-me")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "sqlite:///tennis_set_maker.db"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    ALGORITHM_PATH = os.environ.get("TENNIS_ALGORITHM_PATH")
    HISTORY_WINDOW_DAYS = int(os.environ.get("HISTORY_WINDOW_DAYS", "120"))
    RECAPTCHA_SECRET_KEY = os.environ.get(
        "RECAPTCHA_SECRET_KEY"
    )
    CURRENT_ACTOR_PROVIDER = None
