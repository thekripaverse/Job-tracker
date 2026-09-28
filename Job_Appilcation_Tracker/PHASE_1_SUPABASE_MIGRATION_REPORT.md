# PHASE 1 — SUPABASE POSTGRESQL MIGRATION REPORT

**Date:** 2026-09-28 · **Scope:** SQLite → Supabase PostgreSQL only (no Storage/Auth/deploy/scheduler/AI changes)

## 1. Existing database architecture

Flask + raw `sqlite3` (no ORM), one file `database/tracker.db`, accessed via
`database/db.py:get_db()` (per-request `g.db`, `Row` factory). DDL in
`database/schema.sql` (7 tables) plus runtime `PRAGMA table_info` + `ALTER
TABLE` evolutions in `init_db()`. All 7 route modules + 3 services call
`get_db().execute('... ? ...')` with `cursor.lastrowid` + `db.commit()`.
FKs declared but never enforced (`PRAGMA foreign_keys` never set);
`close_db()` existed but was never wired to `teardown_appcontext`.

## 2. SQLite dependencies discovered (migration map)

| Pattern | Locations | Postgres handling |
|---|---|---|
| `sqlite3.connect` + `row_factory` | `db.py:get_db/init_db`, `check_users.py` (dev tool, left SQLite-only) | Branch: `psycopg.connect(dict_row)` when `DATABASE` is a postgres URL |
| `?` placeholders (~120 sites) | All routes/services (unchanged) | Wrapper translates `?` → `%s` centrally |
| `INSERT OR IGNORE` | `db.py` ×3 (demo user, settings ×2) | Translated to `INSERT ... ON CONFLICT DO NOTHING` |
| `PRAGMA table_info` | `db.py` ×3 + `_email_message_columns` | `information_schema.columns` on Postgres |
| `AUTOINCREMENT` | `schema.sql` + inline DDL ×4 | `SERIAL PRIMARY KEY` in `schema_postgres.sql` |
| `cursor.lastrowid` | `db.py` ×3, `applications.py`, `resume_versions.py` | Emulated via `RETURNING id` (skipped for `user_settings`, PK=`user_id`, + retry fallback) |
| `executescript` | `init_db` | Postgres path executes `schema_postgres.sql` + `ADD COLUMN IF NOT EXISTS` loop |
| Date/datetime as `'YYYY-MM-DD[ HH:MM:SS]'` strings | Routes/services/`email_service` | Pass through; Postgres `DATE`/`TIMESTAMP` cast them; `format_application_row` already handles `date` objects |
| `LOWER()`, `CURRENT_TIMESTAMP`, `COALESCE`, `LIMIT ?` | Various | Valid in both dialects, unchanged |
| Tests on tmp SQLite (`DATABASE` override) | 9 test files | Fixed precedence so explicit `DATABASE` wins over `.env` `DATABASE_URL` |

## 3. PostgreSQL compatibility changes

- `config.py`: added `DATABASE_URL` (env, never hardcoded); `DATABASE =
  DATABASE_URL or DATABASE_PATH or database/tracker.db`. Empty `DATABASE_URL`
  = SQLite, preserving rollback.
- `database/db.py`: dual backend (detection + `_PgConnectionWrapper` with SQL
  translation + `RETURNING id` emulation); `_init_sqlite()` (original logic +
  `jd_url` column + `PRAGMA foreign_keys=ON`) and `_init_postgres()` (base
  schema + idempotent evolutions); `_email_message_columns()` per-backend;
  `upsert_email_message()` dict-safe id extraction. All helper signatures and
  all route SQL unchanged.
- `app.py`: wired `app.teardown_appcontext(close_db)` (fixes connection leak
  on both backends).
- `requirements.txt`: added `psycopg[binary]>=3.1.0` (already present in this
  env as 3.3.6; no other new libs).
- `sample.env` / `.env.example`: documented `DATABASE_URL`, no values.
- New: `database/schema_postgres.sql` (full evolved schema incl. `jd_url` +
  5 indexes), `scripts/migrate_sqlite_to_postgres.py` (read-only source,
  FK-ordered, ID-preserving, SAVEPOINT-per-row, `setval` reset),
  `scripts/verify_migration.py` (counts + 5 FK checks),
  `scripts/smoke_postgres.py` (24 live checks), `docs/...` (this guide).

## 4. Supabase configuration

Project connection string via `DATABASE_URL` env only (loaded from `.env`,
git-ignored, never in code/JS/templates). Schema created by the app itself on
first Postgres boot — no manual tables. Supabase enforces FKs natively.

## 5. Files changed

Modified: `config.py`, `database/db.py`, `app.py`, `requirements.txt`,
`sample.env`, `.env.example`. Added: `database/schema_postgres.sql`,
`scripts/{migrate_sqlite_to_postgres,verify_migration,smoke_postgres}.py`,
`docs/SUPABASE_POSTGRESQL_MIGRATION.md`, this report. Deleted: nothing.
`database/tracker.db`: preserved, unmodified (verified same 606208 bytes).

