"""Production WSGI entry point. Used by gunicorn (see render.yaml):

    gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120

Single worker is intentional: the in-process email scheduler must run exactly
once, and free-tier memory (512 MB) cannot host more.
"""
from app import app  # noqa: F401  (gunicorn target)
