"""Vercel Cron endpoints (Phase 3). The in-process scheduler thread never runs
on serverless, so periodic work is exposed here for Vercel Cron (or any
external free cron) to trigger over HTTP.

Auth: CRON_SECRET via `Authorization: Bearer <secret>` or `?key=<secret>`.
No login session required; the secret is the credential. Never commit it —
configure it in Vercel env vars (and vercel.json must NOT contain it).

Idempotency: run_reminder_cycle() stamps sent dates, so retries/overlaps
never double-send. Gmail per-user sync stays user-triggered (button) by
design — cron only sends due reminders.
"""
import logging

from flask import Blueprint, current_app, jsonify, request

logger = logging.getLogger(__name__)

cron_bp = Blueprint('cron', __name__)


def _cron_authorized():
    expected = (current_app.config.get('CRON_SECRET') or '').strip()
    if not expected:
        return False
    auth = request.headers.get('Authorization', '')
    if auth.startswith('Bearer '):
        from hmac import compare_digest
        if compare_digest(auth[len('Bearer '):].strip(), expected):
            return True
    from hmac import compare_digest
    return compare_digest((request.args.get('key') or '').strip(), expected)


@cron_bp.route('/api/cron/reminders', methods=['GET', 'POST'])
def cron_reminders():
    if not _cron_authorized():
        return jsonify({'error': 'Unauthorized cron request.'}), 401
    from services.scheduler import run_reminder_cycle
    # current_app is the real app object (same instance Vercel serves).
    result = run_reminder_cycle(current_app._get_current_object())
    status = 200 if 'error' not in result else 500
    return jsonify({'success': 'error' not in result, **result}), status


@cron_bp.route('/api/cron/status', methods=['GET'])
def cron_status():
    """Unauthenticated liveness of the cron surface (no secrets, no work)."""
    return jsonify({'cron': 'ready',
                    'endpoints': ['/api/cron/reminders'],
                    'configured': bool((current_app.config.get('CRON_SECRET') or '').strip())})
