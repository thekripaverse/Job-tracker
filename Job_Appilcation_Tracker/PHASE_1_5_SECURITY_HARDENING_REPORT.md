# PHASE 1.5 — SECURITY HARDENING + DATA INTEGRITY REPORT

**Scope:** orphan cleanup, pre-deployment security fixes, production-blocking
bugs, DB/resource safety. NOT implemented (per instructions): Storage, deploy,
Supabase Auth, Scheduler, frontend redesign, React/microservices/Redis.

## 1. Orphan cleanup

- **Original orphan:** `user_settings.user_id = 6` — all-default values
  (byte-identical to a fresh row), no `users.id = 6` (SQLite users are
  1,2,3,4,5,7,8). No customization, no legitimate parent: leftover of a
  deleted test user, not real user data.
- **Decision:** remove ONLY that row (no fake user invented).
- **Removed:** yes — backup first:
  `database/tracker.db.pre-orphan-cleanup-20260928.bak` (git-ignored).
  `DELETE FROM user_settings WHERE user_id = 6` (1 row).
- **Verification:** SQLite settings 6→5; `verify_migration.py` now shows
  **all diffs 0** (users 7/7, settings 5/5, versions 2/2, apps 8/8,
  connections 1/1, messages 46/46, timeline 6/6); 8/8 FK checks 0 orphans
  (SQLite, live). Postgres re-query was blocked by sandbox networking
  (direct DB host resolves IPv6-only; no IPv4 route — §10), but Phase 1.5
  wrote no Postgres data and changed no schema, so the Phase 1 counts stand
  plus the orphan row never existed in Postgres (it was FK-rejected there).

## 2. Security findings fixed

| # | Finding | Fix | Files |
|---|---|---|---|
| 3 | Predictable `SECRET_KEY` fallback | `ENV=production` without explicit key → `RuntimeError` at import; dev-only fallback clearly marked | `config.py` (proven: raises without key) |
| 4 | `DEBUG` default True + `debug=True` | Default False; forced False in production; `app.run(debug=config)` | `config.py`, `app.py` (proven: `DEBUG=False` even when requested in prod) |
| 5 | `verify_signature=False` Google bypass | Removed; missing/placeholder client ID → 503, no auth; `verify_oauth2_token` (sig+aud+iss+exp); required `sub`/`email`; generic error (no `str(e)` leak) | `routes/auth.py` |
| 6 | `dev_mock_*` auto-mocks | Fail closed by default; dev mocks only under `DEV_ALLOW_MOCKS=1` (forced off in prod); sync on mock without flag → `NOT_CONNECTED` | `routes/email_intelligence.py`, `config.py` |
| 7 | `close_db` unwired (Phase 1 fixed; verify) | Verified: `close_db` registered in `teardown_appcontext_funcs`, per-request `g`-scoped connections only, no globals; request serves 200 | `app.py` (unchanged, verified live) |
| 8 | Cookie flags | `HttpOnly` always, `Secure` in prod (off for local HTTP), `SameSite=Lax` | `config.py` (proven) |
| 9 | No CSRF | Session token + meta/hidden inputs + fetch patch; enforced all mutating routes (403); bypass only `TESTING`/`CSRF_DISABLED` | `services/security.py`, `app.py`, `base/login/signup.html`, `static/js/app.js` |
| 10 | No rate limits | In-memory sliding window; login 20, signup 10, google 20, chat 30, autofill/fit 20, sync 10 per min/IP; 429+Retry-After; bypass only `TESTING`/`RATE_LIMIT_DISABLED` | `services/security.py`, 5 route files |
| 11 | Open redirect `next` | `is_safe_redirect` (local relative only); fallback `/` | `routes/auth.py`, `services/security.py` |
| 12 | OAuth `state=user_id`, unverified | Random session-bound single-use state, 600 s TTL, validated pre-code-exchange | `routes/email_intelligence.py`, `services/security.py` |
| 13 | Plaintext Gmail tokens | Fernet `enc:` at rest (`TOKEN_ENCRYPTION_KEY`; required in prod), decrypt server-side only, legacy-read fallback, never logged/sent; mock untouched | `database/db.py`, `routes/email_intelligence.py`, `services/security.py` |
| 14 | Autofill SSRF | Scheme/host/credential/DNS/global-IP validation, metadata-IP blocklist, 2 MB cap, 6 s timeout, 400 with reason | `services/security.py`, `routes/applications.py` |
| 15 | Unlimited resume upload | 8 MB (`MAX_RESUME_MB`) in shared extractor (master + versions); clear 400 | `routes/profile.py` |
| 16 | `deadline/followup` forced None | Parsed + validated (`YYYY-MM-DD`, 400 on invalid), stored, returned; confirmed no UI inputs exist (API-level, no silent drop) | `routes/applications.py` |
| 17 | Account delete, no confirm | Requires `{"confirm":"DELETE"}` (400 otherwise); session ownership; cascade unchanged | `routes/profile.py` |
| — | `compute_fit_score` NameError (prod-blocking) | Added missing import; resume upload recalc works | `routes/profile.py` |

## 3. Security findings intentionally deferred

- **Credential rotation** (`SECRET_KEY`, Google, Gmail app password, Groq,
  Supabase): needs owner account access; documented in `docs/SECURITY_HARDENING.md`.
- **Shared rate-limit store / advanced WAF / CSP headers / 2FA / password
  strength upgrade / token encryption for SMTP/Groq keys**: beyond single-process
  scope; noted for later phases.
- **Supabase IPv4 pooler URL**: sandbox reaches only IPv6; if deploy env lacks
  IPv6, switch `DATABASE_URL` to the pooler (`*.pooler.supabase.com:6543`).

