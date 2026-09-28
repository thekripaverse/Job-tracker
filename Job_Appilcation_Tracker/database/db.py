"""Dual-backend database layer: SQLite (default/rollback) + PostgreSQL (Supabase).

Selection rule (single source of truth):
  DATABASE_URL env (postgres:// or postgresql://) takes precedence.
  Otherwise DATABASE / DATABASE_PATH is treated as a SQLite file path.

All existing call sites keep using:
  db = get_db()
  db.execute('... ? ...', params).fetchone()/fetchall()
  cursor.lastrowid
  db.commit()

The Postgres wrapper translates `?` -> `%s`, `INSERT OR IGNORE` ->
`INSERT ... ON CONFLICT DO NOTHING`, and emulates `lastrowid` via
`RETURNING id`. Rows are dict-like on both backends so
`dict(row)` / `row['col']` keep working.
"""
import os
import sqlite3
from flask import g, current_app


# --------------------------------------------------------------------------
# Backend detection
# --------------------------------------------------------------------------
def _database_url():
    """Return configured URL/path. Explicit DATABASE wins (tests/rollback);
    else DATABASE_URL. config.py already resolves DATABASE = URL or path."""
    try:
        db = current_app.config.get('DATABASE', '')
    except RuntimeError:
        db = ''
    if isinstance(db, str) and db.strip():
        return db.strip()
    try:
        url = (current_app.config.get('DATABASE_URL') or '').strip()
    except RuntimeError:
        url = ''
    if url:
        return url
    return ''


def is_postgres_url(url=None):
    if url is None:
        try:
            url = _database_url()
        except RuntimeError:
            # Outside app context: fall back to env
            url = os.environ.get('DATABASE_URL', '')
    return str(url).startswith(('postgresql://', 'postgres://'))


def is_postgres():
    try:
        return is_postgres_url(_database_url())
    except RuntimeError:
        return False


# --------------------------------------------------------------------------
# Postgres wrappers (psycopg v3, dict rows)
# --------------------------------------------------------------------------
class _PgCursorWrapper:
    def __init__(self, cursor):
        self._cursor = cursor
        self.lastrowid = None

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def __iter__(self):
        return iter(self._cursor.fetchall())


def _translate_sql(sql):
    """Translate SQLite-dialect SQL to Postgres where needed."""
    out = sql
    # INSERT OR IGNORE -> INSERT ... ON CONFLICT DO NOTHING
    if 'INSERT OR IGNORE' in out:
        out = out.replace('INSERT OR IGNORE', 'INSERT')
        # Only append conflict clause for INSERT statements lacking one
        upper = out.upper()
        if 'ON CONFLICT' not in upper:
            out = out.rstrip().rstrip(';') + ' ON CONFLICT DO NOTHING'
    # Placeholder translation ? -> %s (naive but safe: codebase never uses
    # literal ? inside string constants in these queries)
    out = out.replace('?', '%s')
    return out


