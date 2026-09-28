"""Vercel Python serverless entry point.

Vercel's Python runtime serves the ``app`` object from this module. It
re-exports the single Flask application from app.py — no second instance is
created. Importing this module performs no I/O: on Vercel (VERCEL=1) database
initialization is deferred to the first request (see app.create_app) and the
background scheduler never starts (use Vercel Cron → /api/cron/*).
"""
from app import app  # noqa: F401  (Vercel target)
