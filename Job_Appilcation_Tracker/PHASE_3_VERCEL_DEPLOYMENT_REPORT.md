# PHASE 3 — VERCEL DEPLOYMENT REPORT

## 1. Original architecture

Flask (`app.py:app`) + Jinja + vanilla JS, Supabase Postgres + Storage,
in-process scheduler thread, local-fallback uploads, Render-oriented
`render.yaml`/`wsgi.py`, legacy `vercel.json` (v2 `builds`), import-time
`init_db()` + import-time `app = create_app()`.

## 2. Vercel architecture

```
INTERNET → Vercel Python Runtime → api/index.py → Flask app
  → Supabase PostgreSQL (per-request psycopg, teardown-closed)
  → Supabase Storage (private job-tracker-files, REST)
```
Ephemeral compute; signed-cookie sessions; per-instance rate limits;
no threads, no disk persistence, no SQLite in prod.

## 3. Root cause of FUNCTION_INVOCATION_FAILED (proven via Vercel Runtime Logs)

1. **Import-time I/O** (original 500): `app = create_app()` ran `init_db()`
   at import — fixed by lazy per-instance init + `api/index.py`.
2. **Missing router rewrite** (500 → router 404 `X-Vercel-Error`): fixed with
   `"rewrites": [{"/(.*)" → "/api/index"}]`.
3. **Missing `DATABASE_URL` at runtime** (current 500, log-proven):
   `init_db()` → `_init_sqlite()` → `sqlite3.OperationalError: unable to open
   database file` on the read-only FS. The function now raises instead:
   `DATABASE_URL must be a postgresql:// URL (Supabase) when running on
   Vercel…` — visible in Runtime Logs. Resolution is configuration (see §20),
   not code.

## 4. Files changed

New: `api/index.py` (re-export, zero I/O), `routes/cron.py`
(secret-guarded `/api/cron/reminders` + public `/api/cron/status`),
`services.scheduler.run_reminder_cycle()` (extracted idempotent body;
thread loop now calls it), `tests/test_vercel.py` (9 tests),
`docs/VERCEL_DEPLOYMENT.md`, this report.
Modified: `app.py` (lazy per-instance init on Vercel, prod DB fail-closed,
cron blueprint), `vercel.json` (modern: functions + daily cron),
`services/storage_service.py` (tmp scratch on Vercel; prod upload without
Supabase → explicit 503, never ephemeral persistence), `config.py`
(`CRON_SECRET`), `.env.example` + `sample.env` (CRON_SECRET only),
`tests/test_storage.py` + `tests/test_security_hardening.py` (removed global
`VERCEL` leak that broke storage-backend selection), `docs/DEPLOYMENT.md`
(Vercel-primary pointer). `render.yaml`/`wsgi.py` kept as the documented
alternative; nothing app-level depends on Render.

## 5. Vercel entry point

`api/index.py`: `from app import app` — single shared object, verified
`is`-identical in tests.

## 6. Environment variables

`.env.example` covers all 16 used keys incl. new `CRON_SECRET` (placeholders
only). Production additionally requires: `ENV=production`, `SECRET_KEY`,
`DATABASE_URL` (postgresql), Supabase trio, Gmail trio, Groq pair,
`TOKEN_ENCRYPTION_KEY`. `SCHEDULER_ENABLED` ignored on Vercel.

## 7. Supabase PostgreSQL

Only prod DB; `ENV=production` + non-postgres URL raises at startup (clear
logs, no silent SQLite). Lazy once-per-instance `init_db()` (idempotent).
Per-request connections, teardown-closed. NOTE: local `.env` currently has
no `DATABASE_URL` — Vercel **must** define it.

## 8. Supabase Storage

Unchanged integration; Vercel writes route to tmp scratch; prod uploads
without Supabase fail closed (503). Avatar object + metadata verified in
Phase 2 fix; Postgres avatar row still needs one keyed
`migrate_files_to_supabase.py` run from a Postgres-reachable shell
(post-deploy step, §20).