class _PgConnectionWrapper:
    """Minimal DB-API wrapper exposing .execute/.commit/.rollback/.close."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        import psycopg.rows
        translated = _translate_sql(sql)
        params = params or []
        # Emulate lastrowid for INSERTs via RETURNING id, except tables
        # whose PK is not `id` (user_settings PK = user_id).
        upper = translated.strip().upper()
        is_user_settings = 'INTO USER_SETTINGS' in upper.replace('"', '')
        wants_id = upper.startswith('INSERT') and 'RETURNING' not in upper and not is_user_settings
        if wants_id:
            translated = translated.rstrip().rstrip(';') + ' RETURNING id'
        cur = self._conn.cursor(row_factory=psycopg.rows.dict_row)
        try:
            cur.execute(translated, list(params) if params else None)
        except Exception as e:
            # If RETURNING id fails (unexpected table shape), retry plain.
            if wants_id and 'RETURNING' in translated.upper() and 'id' in str(e).lower():
                try:
                    self._conn.rollback()
                except Exception:
                    pass
                cur = self._conn.cursor(row_factory=psycopg.rows.dict_row)
                translated = translated[:translated.upper().rindex('RETURNING')].rstrip().rstrip(';')
                cur.execute(translated, list(params) if params else None)
                wrapper = _PgCursorWrapper(cur)
                wrapper.lastrowid = None
                return wrapper
            raise
        wrapper = _PgCursorWrapper(cur)
        if wants_id:
            try:
                row = cur.fetchone()
                wrapper.lastrowid = row['id'] if row and 'id' in row else None
            except Exception:
                wrapper.lastrowid = None
        return wrapper

    def commit(self):
        self._conn.commit()

    def rollback(self):
        try:
            self._conn.rollback()
        except Exception:
            pass

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass


# --------------------------------------------------------------------------
# Connection management
# --------------------------------------------------------------------------
def get_db():
    if 'db' not in g:
        url = _database_url()
        if is_postgres_url(url):
            import psycopg
            import psycopg.rows
            # Supabase requires SSL; psycopg handles sslmode in URL.
            # Autocommit off so db.commit() semantics match SQLite code.
            conn = psycopg.connect(url, row_factory=psycopg.rows.dict_row)
            g.db = _PgConnectionWrapper(conn)
        else:
            db_path = url
            dir_name = os.path.dirname(db_path)
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)
            conn = sqlite3.connect(
                db_path,
                detect_types=sqlite3.PARSE_DECLTYPES
            )
            conn.row_factory = sqlite3.Row
            # Enforce FKs on SQLite (Postgres enforces them natively).
            try:
                conn.execute('PRAGMA foreign_keys = ON')
            except Exception:
                pass
            g.db = conn
    return g.db


def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        try:
            db.close()
        except Exception:
            pass


# --------------------------------------------------------------------------
# Schema / migrations
# --------------------------------------------------------------------------
SQLITE_SCHEMA_FILE = os.path.join(os.path.dirname(__file__), 'schema.sql')
POSTGRES_SCHEMA_FILE = os.path.join(os.path.dirname(__file__), 'schema_postgres.sql')


def _sqlite_table_columns(conn, table):
    cur = conn.cursor()
    cur.execute(f'PRAGMA table_info("{table}")')
    return {row[1] for row in cur.fetchall()}


def _init_sqlite(db_path):
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute('PRAGMA foreign_keys = OFF')

        with open(SQLITE_SCHEMA_FILE, mode='r') as f:
            conn.executescript(f.read())

        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(applications)")
        columns = [col[1] for col in cursor.fetchall()]

        if 'user_id' not in columns:
            cursor.execute("INSERT OR IGNORE INTO users (id, username, email) VALUES (1, 'demo', 'demo@example.com')")
            cursor.execute("ALTER TABLE applications ADD COLUMN user_id INTEGER DEFAULT 1 REFERENCES users(id)")

        for col, ddl in [
            ('last_email_sent', 'DATE'),
            ('interview_date', 'DATE'),
            ('deadline_date', 'DATE'),
            ('assessment_date', 'DATE'),
            ('followup_date', 'DATE'),
            ('job_url', 'TEXT'),
            ('salary', 'TEXT'),
            ('location', 'TEXT'),
            ("job_type", "TEXT DEFAULT 'Full-time'"),
            ('last_interview_reminder_sent', 'DATE'),
            ('last_assessment_reminder_sent', 'DATE'),
            ('fit_score', 'INTEGER'),
            ('missing_skills', 'TEXT'),
            ('resume_version', 'TEXT'),
            ('jd_url', 'TEXT'),
            ('archived', 'INTEGER DEFAULT 0'),
            ('archived_at', 'TIMESTAMP'),
        ]:
            if col not in columns:
                cursor.execute(f"ALTER TABLE applications ADD COLUMN {col} {ddl}")

        cursor.execute("PRAGMA table_info(users)")
        user_columns = [col[1] for col in cursor.fetchall()]
        for col_name in ['full_name', 'phone', 'location', 'headline', 'university',
                         'grad_year', 'resume_text', 'resume_filename',
                         'avatar_storage_path', 'resume_storage_path']:
            if col_name not in user_columns:
                cursor.execute(f"ALTER TABLE users ADD COLUMN {col_name} TEXT")

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS resume_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                version_name TEXT NOT NULL,
                filename TEXT,
                resume_text TEXT,
                storage_path TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        ''')

        cursor.execute("PRAGMA table_info(resume_versions)")
        rv_columns = [col[1] for col in cursor.fetchall()]
        for rv_col in ['filename', 'resume_text', 'storage_path']:
            if rv_col not in rv_columns:
                cursor.execute(f"ALTER TABLE resume_versions ADD COLUMN {rv_col} TEXT")

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS email_connections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL UNIQUE,
                provider TEXT DEFAULT 'google',
                email_address TEXT NOT NULL,
                access_token TEXT NOT NULL,
                refresh_token TEXT,
                token_expiry TIMESTAMP,
                last_synced_at TIMESTAMP,
                sync_cursor TEXT,
                granted_scopes TEXT,
                status TEXT DEFAULT 'connected',
                last_error TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        ''')

        for col, col_def in [
            ('granted_scopes', 'TEXT'),
            ('status', "TEXT DEFAULT 'connected'"),
            ('last_error', 'TEXT')
        ]:
            try:
                cursor.execute(f"ALTER TABLE email_connections ADD COLUMN {col} {col_def}")
            except Exception:
                pass

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS email_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                message_id TEXT NOT NULL UNIQUE,
                thread_id TEXT,
                sender_name TEXT,
                sender_email TEXT,
                subject TEXT,
                snippet TEXT,
                body_text TEXT,
                body_html_sanitized TEXT,
                received_at TIMESTAMP,
                classification TEXT,
                confidence_score REAL,
                extracted_data TEXT,
                matched_application_id INTEGER,
                match_confidence REAL,
                match_status TEXT DEFAULT 'pending',
                is_action_required INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (matched_application_id) REFERENCES applications(id) ON DELETE SET NULL
            )
        ''')

        for col, col_def in [
            ('gmail_labels', 'TEXT'),
            ('source_folder', 'TEXT'),
            ('has_unsubscribe', 'INTEGER DEFAULT 0'),
            ('is_bulk', 'INTEGER DEFAULT 0'),
            ('is_job_related', 'INTEGER'),
            ('exclude_reason', 'TEXT'),
        ]:
            try:
                cursor.execute(f"ALTER TABLE email_messages ADD COLUMN {col} {col_def}")
            except Exception:
                pass

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS application_timeline_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                event_title TEXT NOT NULL,
                event_description TEXT,
                event_date TIMESTAMP NOT NULL,
                source TEXT DEFAULT 'MANUAL',
                email_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (application_id) REFERENCES applications(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        ''')

        conn.commit()
    finally:
        conn.close()


def _init_postgres(url):
    """Create base schema + idempotent ADD COLUMN IF NOT EXISTS (Supabase-safe)."""
    import psycopg
    with open(POSTGRES_SCHEMA_FILE, mode='r') as f:
        schema_sql = f.read()
    conn = psycopg.connect(url)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(schema_sql)
            # Idempotent evolution columns (covers legacy DBs + jd_url drift)
            for col, ddl in [
                ('user_id', 'INTEGER'),
                ('last_email_sent', 'DATE'),
                ('interview_date', 'DATE'),
                ('deadline_date', 'DATE'),
                ('assessment_date', 'DATE'),
                ('followup_date', 'DATE'),
                ('job_url', 'TEXT'),
                ('salary', 'TEXT'),
                ('location', 'TEXT'),
                ('job_type', "TEXT DEFAULT 'Full-time'"),
                ('last_interview_reminder_sent', 'DATE'),
                ('last_assessment_reminder_sent', 'DATE'),
                ('fit_score', 'INTEGER'),
                ('missing_skills', 'TEXT'),
                ('resume_version', 'TEXT'),
                ('jd_url', 'TEXT'),
                ('archived', 'INTEGER DEFAULT 0'),
                ('archived_at', 'TIMESTAMP'),
            ]:
                cur.execute(
                    f"ALTER TABLE applications ADD COLUMN IF NOT EXISTS {col} {ddl}"
                )
            for col in ['full_name', 'phone', 'location', 'headline', 'university',
                        'grad_year', 'resume_text', 'resume_filename',
                        'avatar_storage_path', 'resume_storage_path']:
                cur.execute(
                    f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} TEXT"
                )
            for col, ddl in [('filename', 'TEXT'), ('resume_text', 'TEXT'),
                             ('storage_path', 'TEXT')]:
                cur.execute(
                    f"ALTER TABLE resume_versions ADD COLUMN IF NOT EXISTS {col} {ddl}"
                )
            for col, ddl in [('granted_scopes', 'TEXT'),
                             ('status', "TEXT DEFAULT 'connected'"),
                             ('last_error', 'TEXT')]:
                cur.execute(
                    f"ALTER TABLE email_connections ADD COLUMN IF NOT EXISTS {col} {ddl}"
                )
            for col, ddl in [('gmail_labels', 'TEXT'),
                             ('source_folder', 'TEXT'),
                             ('has_unsubscribe', 'INTEGER DEFAULT 0'),
                             ('is_bulk', 'INTEGER DEFAULT 0'),
                             ('is_job_related', 'INTEGER'),
                             ('exclude_reason', 'TEXT')]:
                cur.execute(
                    f"ALTER TABLE email_messages ADD COLUMN IF NOT EXISTS {col} {ddl}"
                )
    finally:
        conn.close()


def init_db():
    url = _database_url()
    if is_postgres_url(url):
        _init_postgres(url)
    else:
        _init_sqlite(url)

# User Helper DB Functions
def get_user_by_id(user_id):
    db = get_db()
    return db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()

def get_user_by_email(email):
    db = get_db()
    return db.execute('SELECT * FROM users WHERE email = ?', (email.strip().lower(),)).fetchone()

def get_user_by_username(username):
    db = get_db()
    return db.execute('SELECT * FROM users WHERE LOWER(username) = ?', (username.strip().lower(),)).fetchone()

def get_user_by_google_id(google_id):
    db = get_db()
    return db.execute('SELECT * FROM users WHERE google_id = ?', (google_id,)).fetchone()

def create_user(username, email, password_hash=None, google_id=None, avatar_url=None):
    db = get_db()
    cursor = db.execute(
        'INSERT INTO users (username, email, password_hash, google_id, avatar_url) VALUES (?, ?, ?, ?, ?)',
        (username.strip() if username else None, email.strip().lower(), password_hash, google_id, avatar_url)
    )
    user_id = cursor.lastrowid
    # Create default settings row
    db.execute('INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)', (user_id,))
    db.commit()
    return user_id

def update_user_profile(user_id, data):
    db = get_db()
    avatar_url = data.get('avatar_url')
    full_name = data.get('full_name')
    phone = data.get('phone')
    location = data.get('location')
    headline = data.get('headline')
    university = data.get('university')
    grad_year = data.get('grad_year')

    db.execute('''
        UPDATE users 
        SET avatar_url = COALESCE(?, avatar_url),
            full_name = COALESCE(?, full_name),
            phone = COALESCE(?, phone),
            location = COALESCE(?, location),
            headline = COALESCE(?, headline),
            university = COALESCE(?, university),
            grad_year = COALESCE(?, grad_year)
        WHERE id = ?
    ''', (avatar_url, full_name, phone, location, headline, university, grad_year, user_id))
    db.commit()
    return get_user_by_id(user_id)

def get_user_settings(user_id):
    db = get_db()
    row = db.execute('SELECT * FROM user_settings WHERE user_id = ?', (user_id,)).fetchone()
    if not row:
        db.execute('INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)', (user_id,))
        db.commit()
        row = db.execute('SELECT * FROM user_settings WHERE user_id = ?', (user_id,)).fetchone()
    return dict(row) if row else {}

def update_user_settings(user_id, data):
    db = get_db()
    # Ensure row exists
    get_user_settings(user_id)

    notify_followup = 1 if data.get('notify_followup') in [True, 1, '1', 'true', 'on'] else 0
    notify_interview = 1 if data.get('notify_interview') in [True, 1, '1', 'true', 'on'] else 0
    reminder_time = str(data.get('reminder_time', '1 Day Before'))
    email_notifications = 1 if data.get('email_notifications') in [True, 1, '1', 'true', 'on'] else 0
    theme = str(data.get('theme', 'light'))
    dashboard_view = str(data.get('dashboard_view', 'kanban'))
    card_density = str(data.get('card_density', 'comfortable'))
    show_stats = 1 if data.get('show_stats') in [True, 1, '1', 'true', 'on'] else 0
    show_warnings = 1 if data.get('show_warnings') in [True, 1, '1', 'true', 'on'] else 0
    show_interview_dates = 1 if data.get('show_interview_dates') in [True, 1, '1', 'true', 'on'] else 0

    db.execute('''
        UPDATE user_settings
        SET notify_followup = ?,
            notify_interview = ?,
            reminder_time = ?,
            email_notifications = ?,
            theme = ?,
            dashboard_view = ?,
            card_density = ?,
            show_stats = ?,
            show_warnings = ?,
            show_interview_dates = ?
        WHERE user_id = ?
    ''', (notify_followup, notify_interview, reminder_time, email_notifications, theme, dashboard_view, card_density, show_stats, show_warnings, show_interview_dates, user_id))
    db.commit()
    return get_user_settings(user_id)

def delete_user_account(user_id):
    db = get_db()
    db.execute('DELETE FROM applications WHERE user_id = ?', (user_id,))
    db.execute('DELETE FROM user_settings WHERE user_id = ?', (user_id,))
    db.execute('DELETE FROM email_connections WHERE user_id = ?', (user_id,))
    db.execute('DELETE FROM email_messages WHERE user_id = ?', (user_id,))
    db.execute('DELETE FROM application_timeline_events WHERE user_id = ?', (user_id,))
    db.execute('DELETE FROM users WHERE id = ?', (user_id,))
    db.commit()

# --------------------------------------------------------------------------
# Email Intelligence & Timeline DB Helpers
# --------------------------------------------------------------------------
def get_email_connection(user_id):
    db = get_db()
    return db.execute('SELECT * FROM email_connections WHERE user_id = ?', (user_id,)).fetchone()

def save_email_connection(user_id, email_address, access_token, refresh_token=None, token_expiry=None, sync_cursor=None, granted_scopes=None, status='connected', last_error=None):
    # OAuth tokens are encrypted at rest (Fernet, key from TOKEN_ENCRYPTION_KEY).
    # Legacy plaintext rows still decrypt transparently via decrypt_token().
    from services.security import encrypt_token
    access_token = encrypt_token(access_token)
    refresh_token = encrypt_token(refresh_token) if refresh_token else None
    db = get_db()
    existing = get_email_connection(user_id)
    if existing:
        db.execute('''
            UPDATE email_connections
            SET email_address = ?,
                access_token = ?,
                refresh_token = COALESCE(?, refresh_token),
                token_expiry = COALESCE(?, token_expiry),
                sync_cursor = COALESCE(?, sync_cursor),
                granted_scopes = COALESCE(?, granted_scopes),
                status = COALESCE(?, status),
                last_error = ?
            WHERE user_id = ?
        ''', (email_address, access_token, refresh_token, token_expiry, sync_cursor, granted_scopes, status, last_error, user_id))
    else:
        db.execute('''
            INSERT INTO email_connections (user_id, provider, email_address, access_token, refresh_token, token_expiry, sync_cursor, granted_scopes, status, last_error)
            VALUES (?, 'google', ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, email_address, access_token, refresh_token, token_expiry, sync_cursor, granted_scopes, status, last_error))
    db.commit()
    return get_email_connection(user_id)

def update_email_connection_status(user_id, status, last_error=None):
    db = get_db()
    db.execute('UPDATE email_connections SET status = ?, last_error = ? WHERE user_id = ?', (status, last_error, user_id))
    db.commit()
    return get_email_connection(user_id)

def update_email_sync_timestamp(user_id, sync_cursor=None):
    db = get_db()
    import datetime
    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    if sync_cursor:
        db.execute('UPDATE email_connections SET last_synced_at = ?, sync_cursor = ? WHERE user_id = ?', (now_str, sync_cursor, user_id))
    else:
        db.execute('UPDATE email_connections SET last_synced_at = ? WHERE user_id = ?', (now_str, user_id))
    db.commit()

def delete_email_connection(user_id):
    db = get_db()
    db.execute('DELETE FROM email_connections WHERE user_id = ?', (user_id,))
    db.commit()

def get_email_messages(user_id, limit=50, job_related_only=False):
    db = get_db()
    rel_filter = "AND (em.is_job_related IS NULL OR em.is_job_related = 1)" if job_related_only else ""
    return db.execute(f'''
        SELECT em.*, a.company_name as matched_company_name, a.job_title as matched_job_title, a.status as matched_current_status
        FROM email_messages em
        LEFT JOIN applications a ON em.matched_application_id = a.id
        WHERE em.user_id = ? {rel_filter}
        ORDER BY em.received_at DESC, em.id DESC
        LIMIT ?
    ''', (user_id, limit)).fetchall()

def get_email_message_by_id(user_id, message_id):
    db = get_db()
    return db.execute('''
        SELECT em.*, a.company_name as matched_company_name, a.job_title as matched_job_title, a.status as matched_current_status
        FROM email_messages em
        LEFT JOIN applications a ON em.matched_application_id = a.id
        WHERE em.user_id = ? AND em.message_id = ?
    ''', (user_id, message_id)).fetchone()

def _email_message_columns():
    """Return the set of physical columns on email_messages (migration-safe)."""
    db = get_db()
    try:
        if is_postgres():
            rows = db.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'email_messages'
            """).fetchall()
            return {r['column_name'] for r in rows}
        rows = db.execute("PRAGMA table_info(email_messages)").fetchall()
        cols = set()
        for r in rows:
            if isinstance(r, dict):
                cols.add(r.get('name'))
            else:
                try:
                    cols.add(r['name'])
                except Exception:
                    cols.add(r[1])
        return cols
    except Exception:
        return set()


