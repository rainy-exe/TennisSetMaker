"""Compatibility entry point; use the same factory and database as run.py."""

from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run()
