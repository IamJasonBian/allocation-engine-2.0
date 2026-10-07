import os

bind = "0.0.0.0:" + os.getenv("PORT", "10000")
workers = 1
threads = 4           # Handle concurrent API requests
timeout = 120         # Robinhood API calls can be slow
preload_app = False   # Each worker calls create_app() independently
accesslog = "-"       # Log to stdout
errorlog = "-"
loglevel = "info"



def post_fork(server, worker):
    """Start the engine loop, but only on the Render background worker.

    Both Render services run this same gunicorn command. Render sets
    RENDER_SERVICE_TYPE itself ("worker" vs "web"), so the API web service never
    runs trading code and there is no flag of ours to set.
    """
    if os.getenv("RENDER_SERVICE_TYPE") != "worker":
        return
    import logging
    import threading
    from app.wsgi import application
    from app.background import run_engine_loop

    def _run():
        try:
            with application.app_context():
                run_engine_loop(application)
        except Exception:
            logging.getLogger("app.background").exception("Engine loop crashed")

    threading.Thread(target=_run, daemon=True, name="engine-loop").start()