def upsert_email_message(user_id, message_id, thread_id, sender_name, sender_email, subject, snippet, body_text, body_html_sanitized, received_at, classification, confidence_score, extracted_data, matched_application_id=None, match_confidence=0.0, match_status='pending', is_action_required=0, gmail_labels=None, source_folder=None, has_unsubscribe=0, is_bulk=0, is_job_related=None, exclude_reason=None):
    db = get_db()
    # Serialize label list for storage.
    if isinstance(gmail_labels, (list, tuple)):
        gmail_labels_str = ','.join(str(l) for l in gmail_labels)
    else:
        gmail_labels_str = gmail_labels
    extra = {
        'gmail_labels': gmail_labels_str,
        'source_folder': source_folder,
        'has_unsubscribe': int(bool(has_unsubscribe)),
        'is_bulk': int(bool(is_bulk)),
        'is_job_related': None if is_job_related is None else int(bool(is_job_related)),
        'exclude_reason': exclude_reason,
    }
    available = _email_message_columns()
    extra = {k: v for k, v in extra.items() if (not available or k in available)}
    extra_keys = list(extra.keys())
    existing = db.execute('SELECT id FROM email_messages WHERE user_id = ? AND message_id = ?', (user_id, message_id)).fetchone()
    if existing:
        existing_id = existing['id'] if isinstance(existing, dict) else existing[0]
        set_clause = (
            'thread_id = ?, sender_name = ?, sender_email = ?, subject = ?, snippet = ?, '
            'body_text = ?, body_html_sanitized = ?, received_at = ?, classification = ?, '
            'confidence_score = ?, extracted_data = ?, matched_application_id = ?, '
            'match_confidence = ?, match_status = ?, is_action_required = ?'
        )
        params = [thread_id, sender_name, sender_email, subject, snippet, body_text, body_html_sanitized, received_at, classification, confidence_score, extracted_data, matched_application_id, match_confidence, match_status, is_action_required]
        for k in extra_keys:
            set_clause += f', {k} = ?'
            params.append(extra[k])
        params.append(existing_id)
        db.execute(f'UPDATE email_messages SET {set_clause} WHERE id = ?', params)
        row_id = existing_id
    else:
        cols = (
            'user_id, message_id, thread_id, sender_name, sender_email, subject, snippet, '
            'body_text, body_html_sanitized, received_at, classification, confidence_score, '
            'extracted_data, matched_application_id, match_confidence, match_status, is_action_required'
        )
        vals = [user_id, message_id, thread_id, sender_name, sender_email, subject, snippet, body_text, body_html_sanitized, received_at, classification, confidence_score, extracted_data, matched_application_id, match_confidence, match_status, is_action_required]
        for k in extra_keys:
            cols += f', {k}'
            vals.append(extra[k])
        placeholders = ', '.join(['?'] * len(vals))
        cursor = db.execute(f'INSERT INTO email_messages ({cols}) VALUES ({placeholders})', vals)
        row_id = cursor.lastrowid
    db.commit()
    return row_id

