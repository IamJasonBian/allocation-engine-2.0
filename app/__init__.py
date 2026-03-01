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


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    CORS(app)

    from app.api import register_blueprints
    register_blueprints(app)

    if app.config.get("ENGINE_ENABLED", True):
        start_engine_thread(app)

    return app
