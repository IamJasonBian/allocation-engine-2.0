import os

bind = "0.0.0.0:" + os.getenv("PORT", "10000")
workers = 1           # Single worker — engine thread must be singleton
threads = 4           # Handle concurrent API requests
timeout = 120         # Robinhood API calls can be slow
preload_app = False   # Each worker calls create_app() independently
accesslog = "-"       # Log to stdout
errorlog = "-"
loglevel = "info"


def post_fork(server, worker):
    """Start the engine thread after gunicorn worker is fully initialized.

    This avoids import-lock deadlocks that occur when the engine thread
    tries to import modules while the worker is still in create_app().
    """
    from app.wsgi import application
    from app.background import start_engine_thread
    start_engine_thread(application)