def update_email_message_match(message_id, user_id, matched_app_id, match_status='confirmed'):
    db = get_db()
    db.execute('''
        UPDATE email_messages
        SET matched_application_id = ?, match_status = ?
        WHERE message_id = ? AND user_id = ?
    ''', (matched_app_id, match_status, message_id, user_id))
    db.commit()

def add_application_timeline_event(application_id, user_id, event_type, event_title, event_description=None, event_date=None, source='MANUAL', email_id=None):
    db = get_db()
    import datetime
    if not event_date:
        event_date = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    else:
        event_date_str = str(event_date).strip()
        if len(event_date_str) == 10 and ' ' not in event_date_str:
            event_date = f"{event_date_str} 00:00:00"
        else:
            event_date = event_date_str

    cursor = db.execute('''
        INSERT INTO application_timeline_events (application_id, user_id, event_type, event_title, event_description, event_date, source, email_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (application_id, user_id, event_type, event_title, event_description, event_date, source, email_id))
    db.commit()
    return cursor.lastrowid

def get_application_timeline_events(application_id, user_id):
    db = get_db()
    return db.execute('''
        SELECT * FROM application_timeline_events
        WHERE application_id = ? AND user_id = ?
        ORDER BY event_date ASC, id ASC
    ''', (application_id, user_id)).fetchall()