## 6. Schema created (Supabase, actual)

`users`, `user_settings` (PK `user_id`), `resume_versions`, `applications`
(+`jd_url`, `archived`), `email_connections`, `email_messages`
(+gmail/relevance cols), `application_timeline_events` — all `IF NOT EXISTS`,
FKs enforced, +5 indexes (§18 of the new schema file).

## 7. Migration method

`scripts/migrate_sqlite_to_postgres.py`: read-only SQLite open → `init_db()`
on Postgres → FK-safe order (users → settings/versions/apps → connections/
messages → timeline) → explicit-id `INSERT ... ON CONFLICT DO NOTHING` with
per-row `SAVEPOINT` (one orphan never wipes good rows) → per-table commit →
`setval()` sequence reset. `--truncate` used for the authoritative run (target
was empty/matching source).

## 8-10. Row counts (SQLite → Postgres, live verified)

| Table | SQLite | Postgres | Diff |
|---|---|---|---|
| users | 7 | 7 | 0 |
| user_settings | 6 | 5 | **-1 documented** |
| resume_versions | 2 | 2 | 0 |
| applications | 8 | 8 | 0 |
| email_connections | 1 | 1 | 0 |
| email_messages | 46 | 46 | 0 |
| application_timeline_events | 6 | 6 | 0 |

Discrepancy: exactly one row — `user_settings.user_id=6`, whose parent
`users` row does not exist in SQLite itself (SQLite ids are 1,2,3,4,5,7,8).
Possible only because legacy SQLite never enforced FKs. Postgres correctly
refused it. All 5 FK/ownership orphan checks return 0 on both DBs.

## 11. Tests performed

- Full suite on SQLite: **66/66 passed** (rollback path intact).
- Migration + verification against live Supabase: counts above, FK checks clean.
- Live socket boot with `DATABASE_URL`: `/welcome` 200, `/login` 200.
- `scripts/smoke_postgres.py` on Supabase: **24/24 passed** — register, login,
  create/view/edit/status+interview-date/delete application, search list,
  timeline, calendar, analytics, dashboard HTML, bulk, version create/list/
  select/delete, email status/stats/list, autofill 200, chat 200, fit 400
  (`NO_RESUME`, graceful), logout. Smoke user removed afterwards.

## 12. Failed tests

None. Two issues found and fixed during the phase (not outstanding):
(a) `RETURNING id` on `user_settings` (PK `user_id`) → now skipped with retry
fallback; (b) test tmp-DB overridden by `.env` `DATABASE_URL` → explicit
`DATABASE` now wins. One accepted skip: the orphan `user_settings(6)` row.

## 13. Security checks

- `.env` git-ignored and untracked; `database/tracker.db` ignored, preserved.
- No `DATABASE_URL`/password in JS, HTML, Jinja, or docs (grepped).
- Backend-only DB access preserved; Row Level Security untouched (no Data API
  use — Flask remains the trusted layer, per Step 11).
- Pre-existing issues NOT in scope and NOT introduced: dev `SECRET_KEY`
  fallback, `DEBUG` default, JWT placeholder bypass, plaintext OAuth tokens —
  flagged for later phases; rotation of the `.env` secrets still recommended.

## 14. Rollback procedure

```
set DATABASE_URL=      # empty = SQLite
python app.py          # serves database/tracker.db (byte-identical, untouched)
```
Tests already prove this path (66/66 on SQLite after the change).

## 15. Remaining issues (none blocking)

- `GROQ_MODEL=qwen-2.5-32b-it` returns Groq `model_not_found` (chat still 200
  via fallback; fit/autofill degrade gracefully). Pre-existing config drift,
  belongs to the AI phase.
- `verify_migration.py` exits 1 while the single documented orphan exists —
  by design (strict); treat `-1 user_settings` as expected until the orphan is
  cleaned in SQLite.

## 16. Exact next steps for Phase 2

1. Decide orphan cleanup: delete `user_settings(user_id=6)` in SQLite (or add
   stub user) so future verifies are fully green.
2. Rotate `.env` secrets (they pre-date this phase) and set prod `SECRET_KEY` /
   `DEBUG=False`.
3. Phase 2 candidates (out of scope here): Supabase Storage for avatars/resume
   blobs, pooler URL + connection limits under load, Sentry/health endpoint,
   Groq model-name fix, CSRF/rate-limit, encrypted OAuth tokens.

**Verdict:** Migration complete per the phase exit criteria — Flask connects to
Supabase PostgreSQL, data migrated with IDs/relationships/timestamps/NULLs
preserved, counts verified (single pre-existing orphan documented), major
workflows tested live (24/24), SQLite rollback intact and tested (66/66).