## 4. Files changed

`config.py`, `app.py`, `database/db.py`, `routes/{auth,applications,chatbot,fit_analysis,email_intelligence,profile}.py`,
`services/security.py` (new), `static/js/app.js`,
`templates/{base,login,signup}.html`, `requirements.txt` (+`cryptography`),
`sample.env`, `.env.example`, `tests/test_security_hardening.py` (new, 15 tests),
`tests/{test_routes,test_platform_employer_fixes,test_email_intelligence,test_fit_score_resume_versions,test_profile_settings}.py`
(test-only: hermetic DNS mocks, fail-closed contract updates, dummy Groq key,
delete-confirm). No deletions; `database/tracker.db` preserved (+1 backup file).

## 5. Database changes

Data: 1 orphan row removed (SQLite only, backed up). Schema: **none** (7 tables
intact, no columns added). Behavior: SQLite now also sets
`PRAGMA foreign_keys=ON` (Phase 1 code, verified present); Postgres FKs already
enforced. Token rows now store `enc:…` ciphertext on next Gmail save (no
backfill fabricated for the dev mock row).

## 6. Configuration changes

`ENV` (production gate), `SECRET_KEY` fail-fast, `DEBUG` default False,
session cookie flags, `DEV_ALLOW_MOCKS` (default off, prod-forced off),
`MAX_RESUME_MB=8`, `TOKEN_ENCRYPTION_KEY`, `OAUTH_STATE_TTL_S=600`,
`CSRF_DISABLED`/`RATE_LIMIT_DISABLED` kill-switches. Templates updated for the
new vars (placeholders only).

## 7. Tests

Full suite **81/81 passed** (offline, tmp SQLite; Supabase host unreachable
from sandbox — IPv6-only, see §10):

| TEST | RESULT | DETAIL |
|---|---|---|
| Pre-existing suite (66, incl. updated delete-confirm, fail-closed Gmail, hermetic DNS) | PASS | 66/66 |
| `SafeRedirectTest` (unit + login `next`) | PASS | 2/2 — externals rejected, `/?view=` kept |
| `GoogleAuthFailClosedTest` | PASS | dummy token → 400/503, session stays anon; `verify_signature` gone from source |
| `GmailMockFailClosedTest` | PASS | connect → `gmail_not_configured`, no row; mock sync → `NOT_CONNECTED` |
| `OAuthStateTest` | PASS | consume-once, no replay, purpose/expiry enforced |
| `SSRFTest` (unit + route) | PASS | public IP-literal allowed; localhost/127/private/metadata/file/ftp/creds/IPv6-loopback rejected; route 400s |
| `UploadLimitTest` | PASS | 8 MB+1 → 400 naming sizes; small file 200 |
| `DeadlineFollowupTest` | PASS | stored+returned on create/update; garbage → 400 |
| `AccountDeleteConfirmTest` | PASS | missing/wrong → 400; `DELETE` → 200 + logged out |
| `CSRFLimitTest` | PASS | signup+API 403 without token, 201 with rotated post-signup token; limiter 3/3 then deny+retry |
| `TokenCipherTest` | PASS | roundtrip, legacy + mock passthrough, keygen |
| `SessionCookieConfigTest` | PASS | HttpOnly+Lax in dev; Secure/DEBUG-off proven for prod via config |
| SQLite FK integrity (8 checks) | PASS | 0 orphans everywhere; counts 7/5/2/8/1/46/6 |
| Teardown wiring | PASS | `close_db` registered; request 200 |
| Prod fail-fast | PASS | `ENV=production` w/o key → `RuntimeError`; with key → DEBUG False, Secure/Lax, mocks off |
| Phase-1 Postgres smoke (24/24, live, pre-hardening code) | PASS (carried) | Re-run blocked by sandbox IPv6 (§10); code paths re-covered by suite above |

## 8. PostgreSQL integrity verification

Phase 1 counts (live): 7/7, 5(+orphan)/5, 2/2, 8/8, 1/1, 46/46, 6/6 with 0
orphans. Phase 1.5: no schema change, no Postgres writes; SQLite-side FK proof
above (8/8 clean). Exact re-run when network allows:
`python scripts/verify_migration.py` and
`python scripts/smoke_postgres.py` (use pooler URL if no IPv6).

## 9. SQLite rollback verification

`DATABASE_URL` unset → suite runs fully on tmp SQLite: **81/81**; real
`database/tracker.db` byte-preserved apart from the single documented orphan
delete (backup kept). Pre-existing rollback tests untouched and passing.

## 10. Remaining production blockers

1. **Rotate all `.env` secrets** (owner action) — see §3-deferred list.
2. **Live Postgres re-verification** from a network with IPv6 or via the
   Supabase pooler URL (sandbox limitation, not app code).
3. `GROQ_MODEL=qwen-2.5-32b-it` is rejected by Groq (`model_not_found`; chat
   falls back, still 200) — AI-phase config fix.
4. Rate limiter is per-process memory (fine single-instance; shared store only
   if scaled horizontally). No Storage/Auth/Scheduler changes (out of scope).

## 11. Exact Phase 2 recommendation

1. Rotate secrets; set prod `ENV`/`SECRET_KEY`/`TOKEN_ENCRYPTION_KEY`.
2. Re-run `verify_migration.py` + `smoke_postgres.py` via pooler/IPv6 box.
3. Fix `GROQ_MODEL` to a currently-served model.
4. Then proceed to the planned Storage phase (avatars/resume blobs), keeping
   the `enc:` token scheme and CSRF/fetch conventions introduced here.

**Not claimed:** "production secure". Fixed exactly what §2 lists; §3 and §10
state what remains.
