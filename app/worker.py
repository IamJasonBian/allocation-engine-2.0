"""Worker entrypoint — run with: python -m app.worker

The only process that runs the engine loop (daily sweeps + book sync). The API
web service runs gunicorn app.wsgi:application and never starts it.
"""

from app import create_app
from app.background import run_engine_loop


def main():
    app = create_app()   # also configures logging
    with app.app_context():
        run_engine_loop(app)


if __name__ == "__main__":
    main()
