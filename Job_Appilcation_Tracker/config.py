import os
from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

ENV = os.environ.get('ENV', 'development').strip().lower() or 'development'
IS_PRODUCTION = ENV == 'production'


def _require_secret_key():
    explicit = os.environ.get('SECRET_KEY')
    if explicit:
        return explicit
    if IS_PRODUCTION:
        # Fail fast with a clear message instead of booting with a guessable key.
        raise RuntimeError(
            'SECRET_KEY must be set in the environment when ENV=production. '
            'Generate one (e.g. `python -c "import secrets; print(secrets.token_hex(32))"`) '
            'and set SECRET_KEY before starting the app.'
        )
    # Development-only fallback. Never used in production (see above).
    return 'dev-secret-key-job-tracker__development-only'


class Config:
    ENV = ENV
    SECRET_KEY = _require_secret_key()
    # PostgreSQL (Supabase) connection string takes precedence when set.
    # Example: DATABASE_URL=postgresql://postgres:<password>@db.<ref>.supabase.co:5432/postgres
    DATABASE_URL = os.environ.get('DATABASE_URL') or ''
    DATABASE = DATABASE_URL or os.environ.get('DATABASE_PATH') or os.path.join(BASE_DIR, 'database', 'tracker.db')
    # DEBUG defaults to False everywhere; enabled only by explicit opt-in in
    # non-production. Production can never enable the Werkzeug debugger.
    DEBUG = (os.environ.get('DEBUG', 'False').lower() in ('true', '1', 't')) and not IS_PRODUCTION

    # Session cookie hardening. SECURE requires HTTPS: enabled in production,
    # disabled in local HTTP development so login/OAuth callbacks keep working.
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = IS_PRODUCTION
    SESSION_COOKIE_SAMESITE = 'Lax'
    PERMANENT_SESSION_LIFETIME = int(os.environ.get('SESSION_LIFETIME_SECONDS') or 86400)

    GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID') or 'YOUR_GOOGLE_CLIENT_ID.apps.googleusercontent.com'
    GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', '')
    GOOGLE_REDIRECT_URI = os.environ.get('GOOGLE_REDIRECT_URI', 'http://127.0.0.1:5000/auth/google/gmail/callback')

    # Explicit dev-only mocks. Default OFF: missing Gmail credentials fail
    # closed. Set DEV_ALLOW_MOCKS=1 in local development only to exercise the
    # Gmail UI without live credentials. Never enabled in production.
    DEV_ALLOW_MOCKS = (os.environ.get('DEV_ALLOW_MOCKS') == '1') and not IS_PRODUCTION

    # SMTP Email Configuration
    MAIL_SERVER = os.environ.get('MAIL_SERVER') or 'smtp.gmail.com'
    MAIL_PORT = int(os.environ.get('MAIL_PORT') or 465)
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME', '')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD', '')

    # Groq AI Chatbot Configuration (live-verified IDs; gpt-oss-120b primary,
    # gpt-oss-20b fallback — older qwen/llama IDs are retired by Groq).
    GROQ_API_KEY = os.environ.get('GROQ_API_KEY', '')
    GROQ_MODEL = os.environ.get('GROQ_MODEL', 'openai/gpt-oss-120b')

    # Upload protection (Phase 1.5): resume files, megabytes.
    MAX_RESUME_MB = int(os.environ.get('MAX_RESUME_MB') or 8)

    # OAuth token encryption key (Fernet, base64). Required in production when
    # Gmail is connected; generate with services.security.generate_token_encryption_key.
    TOKEN_ENCRYPTION_KEY = os.environ.get('TOKEN_ENCRYPTION_KEY', '')

    # Supabase Storage (Phase 2, server-side only — never exposed to browser).
    SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
    SUPABASE_SERVICE_ROLE_KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')
    SUPABASE_STORAGE_BUCKET = os.environ.get('SUPABASE_STORAGE_BUCKET') or 'job-tracker-files'

    # OAuth state TTL, seconds.
    OAUTH_STATE_TTL_S = int(os.environ.get('OAUTH_STATE_TTL_S') or 600)

    # Test/ops kill-switches (default off; TESTING mode bypasses CSRF + rate
    # limits so the pre-existing suite keeps exercising SQLite rollback).
    CSRF_DISABLED = os.environ.get('CSRF_DISABLED') == '1'
    RATE_LIMIT_DISABLED = os.environ.get('RATE_LIMIT_DISABLED') == '1'

    # Vercel Cron / external cron secret for /api/cron/* (Bearer or ?key=).
    # Unset = cron endpoints refuse everything (fail closed).
    CRON_SECRET = os.environ.get('CRON_SECRET', '')
