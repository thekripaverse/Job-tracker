# Deployment Guide

> **Primary deployment is Vercel** (serverless) — see `docs/VERCEL_DEPLOYMENT.md`.
> This file documents the **optional Render alternative** (persistent single
> process) from the earlier phase. Both share the same Flask app, Supabase
> Postgres, and Supabase Storage; nothing is Render-specific in app code.

No real credentials appear in this document.

## 1. Hosting provider

**Render** (verified against official docs, Sept 2026):

- HOST: Render web service (Python runtime), region of your choice.
- FREE TIER: Hobby workspace $0, no credit card. 750 instance-hours/month,
  512 MB RAM / fractional CPU, GitHub auto-deploy, managed HTTPS, custom
  `onrender.com` URL.
- LIMITATIONS (official, must-read):
  - Sleeps after **15 min idle**; ~1 min cold start on next request.
  - **Ephemeral filesystem** — redeploys/restarts/spin-downs wipe local files.
    User data therefore lives in Supabase (Postgres + Storage), never on disk.
  - No persistent disks, no background workers/cron, no SSH on free.
  - 1 service effectively always-on within the hour budget (sleep pauses burn).
- WHY IT FITS: Flask+gunicorn first-class, outbound HTTPS to Supabase/Gmail/
  Groq allowed, env-var secrets, `/health` check support, single-worker model
  matches the in-process scheduler. Rejected: Koyeb (card required), Railway
  (credit-based, no permanent free), Fly.io (no free tier), Vercel (serverless
  Python breaks the scheduler/thread model; existing `vercel.json` is legacy
  and NOT the deploy target — tracked for history only).

## 2. Prerequisites

- GitHub repo pushed (`main` branch).
- Supabase project with Postgres `DATABASE_URL` (direct or pooler) and the
  private `job-tracker-files` Storage bucket.
- Google Cloud OAuth client (for Gmail + login) — redirect URI updated post-
  deploy (see §8).
- Groq API key.

## 3. Environment variables (Render Dashboard → Environment, all secret ones
   as Secret Files / secret values — never in git)

| Key | Value / source |
|---|---|
| `ENV` | `production` |
| `DEBUG` | `False` |
| `PORT` | set by Render automatically |
| `PYTHON_VERSION` | `3.12.7` (in render.yaml) |
| `SCHEDULER_ENABLED` | `1` |
| `SUPABASE_STORAGE_BUCKET` | `job-tracker-files` |
| `GROQ_MODEL` | `openai/gpt-oss-120b` |
| `SECRET_KEY` | generate: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `DATABASE_URL` | Supabase → Project Settings → Database → connection string |
| `TOKEN_ENCRYPTION_KEY` | `python -c "from services.security import generate_token_encryption_key; print(generate_token_encryption_key())"` |
| `SUPABASE_URL` | `https://<ref>.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase → Project Settings → API → service_role |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Google Cloud Console |
| `GOOGLE_REDIRECT_URI` | `https://<your-app>.onrender.com/auth/google/gmail/callback` |
| `GROQ_API_KEY` | Groq Console |
| `MAIL_SERVER/PORT/USERNAME/PASSWORD` | Gmail SMTP (app password) |

See `.env.example` for the full list with placeholders.

## 4. Build / start commands

- Build: `pip install -r requirements.txt`
- Start: `gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120`
- Health check path: `/health` → `{"status":"ok",...}` (no secrets).
- Single worker is **required**: the hourly email scheduler thread must run
  exactly once, and 512 MB cannot host more. (`render.yaml` encodes all this;
  Render can import it as Blueprint.)

## 5. Deployment procedure

1. Commit + push all phase work to GitHub `main`.
2. Render Dashboard → New → Web Service → select the repo (or Blueprint →
   `render.yaml`).
3. Confirm build/start/health settings from `render.yaml`.
4. Add every variable from §3 (never commit values).
5. Deploy → wait for `Live` + green `/health`.
6. Post-deploy: run `python scripts/migrate_files_to_supabase.py` in
   Render → Shell (same network as the app; sets the Postgres avatar
   metadata that predates the production DB), then `GET /health`.
7. Update Google Cloud Console redirect URI to the `*.onrender.com` callback.

## 6. Supabase / Storage / Gmail / Groq configuration

- Database: production uses Supabase Postgres only (`DATABASE_URL`). App runs
  `init_db()` on boot (idempotent DDL + new columns). Never upload SQLite.
- Storage: private `job-tracker-files`; service key server-only; paths in §4
  of `docs/SUPABASE_STORAGE.md`. Local `instance/uploads/` is fallback only.
- Gmail OAuth: callback route `/auth/google/gmail/callback`; state-protected;
  localhost URI stays configured separately in Google Console.
- Groq: key from env; live-verified models `openai/gpt-oss-120b` (default) /
  `openai/gpt-oss-20b` (fallback); failures degrade gracefully, never leak keys.

## 7. Health endpoint

`GET /health` → `{"status":"ok"|"degraded","database":"postgres"|"sqlite",
"db_reachable":"ok"|"error","storage":"supabase"|"local"}`. No credentials,
env values, or filesystem paths. Covered by `tests/test_deployment.py`.

## 8. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Boot `RuntimeError: SECRET_KEY…` | `ENV=production` without `SECRET_KEY` — set it (fail-fast by design) |
| Cold start ~1 min | Free-tier sleep; expected, not an error |
| `db_reachable: error` | Wrong `DATABASE_URL` (use pooler string if direct fails) |
| Gmail `invalid_state` / redirect mismatch | `GOOGLE_REDIRECT_URI` must match the deployed URL exactly |
| Reminders stop | Service asleep (free tier) or `SCHEDULER_ENABLED=0`; external cron is a later phase |
| 429s on login | Rate limits working as designed; wait per `Retry-After` |

## 9. Rollback

1. Render → Deployments → Redeploy a previous commit (or `git revert` + push).
2. Env vars persist across deploys; keep a local copy of working values.
3. Supabase data is untouched by deploys (migrations are additive).
4. Local dev: unset `DATABASE_URL` → SQLite (`database/tracker.db` preserved).
