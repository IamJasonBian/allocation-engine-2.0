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

    engine_enabled = app.config.get("ENGINE_ENABLED", True)
    log.info("[create_app] ENGINE_ENABLED=%s, DRY_RUN=%s, ENGINE_BROKER=%s",
             engine_enabled, app.config.get("DRY_RUN"), app.config.get("ENGINE_BROKER"))

    if engine_enabled:
        start_engine_thread(app)
    else:
        log.warning("[create_app] Engine is DISABLED — skipping background thread")

    return app
