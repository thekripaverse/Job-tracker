# PHASE 3 — PRODUCTION READINESS + DEPLOYMENT REPORT

**Status: code-complete, deployment pending owner action** (Render service
creation requires the owner's Render/GitHub account — see §14).

## 1. Deployment architecture

```
Browser (HTTPS)
  → Render free web service (gunicorn wsgi:app, 1 worker × 4 threads, :$PORT)
  → Flask app (ENV=production, DEBUG=False)
  → Supabase Postgres (DATABASE_URL) + Supabase Storage (job-tracker-files)
  → Gmail API / Groq API / Gmail SMTP (outbound HTTPS)
```

## 2. Hosting provider

- HOST: **Render**, free Hobby workspace ($0, no card), verified against
  official docs Sept 2026.
- FREE TIER: 750 hrs/mo, 512 MB / fractional CPU, GitHub auto-deploy, managed
  TLS + `onrender.com` URL, `/health` checks.
- LIMITATIONS: sleeps after 15 min idle (~1 min wake); ephemeral filesystem
  (all persistence in Supabase by design); no workers/cron/SSH/disks; single
  instance. Free Render Postgres expires in 30 days — **not used** (Supabase
  is the DB).
- WHY IT FITS: Flask+gunicorn first-class; outbound HTTPS allowed; secret env
  vars; single worker matches the in-process scheduler. Rejected with reasons:
  Koyeb (card required), Railway (credit-based, no permanent free), Fly.io
  (no free tier), Vercel serverless (breaks scheduler/threads; legacy
  `vercel.json` retained for history only, not the target).

## 3-4. Runtime requirements / production changes

- Entry: `app.py:app`; prod server `gunicorn wsgi:app` (new `wsgi.py`;
  `flask run`/`app.run` never used in prod). PORT from env (local 5000 kept).
- New: `GET /health` (safe fields only; tested secret-free).
- `SCHEDULER_ENABLED` flag (default on; single worker ⇒ runs once).
- Groq models fixed to live-verified IDs: default `openai/gpt-oss-120b`,
  fallback `openai/gpt-oss-20b` (old qwen/llama/mixtral/gemma IDs return
  `model_not_found` — probed live with app headers; key authenticates).
- `.gitignore` hardened (`*.env`, `.env.*`, `*.bak`, `*.log`,
  `instance/uploads/resumes/`); tracked prod files verified secret-free.
- No schema change; no Auth/Gmail/Groq arch change; SQLite files preserved.

## 5. Environment variables

Full placeholder list in `.env.example` (§3 of `docs/DEPLOYMENT.md` for
sources): `ENV/DEBUG/PORT/SCHEDULER_ENABLED`, `SECRET_KEY`,
`DATABASE_URL`, `TOKEN_ENCRYPTION_KEY`, `SUPABASE_URL/SERVICE_ROLE_KEY/BUCKET`,
`GOOGLE_*` (redirect → `https://<app>.onrender.com/auth/google/gmail/callback`),
`GROQ_*`, `MAIL_*`. Nothing secret committed (grep-verified).

## 6-8. Database / Storage / Gmail-OAuth / Groq

- DB: Supabase Postgres via `DATABASE_URL`; per-request connections +
  teardown (unchanged); `init_db()` idempotent on boot; SQLite never uploaded.
  NOTE: local `.env` currently has **no** `DATABASE_URL` (owner-managed) —
  Render **must** set it or the service falls back to ephemeral SQLite.
- Storage: private `job-tracker-files`; key server-only; avatar object live +
  byte-verified in Phase 2 fix; local fs never used for persistence in prod.
- Gmail: callback route unchanged; state-protected; localhost URI stays;
  production URI is a manual Google Console step (documented).
- Groq: env key, verified model IDs, graceful failures, no key leaks.

## 9. Background-job analysis

In-process daemon thread, hourly (stale + event reminders), guarded to one
start; with 1 gunicorn worker it runs exactly once. ** incompatible with free
sleep** (stops while asleep) and future multi-worker setups → recommend a free
external cron (e.g. Render Cron on paid, or an external ping/cron service)
as a later phase; `SCHEDULER_ENABLED=0` covers that switch. Nothing removed
or duplicated.

## 10. Files changed

New: `wsgi.py`, `render.yaml`, `tests/test_deployment.py` (5 tests),
`docs/DEPLOYMENT.md`, this report. Modified: `requirements.txt` (+gunicorn),
`app.py` (`/health`, scheduler flag), `config.py` (Groq default comment),
`services/{groq_service,email_classifier_service}.py`, `routes/{chatbot,
applications}.py`, `tests/test_chatbot.py` (live model IDs), `.env.example`,
`.gitignore`. Unrelated code untouched.

## 11. Local production test (actual)

`wsgi:app` served with `ENV=production` (+ real `.env`): `DEBUG=False`,
`/health` **200** `{"status":"ok","db_reachable":"ok","storage":"supabase"}`,
`/welcome` 200 (27 KB), `/login` 200. Gunicorn binary can't execute on
Windows (`fcntl` — POSIX-only) so serving was via werkzeug; the gunicorn
command itself is standard and encoded in `render.yaml`.
Full suite: **95 + 5 new = 100/100 passed**.

## 12. Deployment result

**Pending owner action** (selected: "Guide me, I'll deploy"):

1. `git add -A && git commit -m "Phase 1-3: postgres, hardening, storage, prod readiness" && git push origin main`
2. Render → New → Web Service → repo `thekripaverse/Job-tracker` (or Blueprint
   with `render.yaml`) → confirm build/start/health from the file.
3. Set **all** §5 variables in Render Environment (esp. `DATABASE_URL`,
   `SECRET_KEY`, service key, `GOOGLE_REDIRECT_URI` with the new domain).
4. Deploy → share the `https://….onrender.com` URL here.
5. Then I run post-deploy smoke (Step 13) from the public URL.
6. Post-deploy housekeeping: Render Shell →
   `python scripts/migrate_files_to_supabase.py` (sets the Postgres avatar
   metadata; object already in bucket, run is a no-op otherwise), and update
   the Google Console redirect URI.

## 13. Post-deployment tests (to run on the public URL)

Public (`/`, static, HTTPS, `/health`) · auth (register/login/logout/session)
· apps CRUD + dashboard counts · profile + avatar upload/get/delete (object
must appear in `job-tracker-files`) · resume upload/download · new rows in
Supabase Postgres · real Groq reply (`gpt-oss-120b`) · Gmail OAuth via prod
callback · security headers/flags spot-check · failure cases (bad key,
oversize). No fake successes accepted. **Not yet executed — no public URL.**

## 14. Security verification (pre-deploy, all verified)

HTTPS (Render-managed), DEBUG=False enforced, SECRET_KEY fail-fast,
server-only keys (grep), private bucket, CSRF/rate-limit/state/SSRF/limits
active (suite), no secrets in HTML/JS/responses/health (tested).

## 15. Known limitations

Free sleep (~1 min cold start); ephemeral disk (by design — Supabase holds
state); scheduler pauses while asleep; 512 MB/1 worker ceiling; local
`.env` lacks `DATABASE_URL` (must exist on Render); Supabase direct DB host
is IPv6-only from some networks (pooler string is the fallback);
pre-existing `GROQ_MODEL` env in old deploys overrides the fixed default.

## 16. Rollback procedure

Render → Redeploy previous commit (or revert + push); env vars persist;
Supabase data untouched (additive migrations only); local dev = unset
`DATABASE_URL` → SQLite (`database/tracker.db` preserved).

## 17. Recommended Phase 4

External free cron for reminders, custom domain + production Gmail OAuth
finalization, Sentry/uptime monitoring, Postgres pooler URL if direct is
flaky, backup policy for Supabase, then feature work.

**Not claimed:** deployment success, Gmail/Storage/AI/scheduler behavior in
production — all gated on the public URL in §12.
