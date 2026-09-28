# Vercel Deployment Guide (primary deployment)

No real credentials appear in this document.

## 1. Vercel architecture

```
INTERNET → Vercel Python Runtime → api/index.py → Flask app (this repo)
    → Supabase PostgreSQL (DATABASE_URL)   [persistent]
    → Supabase Storage job-tracker-files   [persistent]
```

Vercel is ephemeral/serverless compute: no persistent disk, no SQLite, no
daemon threads, no in-memory persistence between invocations. Importing the
app performs zero I/O (DB init is lazy on first request per instance);
per-request connections via `get_db()` + teardown; sessions are signed
cookies; rate-limit buckets are per-instance (safe degradation).

## 2. Why Flask is compatible

Vercel's Python runtime serves a WSGI `app` object from `api/index.py`,
which re-exports the single instance from `app.py`. No gunicorn, no
`app.run()` — Vercel invokes the object directly.

## 3. Entry point

`api/index.py`: `from app import app`. One instance only.

## 4. Environment variables (Vercel Dashboard → Project → Settings →
   Environment Variables; Production + Preview as needed)

| Key | Value/source |
|---|---|
| `ENV` | `production` (refusing SQLite fallback; missing DB fails boot loudly) |
| `SECRET_KEY` | generate: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `DATABASE_URL` | Supabase → Settings → Database → connection string (pooler if direct is flaky) |
| `TOKEN_ENCRYPTION_KEY` | `python -c "from services.security import generate_token_encryption_key; print(generate_token_encryption_key())"` |
| `SUPABASE_URL` | `https://<ref>.supabase.co` (**REST URL, not the DB string**) |
| `SUPABASE_STORAGE_BUCKET` | `job-tracker-files` |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase → Settings → API → service_role |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Google Cloud Console |
| `GOOGLE_REDIRECT_URI` | `https://job-tracker-tau-inky.vercel.app/auth/google/gmail/callback` |
| `GROQ_API_KEY` / `GROQ_MODEL=openai/gpt-oss-120b` | Groq Console |
| `MAIL_*` | Gmail SMTP as before |
| `CRON_SECRET` | generate (`secrets.token_urlsafe(32)`); unset = cron refused |
| `SCHEDULER_ENABLED` | ignored on Vercel (thread never starts there) |

Full placeholder list: `.env.example`.

## 5. Supabase configuration

Postgres is the only production DB (`ENV=production` raises without a
`postgresql://` `DATABASE_URL`). `init_db()` runs lazily once per instance
(idempotent DDL). Storage: private bucket, key server-only, all files via
authenticated Flask routes.

## 6. Gmail OAuth

Callback route: `/auth/google/gmail/callback`. Google Cloud Console →
Credentials → OAuth client → Authorized redirect URIs → add
`https://job-tracker-tau-inky.vercel.app/auth/google/gmail/callback`
(keep `http://127.0.0.1:5000/…` for local dev). State is random,
session-bound, single-use, 600 s TTL.

## 7. Groq

Live-verified models only: `openai/gpt-oss-120b` (default) →
`openai/gpt-oss-20b` (fallback). Key from env; failures degrade gracefully.

## 8. Cron jobs

`vercel.json` schedules `GET /api/cron/reminders` daily 06:00 UTC. Auth:
`Authorization: Bearer $CRON_SECRET` or `?key=$CRON_SECRET` — Vercel's
scheduled fetch carries neither, so either (a) keep the vercel.json cron as
documentation and trigger via a free external cron (cron-job.org) with
`?key=…`, or (b) append the key to the path in an **untracked local edit**
(never commit it). The task body (`run_reminder_cycle`) is idempotent
(date-stamped dedup), so retries/overlaps never double-send. Gmail per-user
sync remains button-triggered by design.

## 9. Local development

Unset `DATABASE_URL` → SQLite (`database/tracker.db`); `python app.py`.
Simulate serverless: `VERCEL=1` (lazy init, no scheduler, tmp scratch dir).

## 10. Vercel deployment

1. Commit + push to GitHub `main` (Vercel project already linked to the repo).
2. Set every §4 variable in the Vercel Dashboard.
3. Deploy → verify `GET /health` → `{"status":"ok",…}`.
4. Post-deploy: set the Postgres avatar metadata once (Render/Vercel Shell or
   local `DATABASE_URL` run of `scripts/migrate_files_to_supabase.py` —
   object already in bucket, run only writes the row).

## 11. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `FUNCTION_INVOCATION_FAILED` on all routes | Import-time crash (fixed: lazy init) or missing env (check Runtime Logs) |
| `RuntimeError: ENV=production requires DATABASE_URL…` | `DATABASE_URL` unset/not-postgres — set it (fail-fast by design) |
| Gmail `invalid_state` | Stale/replayed callback; retry connect |
| Upload 503 `not configured` | `SUPABASE_*` missing in prod (fail-closed, not a crash) |
| Cron 401 | Missing/wrong `CRON_SECRET` (see §8) |
| Cold starts | Hobby function init; DB/Storage calls add ~1-2 s |

## 12. Logs

Vercel Dashboard → Project → Logs (build + runtime). App logs to
stdout/stderr only; tokens/secrets never logged (auth errors log classes).

## 13. Security

DEBUG=False enforced; cookies HttpOnly + Secure (HTTPS) + Lax; CSRF,
rate limits, OAuth state, SSRF guard, upload limits active; private bucket;
all keys server-only; `/health` exposes no secrets.

## 14. Known limitations

Hobby function duration (default 10 s — Groq/Gmail calls have 6-20 s
timeouts and may occasionally exceed on cold starts; retries via UI);
in-memory rate limits reset per instance; scheduler thread absent (cron §8);
free Sleep applies to Render alternative, not Vercel.
