# Supabase PostgreSQL Migration — Setup & Operations Guide

The app runs on **SQLite by default** and **Supabase PostgreSQL when
`DATABASE_URL` is set**. No code changes are needed to switch — only the
environment variable. `database/tracker.db` is preserved as the rollback target
and is never modified by the migration scripts.

## 1. Supabase setup

1. Create a project at https://supabase.com (free tier is enough).
2. Go to **Project Settings → Database** and copy the **Connection string**
   (URI form, port 5432). Use the **pooler** string only if you hit connection
   limits; the direct string works for this app's per-request connections.
3. No manual table creation: the Flask app creates the schema itself on boot
   (`database/db.py:init_db()` → `database/schema_postgres.sql` + idempotent
   `ADD COLUMN IF NOT EXISTS`). Do NOT create tables by hand in the Supabase
   SQL editor — the schema must stay derived from the repo.

## 2. Required environment variables

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | Yes (for Postgres) | `postgresql://postgres:<password>@db.<ref>.supabase.co:5432/postgres` |
| `SECRET_KEY` | Yes (prod) | Flask session signing (do not use the dev default) |
| `DEBUG` | Recommended `False` (prod) | Disables debugger/reloader |
| `GOOGLE_*`, `GROQ_*`, `MAIL_*` | As before | Unchanged by Phase 1 (see `sample.env`) |

Rules: never commit `.env` (already git-ignored); never put `DATABASE_URL`
in JavaScript, HTML, Jinja templates, or docs. It is server-side only —
`config.py` → `database/db.py`; the frontend never sees it.

## 3. Install

```
pip install -r requirements.txt   # includes psycopg[binary]>=3.1.0
```

## 4. Schema creation

Automatic on first boot with `DATABASE_URL` set:

```
set DATABASE_URL=postgresql://postgres:<password>@db.<ref>.supabase.co:5432/postgres
python app.py
```

`init_db()` creates all 7 tables with FKs enforced (unlike SQLite, where the
app now also enables `PRAGMA foreign_keys=ON`) plus performance indexes
(`idx_applications_user_status`, `idx_email_messages_user_class`, …).
Re-running is safe (all DDL is `IF NOT EXISTS`).

## 5. SQLite → Postgres data migration (source is never modified)

The SQLite file is opened **read-only** (`mode=ro`); the script only writes to
Postgres.

```
# Normal run (idempotent: ON CONFLICT DO NOTHING, per-row SAVEPOINTs)
python scripts/migrate_sqlite_to_postgres.py

# Fresh reload (wipes Postgres tables first — only when Postgres holds no
# real data beyond what came from SQLite)
python scripts/migrate_sqlite_to_postgres.py --truncate
```

IDs are preserved (explicit `id` insert + `setval()` sequence reset), so all
foreign keys (`applications.user_id`, timeline/email links) survive. Known
source orphan `user_settings.user_id=6` (no parent `users` row — possible only
because legacy SQLite never enforced FKs) is skipped and logged; everything
else migrates.

## 6. Data verification

```
python scripts/verify_migration.py
```

Expected output (actual repo truth):

```
users                      7 / 7  diff 0
user_settings              6 / 5  diff -1  (documented orphan, see above)
resume_versions            2 / 2  diff 0
applications               8 / 8  diff 0
email_connections          1 / 1  diff 0
email_messages            46 / 46 diff 0
application_timeline_events 6 / 6 diff 0
+ 5 FK orphan checks, all 0
```

## 7. Running Flask with Supabase / rollback

```
# Supabase Postgres
set DATABASE_URL=postgresql://postgres:<password>@db.<ref>.supabase.co:5432/postgres
python app.py

# Rollback to SQLite (local dev): unset the variable, restart
set DATABASE_URL=
python app.py   # uses database/tracker.db again
```

Smoke test (throwaway user, cleans up after itself):

```
python scripts/smoke_postgres.py   # 24 checks: auth, CRUD, timeline,
                                   # calendar/analytics, bulk, versions,
                                   # email endpoints, AI endpoints, delete
```

## 8. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `psycopg.errors.UndefinedColumn: column "id"` | Old `db.py` (pre-Phase-1 wrapper fix). Pull latest Phase 1. |
| Tests hitting Postgres instead of tmp SQLite | Old `_database_url()` precedence. Fixed: explicit `DATABASE` config wins, so pytest tmp files stay SQLite even with `.env` `DATABASE_URL` present. |
| `violates foreign key constraint` during migration | Source orphan row (only known: `user_settings` user 6). Skipped by design; verify output documents it. |
| `connection failed / timeout` to Supabase | Wrong password/host, or IPv6-only network needing the pooler string. Check Dashboard → Database → Connection string. |
| `The model qwen-2.5-32b-it does not exist` (chat) | Pre-existing Groq model-name drift, unrelated to Phase 1 (chat still returns 200 via fallback chain). Fix `GROQ_MODEL` in Phase 2/AI phase. |
| Slow first boot | `init_db()` DDL + Supabase cold start; subsequent boots are fast. |
