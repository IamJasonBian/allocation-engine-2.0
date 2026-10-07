"""Flask app factory for Allocation Engine 2.0."""

import logging
from flask import Flask
from flask_cors import CORS

from app.config import Config

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

    # The engine loop runs only on the Render worker (gunicorn.conf.py).
    log.info("[create_app] DRY_RUN=%s, ENGINE_BROKER=%s",
             app.config.get("DRY_RUN"), app.config.get("ENGINE_BROKER"))

    return app
