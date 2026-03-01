"""WSGI entry point — run with: gunicorn app.wsgi:application"""

from app import create_app

application = create_app()
