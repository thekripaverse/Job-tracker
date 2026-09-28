# Security Hardening Guide (Phase 1.5)

No real secrets appear in this document. All values below are placeholders.

## 1. Secrets policy

- `.env` holds all secrets and is git-ignored and untracked (verified).
- `sample.env` / `.env.example` contain placeholders only.
- No secret lives in Python, JS, HTML/Jinja, tests (test keys only), or docs.
- Credentials below require **manual rotation by the project owner** (they
  pre-date this phase and live in external accounts; nothing here rotates them
  automatically):
  `SECRET_KEY`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `MAIL_USERNAME`,
  `MAIL_PASSWORD` (Gmail app password), `GROQ_API_KEY`, `OPENROUTER_KEY`
  (unused by code), `DATABASE_URL` (Supabase password).
- Never log tokens: OAuth tokens are never printed; Google auth errors return
  generic messages (`Google auth error: <ExceptionClass>` server-side only).

## 2. Production configuration

```
ENV=production
SECRET_KEY=<generate: python -c "import secrets; print(secrets.token_hex(32))">
DEBUG=False            # forced False in production even if set
DATABASE_URL=postgresql://postgres:<password>@db.<ref>.supabase.co:5432/postgres
TOKEN_ENCRYPTION_KEY=<generate: python -c "from services.security import generate_token_encryption_key; print(generate_token_encryption_key())">
```

- Without an explicit `SECRET_KEY`, the app **refuses to boot** (`RuntimeError`
  at import with instructions). Development uses an explicit var or a clearly
  marked dev-only fallback that can never activate under `ENV=production`.
- The dev server runs `debug=app.config['DEBUG']`, which is always `False` in
  production — the Werkzeug debugger cannot be enabled by prod config.
- Rollback stays one variable away: unset `DATABASE_URL` → SQLite
  (`database/tracker.db`, preserved).

## 3. Authentication behavior

- Google ID tokens are verified with `verify_oauth2_token` (signature,
  audience/client-ID, issuer, expiration). The old `verify_signature=False`
  placeholder path is removed: unconfigured Google auth returns **503** and
  authenticates nobody. Required claims (`sub`, `email`) are enforced.
- Login `next` redirects accept only local relative paths (`/`, `/?view=…`);
  `https://…`, `//host`, `javascript:` etc. fall back to `/`.
- Sessions: `HttpOnly` always, `Secure` in production (plain-HTTP local dev
  keeps working with `Secure` off), `SameSite=Lax` (OAuth callbacks unaffected).

## 4. CSRF (`services/security.py`, `app.py`, `base/login/signup.html`, `app.js`)

- Session-bound token (`secrets.token_urlsafe(32)`) rendered into
  `<meta name="csrf-token">` + hidden inputs on login/signup forms.
- `window.fetch` is patched once in `app.js` to attach `X-CSRFToken` on every
  same-origin mutating request (covers all JSON/multipart calls, incl. GSI).
- Server enforces on all POST/PUT/PATCH/DELETE (form field, JSON body, or
  header; 403 otherwise). Bypassed **only** under `TESTING=True` (existing
  suite) or `CSRF_DISABLED=1`; enforcement is proven by dedicated tests with
  `TESTING=False`.

## 5. Rate limiting (in-memory, per-process)

| Endpoint | Limit |
|---|---|
| `POST /signup` | 10/min/IP |
| `POST /login`, `POST /auth/google` | 20/min/IP |
| `POST /api/chat` | 30/min/IP |
| `POST /api/autofill-url`, `POST /api/fit-score/analyze` | 20/min/IP |
| `POST /api/email-intelligence/sync` | 10/min/IP |

429 + `Retry-After` on excess. Bypassed only under `TESTING=True` /
`RATE_LIMIT_DISABLED=1`. No Redis introduced (single-process scope); move to a
shared store only if horizontally scaled later.

## 6. OAuth state

Gmail connect mints `secrets.token_urlsafe(32)` bound to the session with a
600 s TTL (`OAUTH_STATE_TTL_S`); the callback consumes it single-use and
rejects missing/mismatched/expired/replayed state with `gmail_error=invalid_state`.
No user data travels in `state`.

## 7. Token encryption

- `TOKEN_ENCRYPTION_KEY` (Fernet, base64) from env; **required in production**
  (`get_token_cipher()` raises otherwise), ephemeral dev key with warning
  otherwise. Never hardcoded.
- `save_email_connection()` encrypts (`enc:…`); readers decrypt server-side
  only (`decrypt_token` in the sync flow). Legacy plaintext rows still read
  (transparent fallback) and are re-encrypted on next save — no destructive
  migration, no fabricated migration of the dev mock token.
- Tokens never reach the browser (rows are never serialized with tokens) and
  are never logged. `python -c "from services.security import
  generate_token_encryption_key; print(generate_token_encryption_key())"`.

## 8. SSRF protection (`validate_fetch_target` / `safe_fetch_url`)

Autofill fetches only `http(s)`, no URL credentials, hostname must resolve,
**every** resolved IP must be globally routable (rejects localhost, `127/8`,
RFC-1918, link-local incl. `169.254.169.254`, CGNAT/metadata `100.100.100.100`,
multicast/reserved/unspecified). Single bounded read (2 MB), 6 s timeout, no
app credentials forwarded. Blocked URLs get HTTP 400 with the reason.

## 9. Upload limits

Resumes (master + versions share `extract_text_from_file`): default **8 MB**
(`MAX_RESUME_MB`), 400/413-style JSON error naming actual vs allowed size.
Avatars keep their existing 2 MB + magic-byte checks. No Storage yet.

## 10. Behavior changes to know

- `deadline_date`/`followup_date` are now parsed, validated (`YYYY-MM-DD`,
  400 on garbage), stored, and returned — never silently nulled. No deadline
  inputs exist in the UI yet (API-level fix; UI untouched by design).
- `POST /api/account/delete` requires `{"confirm": "DELETE"}` (400 otherwise);
  ownership via session; cascade unchanged; profile edits unaffected.
- `POST /auth/google` without server Google config → 503, no login.
- Gmail connect/sync without credentials → error redirect / `NOT_CONNECTED`,
  never fake success. Explicit dev mocks need `DEV_ALLOW_MOCKS=1` (forced off
  in production).

## 11. Rollback

Unset `DATABASE_URL` → SQLite. Suite (81 tests) runs offline against tmp
SQLite files. Backup from orphan cleanup:
`database/tracker.db.pre-orphan-cleanup-20260928.bak` (git-ignored).
