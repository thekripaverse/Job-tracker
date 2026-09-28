-- Supabase PostgreSQL schema. Derived from database/schema.sql + all
-- idempotent evolutions in database/db.py (FULL column set incl. jd_url).
-- SQLite INTEGER PRIMARY KEY AUTOINCREMENT -> SERIAL PRIMARY KEY.
-- Booleans stay INTEGER 0/1 to preserve existing app code (no ORM changes).
-- Run via database/db.py:_init_postgres() (uses CREATE TABLE IF NOT EXISTS
-- + ADD COLUMN IF NOT EXISTS). Do NOT create tables manually in Supabase.

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username TEXT UNIQUE,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT,
    google_id TEXT UNIQUE,
    avatar_url TEXT,
    full_name TEXT,
    phone TEXT,
    location TEXT,
    headline TEXT,
    university TEXT,
    grad_year TEXT,
    resume_text TEXT,
    resume_filename TEXT,
    avatar_storage_path TEXT,
    resume_storage_path TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_settings (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    notify_followup INTEGER DEFAULT 1,
    notify_interview INTEGER DEFAULT 1,
    reminder_time TEXT DEFAULT '1 Day Before',
    email_notifications INTEGER DEFAULT 1,
    theme TEXT DEFAULT 'light',
    dashboard_view TEXT DEFAULT 'kanban',
    card_density TEXT DEFAULT 'comfortable',
    show_stats INTEGER DEFAULT 1,
    show_warnings INTEGER DEFAULT 1,
    show_interview_dates INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS resume_versions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    version_name TEXT NOT NULL,
    filename TEXT,
    resume_text TEXT,
    storage_path TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS applications (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    company_name TEXT NOT NULL,
    job_title TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('Applied', 'Interviewing', 'Offered', 'Rejected')),
    date_applied DATE NOT NULL,
    last_updated DATE NOT NULL,
    notes TEXT,
    last_email_sent DATE,
    interview_date DATE,
    deadline_date DATE,
    assessment_date DATE,
    followup_date DATE,
    job_url TEXT,
    salary TEXT,
    location TEXT,
    job_type TEXT DEFAULT 'Full-time',
    last_interview_reminder_sent DATE,
    last_assessment_reminder_sent DATE,
    fit_score INTEGER,
    missing_skills TEXT,
    resume_version TEXT,
    jd_url TEXT,
    archived INTEGER DEFAULT 0,
    archived_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS email_connections (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
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
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS email_messages (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
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
    matched_application_id INTEGER REFERENCES applications(id) ON DELETE SET NULL,
    match_confidence REAL,
    match_status TEXT DEFAULT 'pending',
    is_action_required INTEGER DEFAULT 0,
    gmail_labels TEXT,
    source_folder TEXT,
    has_unsubscribe INTEGER DEFAULT 0,
    is_bulk INTEGER DEFAULT 0,
    is_job_related INTEGER,
    exclude_reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS application_timeline_events (
    id SERIAL PRIMARY KEY,
    application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    event_title TEXT NOT NULL,
    event_description TEXT,
    event_date TIMESTAMP NOT NULL,
    source TEXT DEFAULT 'MANUAL',
    email_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for common per-user list queries (SQLite had none; Postgres benefits).
CREATE INDEX IF NOT EXISTS idx_applications_user_status ON applications(user_id, status);
CREATE INDEX IF NOT EXISTS idx_applications_user_updated ON applications(user_id, last_updated DESC);
CREATE INDEX IF NOT EXISTS idx_email_messages_user_class ON email_messages(user_id, classification);
CREATE INDEX IF NOT EXISTS idx_timeline_app_user ON application_timeline_events(application_id, user_id);
CREATE INDEX IF NOT EXISTS idx_resume_versions_user ON resume_versions(user_id);
