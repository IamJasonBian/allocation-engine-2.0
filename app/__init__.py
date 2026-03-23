"""Flask app factory for Allocation Engine 2.0."""

import logging
from flask import Flask
from flask_cors import CORS

from app.config import Config
from app.background import start_engine_thread

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)


log = logging.getLogger(__name__)


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    CORS(app)

    from app.api import register_blueprints
    register_blueprints(app)

    # Engine thread is started by gunicorn's post_fork hook (gunicorn.conf.py)
    # to avoid import-lock deadlocks during create_app().
    log.info("[create_app] ENGINE_ENABLED=%s, DRY_RUN=%s, ENGINE_BROKER=%s",
             app.config.get("ENGINE_ENABLED", True),
             app.config.get("DRY_RUN"),
             app.config.get("ENGINE_BROKER"))

    return app