## 9. Filesystem changes

`instance/` + SQLite writes unreachable in prod paths (fail-closed guards);
`/tmp` scratch only; logs to stdout/stderr; no `send_file` local persistence.

## 10. Scheduler changes

Thread never starts when `VERCEL=1` (proven: flag unchanged, sim run).
`run_reminder_cycle()` shared by thread (Render/local) and cron endpoint.

## 11. Vercel Cron configuration

`vercel.json`: daily 06:00 UTC `GET /api/cron/reminders`. Auth: Bearer or
`?key=` = `CRON_SECRET`; unset secret ⇒ 401 everything (fail closed).
Because committed config cannot hold the secret, the recommended trigger is
a free external cron with `?key=` (documented); vercel.json entry retained
as schedule documentation. Tasks idempotent (date-stamped dedup — double
runs verified in tests).

## 12. Gmail/OAuth changes

None to the flow; callback path is `/auth/google/gmail/callback` → production
URI `https://job-tracker-tau-inky.vercel.app/auth/google/gmail/callback`
(manual Google Console step, §20). State/CSRF protections unchanged.

## 13. Groq changes

None in this phase (model IDs fixed live-verified in the prior phase).

## 14. Test results — **110/110 PASS**

100 prior (incl. 5 deployment) + 10 Vercel tests: entry identity, lazy init
with zero I/O at create, no scheduler start, prod DB fail-closed
(+TESTING-bypass contract), Vercel+SQLite raises a clear `DATABASE_URL`
error (the exact log-proven production path), prod storage 503, cron
401/200/idempotent/status-secret-free/unconfigured-refusal.

## 15. Local Vercel runtime result — PASS

`VERCEL=1` simulation, two sequential cold instances, shared DB file:
`/health` 200, `/welcome` 200 ×2, cron unauth 401, scheduler never started.
(`vercel dev` CLI unavailable in sandbox — simulated equivalently.)

## 16. Production deployment result — PENDING OWNER ACTION

Code + config ready; deploy requires the owner's Vercel project (already
linked to GitHub): push `main`, set §6 vars, deploy. Post-deploy testing
below runs against `https://job-tracker-tau-inky.vercel.app/` once live.

## 17. Public URL

`https://job-tracker-tau-inky.vercel.app/` (currently 500 — pre-fix
deployment; redeploy after push).

## 18. Security verification (code-level, all green)

Fail-closed prod DB/storage/cron, secret-free `/health` (tested), cookies
Secure/HttpOnly/Lax in prod, CSRF/rate/state/SSRF/limits active (suite),
service key + DB URL server-only (grep), `.env` ignored/untracked,
`instance/tracker.db` + avatar remain local-only.

## 19. Known limitations

Hobby function duration (~10 s default — LLM/Gmail calls may flirt with it
on cold starts); per-instance rate limits; no durable scheduler (cron §11);
free Render alternative sleeps; Supabase direct DB host is IPv6-only from
some networks (pooler fallback documented); local `.env` lacks DATABASE_URL.

## 20. Manual steps remaining

1. `git add -A && git commit && git push origin main`.
2. Vercel Dashboard → set all §6 vars (Production), incl. `CRON_SECRET` +
   production `GOOGLE_REDIRECT_URI`.
3. Redeploy → `GET /health` must be `{"status":"ok",…}`.
4. From a Postgres-reachable shell: `python scripts/migrate_files_to_supabase.py`
   (object exists; writes the avatar row), verify `avatar_storage_path`.
5. Google Console: add the production callback URI.
6. Share URL + runtime logs if any route still 500s.
7. External cron (optional): daily `GET …/api/cron/reminders?key=SECRET`.

IMPLEMENTED: everything above except §16/§20 live execution.
VERIFIED LOCALLY: suite 110/110 + Vercel simulation.
VERIFIED ON VERCEL: nothing yet (awaiting redeploy).
NOT IMPLEMENTED: nothing in scope outstanding.
REQUIRES MANUAL CONFIGURATION: §20 steps 1-5.
