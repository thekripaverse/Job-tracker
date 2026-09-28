import os
from flask import Flask, g
from config import Config
from database.db import init_db, close_db
from routes.applications import applications_bp
from routes.auth import auth_bp
from routes.chatbot import chatbot_bp
from routes.profile import profile_bp
from routes.resume_versions import resume_versions_bp
from routes.email_intelligence import email_intelligence_bp
from routes.fit_analysis import fit_analysis_bp
from routes.cron import cron_bp
from services.scheduler import start_email_scheduler

def create_app(config_class=Config):
    # Tests may point the instance dir at tmp via INSTANCE_PATH config so
    # upload side-effects never touch the real instance/uploads/.
    _instance_path = None
    if isinstance(config_class, dict):
        _instance_path = config_class.get('INSTANCE_PATH')
    else:
        _instance_path = getattr(config_class, 'INSTANCE_PATH', None)
    app = Flask(__name__, instance_path=_instance_path) if _instance_path else Flask(__name__)
    if isinstance(config_class, dict):
        app.config.from_object(Config)
        app.config.update(config_class)
    else:
        app.config.from_object(config_class)

    # ------------------------------------------------------------------
    # Serverless-safe startup (Vercel): never do I/O at import time.
    # - init_db() runs eagerly everywhere EXCEPT on Vercel, where the
    #   filesystem may be read-only and cold starts must stay light. There it
    #   runs once per function instance, lazily on the first request.
    # - The background scheduler thread never starts on Vercel (see cron).
    # ------------------------------------------------------------------
    is_vercel = os.environ.get('VERCEL') == '1'

    if app.config.get('ENV') == 'production' and not app.config.get('TESTING'):
        # (TESTING bypass is test-only: production never sets TESTING, and
        # TESTING already bypasses CSRF/rate-limiting by the same contract.)
        db_cfg = (app.config.get('DATABASE') or '')
        if not db_cfg.startswith(('postgresql://', 'postgres://')):
            raise RuntimeError(
                'ENV=production requires DATABASE_URL to be a postgresql:// URL '
                '(Supabase). Refusing to silently fall back to ephemeral SQLite.'
            )

    if is_vercel:
        @app.before_request
        def _ensure_db_once():
            if not app.config.get('_DB_READY'):
                with app.app_context():
                    init_db()
                app.config['_DB_READY'] = True
    else:
        # Initialize database
        with app.app_context():
            init_db()

    # Start background email scheduler (if not testing and not on Vercel).
    # On hosting with a single worker (see render.yaml) this runs exactly once.
    # Set SCHEDULER_ENABLED=0 to disable (e.g. multi-worker or external cron).
    # On Vercel the scheduler NEVER starts: use Vercel Cron → /api/cron/*.
    scheduler_on = os.environ.get('SCHEDULER_ENABLED', '1') == '1'
    if not app.config.get('TESTING') and not is_vercel and scheduler_on:
        start_email_scheduler(app)

    # Register blueprints
    app.register_blueprint(auth_bp)
    app.register_blueprint(applications_bp)
    app.register_blueprint(chatbot_bp)
    app.register_blueprint(profile_bp)
    app.register_blueprint(resume_versions_bp)
    app.register_blueprint(email_intelligence_bp)
    app.register_blueprint(fit_analysis_bp)
    app.register_blueprint(cron_bp)

    # Always release the per-request DB connection (SQLite + Postgres).
    # Verified: get_db() caches per-request on flask.g; teardown pops and
    # closes it, so no persistent global connections exist on either backend.
    app.teardown_appcontext(close_db)

    @app.route('/health', methods=['GET'])
    def health():
        """Lightweight liveness probe for hosting health checks.

        Returns only safe status fields — never credentials, env values, or
        filesystem details. DB probe is a read-only SELECT 1.
        """
        from database.db import get_db
        db_status = 'unknown'
        try:
            get_db().execute('SELECT 1').fetchone()
            db_status = 'ok'
        except Exception:
            db_status = 'error'
        from services import storage_service as store
        return {
            'status': 'ok' if db_status == 'ok' else 'degraded',
            'database': 'postgres' if app.config.get('DATABASE', '').startswith(
                ('postgresql://', 'postgres://')) else 'sqlite',
            'db_reachable': db_status,
            'storage': store.backend_name(),
        }

    @app.before_request
    def _csrf_hook():
        from services.security import csrf_protect
        rejected = csrf_protect()
        if rejected is not None:
            return rejected

    @app.context_processor
    def inject_user_context():
        from services.security import get_csrf_token
        try:
            csrf_token_value = get_csrf_token()
        except Exception:
            csrf_token_value = ''
        return {
            'current_user': getattr(g, 'user', None),
            'google_client_id': app.config.get('GOOGLE_CLIENT_ID', ''),
            'csrf_token_value': csrf_token_value,
        }

    return app

app = create_app()

if __name__ == '__main__':
    # Development server only. DEBUG comes from config and is always False in
    # production (ENV=production), so the Werkzeug debugger can never be
    # enabled by production configuration.
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=app.config.get('DEBUG', False))
