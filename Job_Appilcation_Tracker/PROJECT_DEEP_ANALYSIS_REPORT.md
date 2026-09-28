# PROJECT DEEP ANALYSIS REPORT — Job & Internship Application Tracking System

**Audit date:** 2026-09-28
**Audit mode:** READ-ONLY (no code changed, no deps installed)
**Repo root:** `A:\Downloads\Job_Appilcation_Tracker_ok_va_nu_paru\Job_Appilcation_Tracker`
**Entry point:** `app.py:create_app()` → Flask app
**Canonical DB:** `database/tracker.db` (SQLite, via `database/db.py` + `database/schema.sql`)

> This report is based on tracing actual source code, not README claims. Every claim below was verified in the listed file/line.

---

# 1. PROJECT STRUCTURE

## 1.1 Directory tree (actual)

```
Job_Appilcation_Tracker/
  app.py                      # Flask factory create_app(), blueprint registration, scheduler start
  config.py                   # Config from .env + defaults
  requirements.txt            # Flask, pytest, google-auth, requests, dotenv, pypdf, python-docx
  vercel.json                 # Vercel python build, all routes -> app.py
  .env / .env.example / sample.env / .gitignore
  check_users.py              # Debug helper: SELECT id,username FROM database/tracker.db
  README.md / summary.md      # Docs (stale, see §17)
  database/
    db.py                     # get_db(), init_db()+migrations, all user/email/timeline helpers
    schema.sql                # 7 tables DDL
    tracker.db / app.db       # Local SQLite files (canonical = tracker.db; app.db = orphan)
  instance/
    tracker.db                # Orphan/legacy SQLite copy (no code references it)
    uploads/avatars/user_7.jpg # Uploaded avatar on disk
  routes/
    __init__.py
    auth.py                   # /welcome, /login, /signup, /auth/google, /logout, login_required
    applications.py (1129L)   # /, /applications CRUD, /api/calendar-events, /api/analytics,
                              # /api/autofill-url, /api/applications/bulk, timeline
    chatbot.py                # POST /api/chat (Groq)
    profile.py (347L)         # /api/profile, /api/resume*, /api/settings, /api/account/*, photo
    resume_versions.py        # /api/resume-versions*, /api/fit-score/select-version, analytics
    fit_analysis.py           # POST /api/fit-score/analyze
    email_intelligence.py(715L)# Gmail OAuth + /api/email-intelligence/* (11 endpoints)
  services/
    groq_service.py           # compute_fit_score, analyze_resume_fit, validate_fit_analysis
    gmail_service.py (443L)   # OAuth URLs, token exchange/refresh, list/detail/parse Gmail
    email_service.py (244L)   # SMTP send_followup/event reminders, stale+event processors
    email_classifier_service.py(838L) # relevance scoring, classify, extract, match to apps
    resume_context.py         # get_active_resume(), build_resume_brief()
    scheduler.py              # start_email_scheduler() daemon thread 3600s
  templates/
    base.html (202L)          # Authenticated shell: header, gradient nav, search, toast root
    dashboard.html (1781L)    # Entire app: 6 views + all modals + chatbot widget
    landing.html (455L)       # Public marketing page
    login.html (77L) / signup.html (86L)  # Standalone split-screen auth
    partials/_application_card.html / _upcoming_interview_card.html
  static/
    js/app.js (4046L)         # All frontend logic: CRUD, calendar, analytics, chat, email, fit
    js/refine.js (818L)       # Toasts, resume preview, FIT popover, photo, bulk, view hooks
    js/theme-3d.js (628L)     # Visual only: orbs, parallax, scrambled text, feed, accordion
    css/style.css (~6088L)    # Base system incl. dark theme
    css/theme-3d.css (~1602L) # Aurora Glass layer
    css/refine.css (~928L)    # Fix layer + toasts/bulk/fit/photo/email cards
    images/login_hero.png, hero_bg.jpg, ai_avatar.jpg
  components/
    JobCalendar.jsx/.css, GradientMenu.jsx/.css, ScrambledText.jsx/.css,
    Highlighter.jsx/.css, AccordionGallery.jsx/.css  # UNUSED React prototypes (see §8)
  tests/ (9 files, ~66 tests)
    test_routes.py, test_profile_settings.py, test_platform_employer_fixes.py,
    test_fit_score_resume_versions.py, test_fit_analysis.py, test_event_reminders.py,
    test_email_intelligence.py, test_chatbot.py, test_bulk_and_photo.py
```

## 1.2 Responsibility of every important directory

| Directory | Responsibility | Verified |
|---|---|---|
| `/ (root)` | Flask factory, config, deploy, env templates, debug script | `app.py:14-53`, `config.py:1-23`, `vercel.json:1-15` |
| `routes/` | All HTTP endpoints (Flask Blueprints, no url_prefix). Auth gate via `routes/auth.py:10-18` | 7 blueprints registered `app.py:32-38` |
| `services/` | External integrations + business logic: Groq LLM, Gmail API, SMTP, classifier, resume context, scheduler | Each file read; no other service layer |
| `database/` | SQLite access + DDL + migrations. No ORM, raw `sqlite3` | `database/db.py:5-213` |
| `templates/` | Server-rendered Jinja. `dashboard.html` is the whole authenticated SPA-in-Jinja | `dashboard.html:1781L`, `base.html:202L` |
| `static/js/` | Vanilla JS, no bundler/framework. `app.js` = core, `refine.js` = additive, `theme-3d.js` = visual | No `package.json`, no imports |
| `static/css/` | Three layered stylesheets loaded in `base.html` order style→theme-3d→refine | `base.html` link tags |
| `components/` | Dead React JSX prototypes, never imported/linked/bundled | Grep: no importer |
| `instance/` | Flask default instance dir; holds orphan `tracker.db` + real avatar uploads `uploads/avatars/` | `routes/profile.py:276-317` writes there |
| `tests/` | `unittest` + `pytest` mix, tempfile SQLite per test, mocked SMTP/Groq/Gmail | 9 files counted |
| Deployment | Only `vercel.json`. No Dockerfile, compose, CI, Procfile | Glob found none |

**No directories for:** `middleware/` (inline `login_required`+`before_app_request` only), `models/`/`schemas/` (dict rows, no ORM), `controllers/` (route funcs are controllers), `utils/` (helpers inline in routes), `Docker/` (none), `migrations/` (inline `PRAGMA table_info` in `db.py`).

---

# 2. TECHNOLOGY STACK

## Frontend

* Framework: **None** — server-rendered Jinja + vanilla JS. Verified: no `package.json`, no React/Vue/Angular runtime, `components/*.jsx` unused.
* Language: HTML (Jinja2), CSS3, vanilla ES6 JS.
* UI library: Hand-rolled CSS (kanban, modals, calendar grid, donut SVG, funnel bars). CDN `gsap@3.12.5` only on `landing.html`.
* Styling: 3 layered CSS files (~8600 lines total).
* State management: Ad-hoc JS module-scope variables (`cachedCalendarEvents`, `chatHistory`, `selectedIds:Set`, `emailIntelligenceCache`, `calendarFilterState`). No Redux/Zustand/Context.
* Routing: Server routes + client `switchView(viewName)` toggling 6 hidden `.app-view-page` divs in `dashboard.html` (`static/js/app.js:switchView`). No SPA router.
* Forms: Native HTML forms (auth) + JS `fetch` JSON/multipart (app modals).
* Charts: Hand-rolled SVG donut (`#donut-segment-*`), div funnel bars, KPI count-up in `theme-3d.js`. No Chart.js/D3.
* API communication: Native `fetch()` (~30 call sites in `app.js`+`refine.js`).
* Auth handling: Cookie session (`session['user_id']`); Google Identity Services button → `POST /auth/google {credential}`.

## Backend

* Framework/Language: **Flask ≥3 (Python)**. Verified `requirements.txt:1`, `app.py:2`.
* API architecture: Mixed — HTML page renders (`GET /`, `/welcome`, `/login`, `/signup`) + JSON REST-ish (`/applications*`, `/api/*`). No versioning (`/api/*` but also bare `/applications`), no OpenAPI.
* Middleware: `login_required` decorator + `load_logged_in_user before_app_request`. No CORS/CSRF/rate-limit middleware.
* Validation: Manual per-route (`required` checks, `VALID_STATUSES`, ext/magic checks). No Marshmallow/Pydantic/WTForms.
* Authentication: Flask session cookie + `werkzeug.security generate/check_password_hash` + Google `verify_oauth2_token` (with bypass, see §7).
* Authorization: Per-query `WHERE user_id=?` ownership checks. No RBAC/roles (single role).
* Background jobs: `services/scheduler.py` daemon thread (`sleep 5` then every 3600s) calling `process_automated_stale_reminders` + `process_upcoming_event_reminders`. Disabled when `TESTING` or `VERCEL=1`.
* Error handling: Per-route `jsonify({error}), 4xx/5xx` + structured `{success, error_type, retryable}` for fit/email.

## Database

* Type: **SQLite** via stdlib `sqlite3`, `Row` factory. No ORM.
* Schema: 7 tables in `database/schema.sql:1-119` + runtime migrations in `database/db.py:23-213`.
* Relationships: FKs declared `ON DELETE CASCADE/SET NULL` but **never enforced** (no `PRAGMA foreign_keys=ON`).
* Indexes: Only PK/UNIQUE auto-indexes. No custom indexes on `applications(user_id,status)`, `email_messages(user_id,classification)`, `timeline(application_id)`.
* Migrations: Idempotent `PRAGMA table_info` + `ALTER TABLE ADD COLUMN` with `try/except pass`.
* Seed: `INSERT OR IGNORE users(1,'demo','demo@example.com')` only for legacy DBs missing `user_id` (`db.py:42`).

## Infrastructure

* Docker/Compose/CI/CD: **None** (glob found no `Dockerfile`, `docker-compose*`, `.github/**/*`, `*.yml`, `Procfile`).
* Cloud: None active. `vercel.json` legacy `builds` style routes all → `app.py`. External SaaS used at runtime: Groq LLM API, Google OAuth/Gmail API, Gmail SMTP.
* Env: `.env` (with live secrets present locally) + `.env.example`/`sample.env` templates; `config.py` loads via `dotenv`.

## Technology table

| Technology | Where Used | Purpose | Verified? |
|---|---|---|---|
| Flask 3 | `app.py`, `routes/*` | HTTP server, Jinja, sessions, blueprints | Yes — `requirements.txt:1`, `app.py:15` |
| Jinja2 (bundled) | `templates/*` | Server rendering | Yes — `render_template` in routes |
| SQLite3 stdlib | `database/db.py`, `check_users.py` | Persistence, no ORM | Yes — `sqlite3.connect` |
| werkzeug.security | `routes/auth.py`, `routes/profile.py` | `generate/check_password_hash` | Yes — imports |
| google-auth | `routes/auth.py:94-151`, `services/gmail_service.py` | `verify_oauth2_token`, OAuth/token/Gmail REST | Yes — `requirements.txt:3` |
| requests | Indirect (tests/tools) | HTTP client lib (routes use `urllib`) | Yes — in requirements, routes use `urllib.request.urlopen` |
| python-dotenv | `config.py:5` | Load `.env` | Yes |
| pypdf / python-docx | `routes/profile.py:43-` `extract_text_from_file` | Resume text extraction (pdf/docx/txt) | Yes — `requirements.txt:6-7` |
| Groq API (urllib) | `services/groq_service.py`, `routes/chatbot.py`, `routes/applications.py:485-490` | Chat, fit score, autofill | Yes — `https://api.groq.com/openai/v1/chat/completions` |
| Gmail API + SMTP_SSL | `services/gmail_service.py`, `services/email_service.py` | OAuth, list/detail Gmail; send reminders | Yes |
| GSI JS (CDN) | `login.html`, `signup.html` | Google Sign-In button | Yes — `accounts.google.com/gsi/client` |
| GSAP CDN | `landing.html` only | Accordion flex animation | Yes |
| Vanilla JS + fetch | `static/js/app.js`, `refine.js` | All client logic | Yes — no framework |
| pytest + unittest | `tests/` (9 files) | ~66 tests, mocked externals | Yes — counted |
| Vercel Python | `vercel.json`, `app.py:27-29,49` | Serverless deploy (scheduler disabled) | Yes |
| PyJWT (implicit) | `routes/auth.py:108` `import jwt` | Dummy-token decode bypass | Partial — **not in requirements.txt** (missing dep risk) |
| Pillow/gunicorn/ORM/limiter | — | — | **Not used** (confirmed absent) |

---

# 3. FUNCTIONALITY INVENTORY

Legend: ✅ working · ⚠️ incomplete · 🔴 broken · 🎭 mock

## 3.1 Job Management (jobs == applications; no separate jobs table)

| Feature | Frontend | Backend | API | DB | Status / Limitations |
|---|---|---|---|---|---|
| Add job | `dashboard.html:#add-modal` form (`company_name*,job_title*,job_type,status,salary,location,date_applied,interview/assessment_date,notes,job_url`) → `app.js:POST /applications` | `routes/applications.py:816-` | `POST /applications` (JSON or form) | `INSERT applications` + timeline `APPLICATION_SUBMITTED` | ✅ working. Duplicate guard `LOWER(company,job)` → 409 + duplicate modal with `force_add` retry. **Limit:** `deadline_date/followup_date` forced `None` on create (`L831,833`) — inputs ignored. Fit recompute optional via Groq. |
| Edit job | `#details-modal` view+edit modes → `PUT /applications/<id>` | `applications.py:918-` | `PUT/PATCH /applications/<id>` | `UPDATE ... WHERE id AND user_id` | ✅ working. Same dead `deadline/followup=None` (`L940,946`). Fit recomputed only if notes/job_url changed. |
| Delete job | Card `Remove` → `#delete-modal` → `DELETE` | `applications.py:1000-` | `DELETE /applications/<id>` | `DELETE WHERE id AND user_id` | ✅ working. Optimistic DOM remove. No soft-delete (hard delete; bulk archive is separate). |
| View job | Card click → `GET /applications/<id>` → `#details-modal` + timeline | `applications.py:900-910` | `GET /applications/<id>`, `GET /api/applications/<id>/timeline` | `SELECT WHERE id AND user_id` + `get_application_timeline_events` | ✅ working. |
| Search | `base.html:#search-input` → `app.js:filterCards()` client substring on `data-company/title` | None (server also has Python `search` filter on `GET /applications?search=` `L268`) | `GET /applications` (server filter) but dashboard uses client filter | None | ✅ working client-side; server search exists but dashboard list is server-rendered, so client filter is what user sees. No debounce, no server pagination. |
| Filtering | Status columns are the filter; calendar has status pills + event-type filter; email has pills | `_active_apps_filter` = `(archived=0 OR archived IS NULL)` | `GET /api/calendar-events`, `/api/analytics` | `archived` col (added by migration, not in base schema) | ✅ working. No custom filter builder. |
| Sorting | `ORDER BY last_updated DESC` server; fit list sorted desc client | `applications.py:77,268` | — | — | ✅ fixed sort only, no user sort control. |
| Job status | 4 values only: `Applied, Interviewing, Offered, Rejected` (`CHECK` in schema + `VALID_STATUSES`) | Enforced on create/update/bulk/confirm-match | All app endpoints | `applications.status` | ✅ working. No `Saved/Assessment/Offer-accepted` etc. Interview/assessment are **dates**, not statuses. |
| Bulk ops | `#bulk-action-bar` (select all/stale/clear, status/archive/delete) → `POST /api/applications/bulk` | `applications.py:1014-` validates `action, ids≤200, status` | `POST /api/applications/bulk` | Per-id ownership loop, partial `updated/failed` | ✅ working. Archive = `archived=1,archived_at`. |
| URL autofill | `#btn-autofill-add/details` → `POST /api/autofill-url {url}` fills form | `applications.py:791-` → `parse_url_job_details` (Groq→JSON-LD→og tags, ATS heuristics) | `POST /api/autofill-url` | None | ✅ working when Groq key set; degrades to heuristics. **SSRF risk** (fetches arbitrary URL, §7). |

## 3.2 Internship Management

No separate internship model. Internships = `applications` with `job_type='Internship'` (select in add/edit) + auto-inference (`intern/co-op` in title → Internship in autofill). Company info = `company_name`; dates = `date_applied/interview_date/assessment_date`; deadlines = `deadline_date` column **exists but dead** (forced None, §3.1). Status: ✅ implemented as convention, ⚠️ deadline non-functional.

## 3.3 Application Tracking lifecycle

Actual lifecycle verified: `Applied → Interviewing → Offered | Rejected` + `archived` flag. No `Saved/Assessment/Offer-accepted` statuses. `interview_date/assessment_date` are parallel date fields. Timeline events (`application_timeline_events`: `APPLICATION_SUBMITTED, STATUS changed, BULK_STATUS_UPDATE, Gmail classifications, INTERVIEW/ASSESSMENT_SCHEDULED`) provide history. `needs_followup = days_since_update≥7 AND status∈{Applied,Interviewing}` + `last_email_sent` throttle.

## 3.4 Interview Management

| Feature | Status |
|---|---|
| Scheduling (dates) | ✅ `interview_date` + `assessment_date` date inputs in add/edit; `POST /api/email-intelligence/add-calendar-event` from Gmail |
| Rounds | ❌ Not implemented (single date fields, no round table) |
| Notes | ✅ `notes` free text (also used as JD for fit score) |
| Status | ✅ Via application `status`; per-interview status none |
| Reminders | ✅ `process_upcoming_event_reminders()` — interview/assessment == tomorrow, respects `user_settings.notify_interview && email_notifications`, dedups `last_*_reminder_sent != today`. Plus 7-day stale `process_automated_stale_reminders()`. Only runs via scheduler (disabled on Vercel/TESTING) |

## 3.5 Document Management (resumes + avatars; no cover-letter/certificate model)

| Feature | Endpoint | Status |
|---|---|---|
| Resume upload (master) | `POST /api/resume/upload` (multipart `resume_file\|file`, pdf/docx/txt) → `UPDATE users.resume_text/filename` + `recalculate_user_fit_scores` | ✅ working; ⚠️ **no size limit**; 🔴 `recalculate` raises `NameError` (`compute_fit_score` not imported in `profile.py:100`) |
| Resume versions | `GET/POST /api/resume-versions`, `DELETE /api/resume-versions/<id>`, `POST /api/fit-score/select-version`, `GET /api/analytics/resume-versions` | ✅ working; selecting version copies text into `users.resume_text` (denormalized) |
| Resume download | ❌ No download endpoint; preview via `GET /api/resume` returns text | Planned but not implemented |
| Resume delete | `DELETE /api/resume` nulls `users.resume_*` + `applications.fit_score/missing_skills` | ✅ |
| Cover letter/certificates/offer letters | ❌ No model/endpoints/UI | Not implemented |
| Avatar upload | `POST /api/profile/photo` (jpg/png/webp, magic-byte + 2MB, safe `user_{id}.ext` in `instance/uploads/avatars/`) | ✅ working |
| Avatar serve/delete | `GET /api/profile/photo/file`, `DELETE /api/profile/photo` (own only, no IDOR) | ✅ |
| Storage | Local filesystem (`instance/uploads/avatars/`) + resume text in SQLite. No S3/cloud | ✅ local only |

## 3.6 Dashboard

| Element | Source | Status |
|---|---|---|
| Kanban (4 columns + counts) | Server `grouped/counts` in `index` + client `recalculateCounters()` | ✅ |
| Upcoming interviews | Server `upcoming_interviews` + `_upcoming_interview_card.html` | ✅ |
| Action-required (stale) | Server `followup_applications` + `_application_card.html` + `.stale-highlight` | ✅ |
| KPIs (total/interview/offer/rejection rates) | `GET /api/analytics` → `#kpi-*` | ✅ |
| Funnel + donut | Same analytics payload | ✅ |
| Calendar | `GET /api/calendar-events` expands each app → 1-4 events | ✅ |
| Email updates mini-list | `loadDashboardEmailUpdates()` (max 4) | ✅ when Gmail connected |
| Resume-version performance | `GET /api/analytics/resume-versions` | ✅ |
| Charts lib | None (hand-rolled) | — |

## 3.7 Other implemented features

* **AI Chatbot** (`POST /api/chat`): app+email+resume context, 6-turn history, 4-model fallback, career-only system prompt. Fallback reply when no key. ✅
* **FIT score + analysis** (`POST /api/fit-score/analyze`, `compute_fit_score`, `analyze_resume_fit`): authoritative score stored on app; strengths/improvements/keywords rendered by `refine.js`. ✅ (needs Groq key; `502 retryable:true` without)
* **Email intelligence** (Gmail OAuth, sync 30 msgs, heuristic+AI classify, match to apps, confirm/ignore, draft reply via Groq, add-calendar-event, stats): ✅ with live creds; 🎭 mock path when placeholder client ID (fake connected + fake sync success)
* **Profile/settings**: `GET/PUT /api/profile`, `GET/PUT /api/settings`, change-password, delete-account. ⚠️ settings/change-password/delete UI elements absent in `dashboard.html` (JS dead, API live)
* **Auth**: login/signup/logout + Google One-Tap. ✅ (with security caveats §7)

---

# 4. COMPLETE USER FLOW

## 4.1 Global flow

```
User browser
 ↓ (GET /welcome | /login | /signup | /  | fetch /api/*)
Flask (app.py:create_app)
 ↓ before_app_request: load_logged_in_user (session[user_id] → g.user)
login_required gate: /api* or non-GET /applications* or JSON → 401 else redirect /welcome
 ↓ Blueprint route (routes/*)
Service (services/*: Groq/Gmail/SMTP/classifier/resume_context) [optional]
 ↓ sqlite3 (database/db.py:get_db → database/tracker.db, Row factory)
Response: render_template (HTML) or jsonify (JSON)
 ↓ Frontend: full reload (auth, create/edit/delete) OR optimistic DOM (status/bulk) OR lazy view load (switchView)
UI update: kanban move, counters, calendar, KPIs, toasts, modals
```

Background: `scheduler.py` thread (non-Vercel) → `email_service.process_*` → SMTP → `last_email_sent / last_*_reminder_sent` stamps.

## 4.2 Traced workflows

### Login
1. UI: `templates/login.html` form `POST /login?next=` (`login_input`,`password`) or GSI button → `handleGoogleCredentialResponse` → `POST /auth/google {credential}`.
2. Validation: HTML5 `required`; server requires both fields, `get_user_by_email or get_user_by_username`, rejects Google-only (`password_hash None`), `check_password_hash`.
3. API: `POST /login` (form) / `POST /auth/google` (JSON). No separate `/api/login`.
4. Backend: `routes/auth.py:32-94`.
5. DB: `SELECT users WHERE email/username`.
6. Response: redirect `next or /` (form) or `{success,redirect}` (Google); error re-renders `login.html {{error}}`.
7. State: `session.clear(); session[user_id]=id` (server cookie). No client token.
8. UI: navigates to `GET /` dashboard.

### Registration
Same as login via `templates/signup.html` → `POST /signup` (`username,email,password,confirm`, `minlength=6`, `password==confirm`, uniqueness checks, `generate_password_hash`, auto-login). No email verification.

### Add application
1. UI: `#btn-open-modal` → `#add-modal` (`add-app-form`). Optional autofill (`add-job-url` + `POST /api/autofill-url`).
2. Fields: `company_name*,job_title*,job_type,status,salary,location,date_applied(=today),interview/assessment_date,notes,job_url,resume_version`.
3. Validation: HTML5 `required` + server `company/job required (400)`, `status ∈ 4 (400)`.
4. Request: `POST /applications` (JSON if `fetch`, else form).
5. Route: `applications.py:816`.
6. Service: optional `compute_fit_score`, Groq for autofill only.
7. DB: duplicate check → 409; else `INSERT` + `add_timeline_event(APPLICATION_SUBMITTED)`.
8. Response: `201 JSON` or redirect `/`; 409 `{duplicate_found,existing_app}` → `#duplicate-modal` (View Existing scroll+pulse vs `force_add:true` retry).
9. State/UI: `location.reload()` (no optimistic insert).

### Edit application
Card click → `GET /applications/<id>` fills `#details-modal` view mode → `toggleEditMode(true)` → submit → `PUT /applications/<id>` (merge with existing, forced `deadline/followup=None`) → reload.

### Delete application
`Remove` → `confirmDelete` → `#delete-modal` → `DELETE /applications/<id>` → remove node + `recalculateCounters()` + `kanban:changed{removedId}` (refine prunes bulk selection).

### Update status (inline)
Card `select.status-dropdown` → `PUT /applications/<id>{status}` → optimistic move to `#cards-{status}`, pill/accent swap, counters; failure → `alert` + reload. Bulk variant via `POST /api/applications/bulk`.

### Add interview
Via add/edit `interview_date/assessment_date` inputs, or email modal `POST /api/email-intelligence/add-calendar-event {application_id,event_date,event_type}` → `UPDATE` + timeline. Surfaces in upcoming section + calendar (`GET /api/calendar-events`).

### Upload document (resume)
Fit view dropzone/file input → `uploadResumeFile`: if `version_name` → `POST /api/resume-versions` (multipart) else `POST /api/resume/upload` → `extract_text_from_file` (pdf/docx/txt) → `UPDATE users` → `recalculate_user_fit_scores` (🔴 NameError) → toast + preview refresh. Avatar separate flow (`POST /api/profile/photo`).

### Dashboard statistics
`GET /` server counts → `#col-count-*`; entering analytics view → `GET /api/analytics` → `#kpi-*, #funnel-*, #donut-segment-*`; versions chart via `GET /api/analytics/resume-versions`. Client counters recomputed on mutations.

### Search/filter
Dashboard `filterCards()` substring hide + per-column empty states; calendar `matchesCalendarFilters()` (search+status+type); email pills client filter. Server `GET /applications?search=` exists but unused by dashboard filter.

### Notifications
No push/in-app center. Email only: 7-day stale + tomorrow interview/assessment via SMTP (scheduler). Settings `notify_followup/notify_interview/email_notifications` gate them. Gmail sync does not send; it ingests.

---

# 5. API AUDIT

Auth = `login_required` (session). Public only: `GET /welcome`, `GET/POST /login`, `GET/POST /signup`, `POST /auth/google`, `GET /logout`.

| Method | Endpoint | Purpose | Auth | Frontend caller | DB op | Status |
|---|---|---|---|---|---|---|
| GET | `/` | Dashboard render (apps, counts, upcoming, followups, versions) | Yes | Browser nav, post-login redirect | `SELECT applications WHERE user_id ORDER BY last_updated DESC` | ✅ called |
| GET | `/welcome` | Landing | No | Browser, logged-out redirect | None | ✅ |
| GET,POST | `/login` | Session login (form) | No | `login.html` form | `SELECT users` | ✅ |
| GET,POST | `/signup` | Register + auto-login | No | `signup.html` form | `SELECT` checks + `INSERT users + user_settings` | ✅ |
| POST | `/auth/google` | GSI credential login/register | No | `handleGoogleCredentialResponse` (login/signup) | `SELECT/INSERT users` | ✅; 🔴 verify bypass when placeholder client ID |
| GET | `/logout` | Clear session | No | `#header-logout-btn` | None | ✅ |
| GET | `/applications` | JSON list (or redirect to `/` for doc nav) | Yes* | `loadFitScorePage`, `populateFitPopover` | `SELECT WHERE user_id` + Python search filter | ✅ called (2 callers) |
| POST | `/applications` | Create app | Yes | `#add-app-form` | Duplicate check + `INSERT` + timeline | ✅ |
| GET | `/applications/<id>` | Single app (edit modal) | Yes | `openApplicationDetails` | `SELECT WHERE id AND user_id` else 404 | ✅ |
| PUT,PATCH | `/applications/<id>` | Update app / inline status | Yes | `updateApplicationStatus`, details submit, notes(dead) | `UPDATE WHERE id AND user_id` | ✅ |
| DELETE | `/applications/<id>` | Delete app | Yes | `confirmDelete` | `DELETE WHERE id AND user_id` | ✅ |
| GET | `/api/applications/<id>/timeline` | Timeline events | Yes | `loadApplicationTimeline` | `get_application_timeline_events` | ✅ |
| POST | `/api/applications/bulk` | Bulk status/archive/unarchive/delete (≤200) | Yes | Bulk bar (`refine.js`) | Per-id ownership loop + single commit | ✅ |
| GET | `/api/calendar-events` | Expand apps → 1-4 events | Yes | `renderSmartCalendar` | `SELECT active apps` | ✅ |
| GET | `/api/analytics` | Counts + rates | Yes | `loadAnalyticsData` | `SELECT active apps`, Python agg | ✅ |
| POST | `/api/autofill-url` | Extract job from URL | Yes | `autoFillFromUrl` | None (fetch URL + Groq) | ✅; SSRF |
| POST | `/api/chat` | AI assistant | Yes | Chat widget `chatbot-form` | `SELECT apps + email_messages(10) + resume` | ✅; fallback when no key |
| GET,PUT | `/api/profile` | Get/update profile (7 fields) | Yes | `profile-form` (PUT); GET on view load | `SELECT/UPDATE users` | ✅ |
| GET | `/api/resume` | Master resume text | Yes | `renderResumePreview`, fit page | `SELECT users.resume_*` | ✅ |
| POST | `/api/resume/upload` | Upload master resume | Yes | `uploadResumeFile` (no version name) | `UPDATE users` + recalc | 🔴 recalc NameError; no size cap |
| DELETE | `/api/resume` | Clear resume + scores | Yes | (no live button — dead `btn-delete-resume`) | `UPDATE users/applications` | ⚠️ live API, dead UI |
| GET,PUT | `/api/settings` | Get/update settings | Yes | `load/saveUserSettings` (elements absent → silent) | `get/update_user_settings` | ⚠️ API live, UI absent |
| POST | `/api/account/change-password` | Change/set password | Yes | Dead (no form in DOM) | `UPDATE users.password_hash` | ⚠️ API live, UI absent |
| POST | `/api/account/delete` | Delete account + data | Yes | Dead (no trigger in DOM) | Deletes 5 tables + user, clears session | ⚠️ API live, UI absent |
| POST | `/api/profile/photo` | Upload avatar | Yes | Photo manager (`refine.js`) | `UPDATE users.avatar_url`, FS write | ✅ |
| GET | `/api/profile/photo/file` | Serve own avatar | Yes | Avatar `img` | FS read `user_{id}.*` | ✅ |
| DELETE | `/api/profile/photo` | Remove avatar | Yes | Photo remove btn | FS delete + `avatar_url=NULL` | ✅ |
| GET,POST | `/api/resume-versions` | List/create version | Yes | Versions page + fit dropdown | `SELECT/INSERT/UPDATE` | ✅ |
| DELETE | `/api/resume-versions/<id>` | Delete version | Yes | Versions page | `DELETE WHERE id AND user_id` | ✅ |
| POST | `/api/fit-score/select-version` | Activate version (copies into users) | Yes | Version select | `SELECT` + `UPDATE users` + recalc | 🔴 recalc NameError |
| GET | `/api/analytics/resume-versions` | Per-version stats | Yes | Versions + analytics charts | `SELECT versions + apps`, Python agg | ✅ |
| POST | `/api/fit-score/analyze` | AI fit analysis | Yes | FIT Analyze btn | `SELECT app + resume` | ✅ (needs resume+key) |
| GET | `/auth/google/gmail/connect` | Start Gmail OAuth (or mock) | Yes | Connect btn | `save_email_connection` (mock path) | ✅; 🎭 mock when placeholder ID |
| GET | `/auth/google/gmail/callback` | OAuth callback | Yes | Google redirect | `save_email_connection` | ✅; requires live session |
| GET | `/api/email-intelligence/status` | Connection status | Yes | EI page poll | `SELECT email_connections` | ✅ |
| POST | `/api/email-intelligence/disconnect` | Remove connection | Yes | Disconnect modal | `DELETE connection` | ✅ |
| POST | `/api/email-intelligence/sync` | Pull 30 Gmail → classify/match/upsert | Yes | Sync btn | `upsert_email_message`, sync stamp | ✅; 🎭 fake success on mock token |
| GET | `/api/email-intelligence/emails` | List (limit 60, job-related) | Yes | EI list | `get_email_messages` | ✅ |
| GET | `/api/email-intelligence/stats` | Counts by classification | Yes | EI stat cards | `SELECT email_messages` agg | ✅ |
| POST | `/api/email-intelligence/confirm-match` | Link email→app + status/date update | Yes | Confirm btn | `UPDATE applications + email_messages` + timeline | ✅ |
| POST | `/api/email-intelligence/ignore-match` | Mark ignored | Yes | Ignore btn | `UPDATE match_status=ignored` | ✅ |
| POST | `/api/email-intelligence/add-calendar-event` | Set interview/assessment date | Yes | Email modal | `UPDATE applications` + timeline | ✅; no date validation |
| POST | `/api/email-intelligence/draft-reply` | Groq draft or fallback template | Yes | Draft btn | `SELECT email + user` | ✅ |

Verification per endpoint: all exist (route decorators found); all above marked ✅ called have a `fetch`/form/redirect caller; validation = manual (required/status/extension/magic/ids); auth = `login_required` on all except 5 public; authz = `user_id` in every object query (no IDOR found); responses = JSON or redirect; errors = 400/404/409/401/429/502 with `error/error_type/retryable`; real DB except mock Gmail paths.

Missing: resume download, cover-letter/certificates, interview rounds, push notification center, pagination params, public API docs. Unused: none (all endpoints have callers except `DELETE /api/resume`, settings/password/delete which lack UI but are callable). Duplicates: `PUT` vs `PATCH` same handler (intentional); `GET /applications` vs `GET /` overlap. Inconsistent contracts: bare `/applications*` vs `/api/*`; `POST /applications` accepts JSON *or* form; bulk returns `success=(fail==0)` partial semantics.

---

# 6. DATABASE DEEP ANALYSIS

## 6.1 Tables

**`users`** — PK `id`, UNIQUE `username,email,google_id`, NOT NULL `email`. Fields: `password_hash` (nullable → Google-only), `avatar_url,full_name,phone,location,headline,university,grad_year,resume_text,resume_filename,created_at`. Indexes: PK+3 UNIQUE only. Relationships: parent of all.

**`user_settings`** — PK `user_id` (FK CASCADE). Fields: `notify_followup/interview/email_notifications/show_stats/warnings/interview_dates` (INT bool, default 1), `reminder_time='1 Day Before'`, `theme='light'`, `dashboard_view='kanban'`, `card_density='comfortable'`. Auto-created on register (`create_user`) and lazily in `get_user_settings`.

**`resume_versions`** — PK `id`, `user_id NOT NULL`, `version_name NOT NULL`, `created_at`. Runtime-added: `filename,resume_text` (migration). No UNIQUE(user,version_name) — dup handled in code (update-in-place, 200). FK CASCADE (unenforced).

**`applications`** — PK `id`, `user_id NOT NULL`, `company_name,job_title NOT NULL`, `status CHECK(Applied|Interviewing|Offered|Rejected) NOT NULL`, `date_applied,last_updated NOT NULL`, `notes,last_email_sent,interview_date,deadline_date,assessment_date,followup_date,job_url,salary,location,job_type='Full-time',last_interview_reminder_sent,last_assessment_reminder_sent,fit_score,missing_skills(JSON text),resume_version,archived(0/1),archived_at` (many via migration). FK CASCADE (unenforced). No index on `(user_id,status,archived)`.

**`email_connections`** — PK `id`, `user_id UNIQUE NOT NULL`, `provider='google'`, `email_address NOT NULL`, `access_token NOT NULL`, `refresh_token,token_expiry,last_synced_at,sync_cursor,created_at` + migration `granted_scopes,status,last_error`. Plaintext tokens.

**`email_messages`** — PK `id`, `user_id NOT NULL`, `message_id UNIQUE NOT NULL`, `thread_id,sender_name/email,subject,snippet,body_text,body_html_sanitized,received_at,classification,confidence_score,extracted_data(JSON),matched_application_id(SET NULL),match_confidence,match_status='pending',is_action_required,created_at` + migration `gmail_labels,source_folder,has_unsubscribe,is_bulk,is_job_related,exclude_reason`. No index on `(user_id,classification,match_status)`.

**`application_timeline_events`** — PK `id`, `application_id NOT NULL (CASCADE)`, `user_id NOT NULL (CASCADE)`, `event_type,title NOT NULL`, `description,event_date NOT NULL,source='MANUAL',email_id,created_at`. No index on `(application_id,user_id)`.

## 6.2 ER (only real FKs; all unenforced)

```
User (1)
 ├─ (N) Applications [user_id]
 │    ├─ (N) TimelineEvents [application_id + user_id]
 │    └─ (N) EmailMessages.matched_application_id [SET NULL]
 ├─ (N) ResumeVersions [user_id]
 ├─ (1) UserSettings [user_id PK]
 ├─ (1) EmailConnections [user_id UNIQUE]
 └─ (N) EmailMessages [user_id] + (N) TimelineEvents [user_id]
```

## 6.3 Integrity / normalization issues

* FKs never enforced → orphan timeline/emails possible if deletes bypass helpers; app code does manual deletes correctly but DB won't guarantee it.
* Denormalization: activating a resume version copies text into `users.resume_text`; `missing_skills` JSON text; `gmail_labels` CSV; `extracted_data` JSON. Acceptable for SQLite but no validation at DB level.
* `archived`, avatar, resume-version file cols added only by migration — fresh `schema.sql` lacks them (works because `init_db` always migrates, but schema file alone is incomplete).
* Missing indexes: list queries (`WHERE user_id`, calendar/analytics full scans) will degrade with history; email stats scan.
* Duplicate data risk: no DB-level dedup except `message_id UNIQUE`, `user/email UNIQUE`; app-level duplicate check is case-insensitive Python (race-prone, no constraint).
* `close_db` never registered as `teardown_appcontext` → connection leak under threads.
* Three `.db` files on disk with one canonical path → stale-data confusion; `check_users.py` hardcodes relative path.
* No migrations framework; `try/except pass` hides real ALTER failures.

---

# 7. AUTHENTICATION & SECURITY AUDIT

| Area | Finding | File:Line | Severity |
|---|---|---|---|
| Secrets committed | `.env` contains live `SECRET_KEY, GOOGLE_CLIENT_ID/SECRET, MAIL_USERNAME/PASSWORD(app pw), GROQ_API_KEY, OPENROUTER_KEY` | `.env:2-19` | 🔴 |
| Weak default secret | `or 'dev-secret-key-job-tracker'` session forgery if env missing | `config.py:8` | 🔴 |
| DEBUG default ON + `debug=True` | Debugger + reloader in prod | `config.py:10`, `app.py:53` | 🔴 |
| JWT verify bypass | `if placeholder client_id: jwt.decode(verify_signature=False)` — forged Google token login | `routes/auth.py:107-110` | 🔴 |
| Gmail mock auth | Fake `dev_mock_access_token` connection + fake sync success | `routes/email_intelligence.py:57-67,191-198` | 🟠 (demo backdoor if placeholder leaks to prod) |
| Open redirect | `next=request.args.get('next') or index`, no validation | `routes/auth.py:56` | 🟠 |
| OAuth state unverified | `state=user_id` sent but never checked on callback; callback requires login session (breaks if expired) | `services/gmail_service.py` + `email_intelligence.py:72-` | 🟠 |
| Plaintext OAuth tokens | `access/refresh_token` stored raw in SQLite | `email_connections` | 🟠 |
| No CSRF | No `flask-wtf`/token on any form/fetch | All routes | 🟠 |
| No rate limit | Login/signup/chat/autofill/sync unlimited → brute-force/LLM cost abuse | All | 🟠 |
| Weak password policy | `len>=6` only; no email format check; error leak `f"Google login failed: {str(e)}"`, Groq body slice | `auth.py:62-,150-151`, `chatbot.py` | 🟡 |
| Google-only pw set w/o current | By design but note: `if password_hash set verify else skip` | `profile.py:185-` | 🟡 |
| Delete account w/o confirm | No password re-check; irreversible cascade | `profile.py:218-` | 🟡 |
| SSRF | `parse_url_job_details(url)` fetches arbitrary URL (no allowlist/size cap, 5s timeout, Chrome UA) | `applications.py:791-, parse fn` | 🟠 |
| File upload | Resume: ext-check only, **no size cap** (DB bloat/DoS) + NameError path. Photo: good (ext+magic+2MB+safe name) but under `instance/` (not gitignored, ephemeral on Vercel) | `profile.py:126-,276-` | 🟠/🟡 |
| SQLi | Safe: all queries `?`-bound; f-strings only fixed fragments/allowlisted cols | `database/db.py`, routes | ✅ |
| IDOR / cross-user | Safe: every object query includes `AND user_id=?`; photo serves own only; tests verify 404 cross-user | Routes + `test_routes.py` | ✅ |
| XSS | Jinja autoescape + `escapeHtml` in refine + chatbot sanitizer (regex strip, not parser — must still render `body_html_sanitized` carefully) | `chatbot.py`, `refine.js`, `gmail_service.sanitize` | 🟡 |
| CORS/CSRF/session flags | No CORS config; session uses defaults (no `Secure/HttpOnly/SameSite/PERMANENT_LIFETIME`) | `app.py`, `config.py` | 🟡 |
| Missing dep | `import jwt` but `PyJWT` not in requirements | `auth.py:108`, `requirements.txt` | 🟡 |
| CORS problems (frontend-backend) | None — same-origin (no separate frontend host); hardcoded API URLs none (all relative `fetch('/api/...')`) | `app.js`, `refine.js` | ✅ |

**Critical question — can user A access user B's apps?** No, by code: all app/timeline/email/version/photo queries filter `user_id` from session; cross-user PUT/DELETE return 404 and are covered by `test_routes.py`. No endpoint takes a `user_id` param.

---

# 8. MOCK / FAKE DATA AUDIT

```text
File: services/../database/db.py:41-42
What is fake/mock: INSERT OR IGNORE users(1,'demo','demo@example.com')
Where used: Legacy migration only when user_id column newly added
Should be: Remove after one-time migration; harmless if left (OR IGNORE)
```
```text
File: routes/auth.py:107-110
What is fake/mock: jwt.decode(token, verify_signature=False) when GOOGLE_CLIENT_ID placeholder
Where used: POST /auth/google local testing with dummy token
Should be: Require real verify_oauth2_token; refuse login when placeholder in prod
```
```text
File: routes/email_intelligence.py:57-67
What is fake/mock: save_email_connection(dev_candidate@gmail.com, dev_mock_access_token, ...)
Where used: GET /auth/google/gmail/connect without real client ID → redirect ?connected=true
Should be: Show "Gmail not configured" error instead of fake connected state
```
```text
File: routes/email_intelligence.py:190-198
What is fake/mock: if access_token=='dev_mock_access_token': return {scanned 0, new 0}
Where used: POST /api/email-intelligence/sync on mock connection (fake success toast)
Should be: 400 NOT_CONNECTED when mock token
```
```text
File: static/js/theme-3d.js:initLiveActivityFeed (activities[4])
What is fake/mock: Looping feed Google/Applied, Microsoft/Interview, Amazon/Follow-up, Startup AI/Offer every 2.4s
Where used: Landing hero #animatedFeedContainer
Should be: Marketing placeholder — keep on landing only; never in dashboard (dashboard uses real data)
```
```text
File: templates/landing.html: hero + 4 feature mockups
What is fake/mock: Ghost pills (Interviewing, Offered 91%), stats (100%/1-Click/24/7), Google URL extract card, Qwen chips, funnel 48/18/6, Aug 2026 calendar grid
Where used: Landing page visuals only
Should be: Label as illustrative or replace with product screenshots
```
```text
File: templates/dashboard.html: profile placeholders
What is fake/mock: Placeholder text Dhayanandham R, +1 555-0192, headline, Stanford, 2026
Where used: input placeholder= attributes only (not values)
Should be: Fine; replace with generic examples if desired
```
```text
File: static/js/app.js + refine.js fallbacks
What is fake/mock: Display defaults Company/Software Engineer/Remote/Competitive/Full-time, CHAT_PLACEHOLDERS[5], welcome prompts, Master Resume (Default), email/timeline emoji icons
Where used: Empty-state/fallback rendering
Should be: Keep (not mock data)
```
```text
File: tests/* (all mock_*, patch(), fake_urlopen, gsk_test_mock_key)
What is fake/mock: Mocked SMTP_SSL, urllib.urlopen, Groq responses, HTTPError objects
Where used: Test isolation only
Should be: Keep; never ships to prod
```
```text
File: .env (live secrets) + config.py:8,11 defaults
What is fake/mock: Placeholder GOOGLE_CLIENT_ID 'YOUR_GOOGLE...' triggers mock paths above
Where used: Dev fallback
Should be: Fail closed in prod (refuse mock when ENV==production)
```

No `TODO/FIXME` strings in code. `OPENROUTER_API_KEY` in `.env` is unused (dead env). `components/*` are unused prototypes, not runtime mocks.

---

# 9. FRONTEND ↔ BACKEND CONNECTION AUDIT

* Transport: same-origin `fetch` with relative URLs — no hardcoded hosts, no CORS issues. Verified ~30 call sites; all methods match route decorators; all payload shapes match server parsers (`{credential}`, `{url}`, `{message,history}`, `{version_name}+multipart`, `{action,ids,status}`, `{message_id,application_id,new_status}`, etc.).
* Mismatches found:
  * `toggleNotesPanel/saveNotes` → `PUT /applications/<id>{notes}` but no `#notes-*` DOM → dead caller, live endpoint.
  * `DELETE /api/resume`, `GET/PUT /api/settings`, `POST /api/account/*` live but no DOM → callable via console, unreachable via UI.
  * `switchView('settings')` → `#view-settings` absent → blank.
  * `deadline_date/followup_date` sent by nothing usefully (server forces None) — contract silently drops fields.
  * `GROQ_MODEL` default inconsistent (`config.py: qwen-2.5-32b-it` vs `chatbot.py: openai/gpt-oss-120b` fallback chain) — same key, different behavior per caller.
* Error handling: CRUD/autofill failures `alert()`; profile/photo/resume inline `.save-status-text`; fit/email structured `error_type/retryable` panels + `showToast` (refine) — but `app.js` paths that `alert` lose `retryable` semantics.
* Auth mismatch: browser `GET /applications` unauthed → redirect (not 401) per decorator; `fetch` JSON unauthed → 401. Correct.
* Broken endpoints: none (all fetched URLs exist). Wrong methods: none. CORS: N/A (same origin).

---

# 10. ERROR HANDLING

* Backend: `400` validation (required/status/version/ids/file), `401` auth, `404` ownership, `409` duplicate, `429/403/502` Gmail/Groq mapped via `GmailApiError` + model fallback loop; SMTP/scheduler swallow to `0`/log; migrations `try/except pass`. Structured `{success,error,error_type,retryable}` on fit/email/chat.
* Frontend: loading disables (`Extracting.../Syncing.../Saving.../Thinking...`); empty states for upcoming/follow-ups/columns/calendar/fit/versions/email-dashboard; error via `alert` (CRUD), inline status (profile/photo/resume), alert-box + toast (email), skeleton→error card with Retry (fit, honors `retryable`).
* Silent-failure risks: (1) `except pass` migrations hide ALTER errors; (2) `if #setting-*` guards silently skip settings saves; (3) `fit-score` status els absent → updates lost; (4) `deadline/followup` silently nulled; (5) `bulk BULK_STATUS_UPDATE` timeline exc swallowed; (6) scheduler exceptions logged only; (7) `body_html_sanitized` regex sanitizer may miss vectors — render with caution; (8) `close_db` never called → exhausted handles under load manifest as random 500s.

---

# 11. STATE MANAGEMENT

No global store. Module-scope vars + DOM as source of truth:

| Scope | Mechanism | Examples |
|---|---|---|
| Server state | `fetch` per view, no cache except `emailIntelligenceCache`, `cachedCalendarEvents` | Calendar/Analytics refetch on `switchView`; no SWR/dedup/invalidation |
| Global UI | `switchView()` + `localStorage:app-theme ↔ body[data-theme]` | 6 views, theme persists; `?view=` deep-link + `history.replaceState` cleanup |
| Local | Per-widget vars: `calendarCurrentDate`, `calendarFilterState`, `chatHistory/activeChatContext/lastUserMessage`, `selectedIds:Set`, `currentSelectedCalendarDate` | Chat history (last 6 sent), bulk selection pruned on `kanban:changed` |
| Persistence | `localStorage` theme only; session cookie server-side; resume/avatar/DB server-side | No offline/cache; reload loses chat/bulk/calendar filters |
| Trace example (status) | `select→optimistic DOM move→PUT→recalculateCounters→kanban:changed→refine prunes selection` | No rollback on failure except `alert`+reload |

Gaps: no optimistic create (reload), no pagination cache, polling could duplicate calendar/analytics fetches, chat context (`activeChatContext`) cleared only manually.

---

# 12. PERFORMANCE AUDIT

* **No pagination on core lists** — `SELECT * FROM applications WHERE user_id ORDER BY` + `fetchall()` on `GET /`, `/api/calendar-events`, `/api/analytics`, `GET /applications` (`applications.py:77,135,219,268`); `SELECT * resume_versions`, all active apps in `chatbot.py:41`. Only bounded: `email_messages LIMIT 60`, Gmail `max 30`, resume `LIMIT 1`, chat history `LIMIT 10`. Concrete impact: user with 500 apps downloads full `notes/resume` JSON on every dashboard/analytics/calendar/chat call.
* **O(n) Python aggs per poll** — calendar expands 1 app → 4 events; analytics recomputes rates; versions analytics joins in Python. No caching/ETag.
* **N+1:** not classic per-row queries, but timeline + email lookups are extra round-trips per modal open.
* **Missing indexes:** no index on `applications(user_id,status,archived)`, `email_messages(user_id,classification)`, `timeline(application_id)`.
* **Blocking I/O:** `urllib` Groq/Gmail + `SMTP_SSL` synchronous in request thread (Groq 6-12s timeouts, SMTP 10s); autofill/chat/sync buttons can hang workers.
* **Frontend:** `filterCards`/`matchesCalendarFilters` run on every keystroke (no debounce); calendar renders max 2 pills + `+N` (good); `MutationObserver` KPI count-up + rAF scramble are throttled (good); `hero_bg.jpg` referenced but disabled by theme-3d (wasted asset); `login_hero.png` full-bleed unoptimized.
* **Scheduler:** scans all stale/event apps hourly in one thread; no batching/limit.

---

# 13. CLOUD READINESS AUDIT

| Layer | Current (local) | Cloud replacement | Notes |
|---|---|---|---|
| Database | SQLite file `database/tracker.db` (+2 orphan copies) | Neon/Supabase/RDS Postgres (via SQLAlchemy/psycopg) | **Must-move for any multi-instance/Vercel prod** — ephemeral FS loses data; needs ORM rewrite + migrations |
| File storage | `instance/uploads/avatars/` local FS; resume text in DB | S3/R2/Supabase Storage (avatars + resume PDFs) | Avatars break on serverless scale-out; resume blobs bloat SQLite |
| Auth | Session cookie + Google One-Tap (own code) | Keep sessions w/ server store (Redis) or Clerk/Auth0/Supabase Auth | Current works single-instance; needs `Secure/HttpOnly/SameSite`, secret rotation |
| Backend | `app.run(debug=True)` local; `vercel.json` serverless (legacy style) | Vercel (fix `functions/rewrites`, add cron) or Render/Fly/Cloud Run + gunicorn | No Dockerfile; scheduler won't run on Vercel (needs cron/queue) |
| Background jobs | In-process daemon thread | Vercel Cron / Cloud Scheduler + queue (RQ/Celery/Upstash) | Stale + event reminders need external trigger on serverless |
| Notifications | Gmail SMTP (`MAIL_*`) direct | Resend/SES/SendGrid + queue + templates | SMTP creds in `.env`; no retry/templates |
| Secrets | `.env` file (committed secrets locally) | Vercel/Cloud secret manager + `DATABASE_URL`, no defaults | Rotate all leaked keys immediately |
| Monitoring | `print/log` only; no APM/health/metrics | Sentry + healthz + structured logs + uptime | No `/healthz`, no request IDs |

Do not lift-and-shift SQLite to Vercel without Postgres — data loss is certain.

---

# 14. CLOUD INTEGRATION OPPORTUNITIES (ranked; no rewrite required first)

| # | Opportunity | Tech value | User value | Complexity | Depends on |
|---|---|---|---|---|---|
| 1 | Managed Postgres (Neon/Supabase) + connection string | High (durability, concurrency) | High (no data loss) | Medium (ORM + migrate) | Secrets mgmt |
| 2 | Secret manager + rotate leaked keys + `SECRET_KEY` | High (closes 🔴) | High (account safety) | Low | — |
| 3 | Object storage for avatars/resumes (S3/R2) | High (serverless-safe) | Medium | Low-Med | DB URL refs |
| 4 | Transactional email (Resend/SES) + queue | Med-High (deliverability, retry) | High (reminders arrive) | Low-Med | Cron/queue |
| 5 | Cron for scheduler (Vercel Cron / Scheduler) | High (reminders on serverless) | High | Low | Email provider |
| 6 | Sentry + health endpoint + structured logs | Med (debug prod) | Med (uptime) | Low | — |
| 7 | AI upgrades (Groq key mgmt, resume parsing already local, fit cache) | Med | High (fit/chat quality) | Low (cache scores) | Secrets |
| 8 | Gmail incremental sync (historyId/watch push) | Med | Med (fresh inbox) | Med | Queue |
| 9 | Analytics warehouse (export apps → BigQuery) | Low-Med | Low-Med | Med | Postgres first |
| 10 | Backup/PITR + export CSV | Med | Med | Low | Postgres first |

---

# 15. DEPLOYMENT AUDIT

* `requirements.txt`: unpinned `>=`, missing `PyJWT` (needed by `auth.py:108`), missing prod server (`gunicorn/waitress`). `pip install` works locally but prod image non-reproducible.
* `vercel.json`: legacy `builds+routes` (deprecated; use `functions+rewrites`). All traffic → `app.py` works; `app = create_app()` at import is Vercel-compatible; scheduler correctly disabled when `VERCEL=1` (so reminders silently stop in prod).
* Env: `SECRET_KEY` fallback, `DEBUG` default True, `GOOGLE_REDIRECT_URI` localhost default — all must be overridden in prod. `DATABASE_PATH` supported but SQLite ephemeral on Vercel.
* Build: no Dockerfile/compose/CI/Actions/Procfile. `app.run(debug=True)` hardcoded.
* **Can it deploy today?** Yes to Vercel as demo (with data-loss + no-reminders caveats) after setting env vars. No to durable prod (needs Postgres, secrets rotation, gunicorn, cron, storage). Exact blockers: (1) SQLite ephemeral, (2) leaked/default secrets, (3) `DEBUG=True`, (4) missing PyJWT/gunicorn pins, (5) scheduler dead on serverless, (6) local avatar FS.

---

# 16. TESTING AUDIT

* Corpus: **~66 tests in 9 files** (`unittest` + `pytest`), tempfile SQLite per test, mocked SMTP/Groq/Gmail. Docs claim 21/9 — stale.
* Tested: auth + isolation, CRUD + bulk + photo, stale/event reminders + dedup, calendar/analytics, autofill/employer regressions, profile/settings/password/delete, resume versions + fit (mocked Groq) + authoritative-score enforcement, email heuristics/match/full-flow + error parsing.
* Not tested: real OAuth/Gmail sync/pagination/refresh, real Groq/SMTP delivery, scheduler thread (skipped), frontend JS/templates (beyond substring), duplicate-user/weak-pw/session-expiry/rate-limit/XSS, corrupt PDF/DOCX, concurrent uploads, legacy migrations, `instance/uploads` cleanup, `archived` filter coverage, error leakage.
* Broken: none syntactically; fragilities: `TestConfig` class vs dict inconsistency (`test_routes.py:18-23` loses defaults), `user_id==1` assumption, redundant `init_db()`, date-hardcoded strings, template-wording-coupled asserts.
* Critical gaps: security (CSRF/IDOR fuzz/SSRF), performance (pagination/load), prod paths (Vercel/scheduler).

---

# 17. DOCUMENTATION AUDIT

| Doc claim | Actual | Verdict |
|---|---|---|
| `README: 21 tests (4 files)` / `summary: 9/9 in test_routes` | ~66 tests in 9 files | ❌ stale |
| `README: Qwen 3.8 / qwen3.8-27b` | `config: qwen-2.5-32b-it`; chatbot fallbacks `openai/gpt-oss-120b, qwen3.8-27b, compound` | ❌ inconsistent |
| `summary: structure (applications/auth, 2 services, style/app.js, 3 templates)` | 7 route modules, 6 services, 3 JS, 3 CSS, 7 templates | ❌ omits chatbot/profile/versions/email/fit + all new tests |
| `summary: minimal users/applications schema` | 7 tables + many migrated cols | ❌ incomplete |
| `README: 14-endpoint API table` | ~40 endpoints (bulk/photo/resume/fit/email/timeline missing) | ❌ incomplete |
| `summary: pip install flask pytest dotenv google-auth` | Needs also `requests,pypdf,python-docx` (+implicit PyJWT) | ❌ incomplete |
| `README: 24h + 7-day reminders` | Correct | ✅ |
| `summary: only 7-day` | Omits 24h | ❌ incomplete |
| `README mermaid: database/tracker.db` | Correct canonical path | ✅ |

Do not trust README/summary for counts, models, structure, schema, or install.

---

# 18. ARCHITECTURE DIAGRAM (actual only)

```
Browser (Jinja HTML + vanilla JS: app.js / refine.js / theme-3d.js)
  │  same-origin fetch (relative /applications*, /api/*) + form POSTs + GSI
  ▼
Flask app.py:create_app()
  ├─ before_app_request: load_logged_in_user (session[user_id] → g.user)
  ├─ login_required gate (401 JSON vs redirect /welcome)
  ├─ Blueprints (no prefix):
  │    auth (/welcome,/login,/signup,/auth/google,/logout)
  │    applications (/, /applications*, /api/calendar-events, /api/analytics,
  │                  /api/autofill-url, /api/applications/bulk, timeline)
  │    chatbot (/api/chat) ──► Groq API (urllib, 4-model fallback)
  │    fit_analysis (/api/fit-score/analyze) ──► groq_service
  │    profile (/api/profile,resume*,settings,account*,photo) ──► pypdf/docx, FS
  │    resume_versions (/api/resume-versions*, fit-score/select, analytics)
  │    email_intelligence (Gmail OAuth/callback, /api/email-intelligence/*)
  │         ├─► Google OAuth2 + Gmail REST (gmail_service)
  │         ├─► classifier (email_classifier_service: relevance/classify/extract/match)
  │         └─► Groq draft-reply (or fallback template)
  ├─ Services:
  │    email_service (SMTP_SSL Gmail) ◄── scheduler thread (3600s, off on Vercel/TESTING)
  │    resume_context (active resume = users else latest version)
  ▼
SQLite database/tracker.db (raw sqlite3, Row; FKs unenforced; no custom indexes)
  users / user_settings / resume_versions / applications /
  email_connections(plaintext tokens) / email_messages / application_timeline_events

External (runtime): Groq LLM, Google OAuth/Gmail, Gmail SMTP
Local FS: instance/uploads/avatars/user_{id}.*
No: ORM, cache, queue, object store, Docker, CI, APM
```

---

# 19. DATA FLOW DIAGRAMS

## Application creation
```
Add modal (#add-app-form) [+ autofill POST /api/autofill-url → fetch URL → Groq/heuristics → fill]
 → POST /applications {company,job,...} → login_required → duplicate LOWER() check → 409? duplicate modal : INSERT + timeline(APPLICATION_SUBMITTED) → 201 JSON → location.reload() → GET / regroup
```

## Application status update
```
Card select.onchange → optimistic move (#cards-*) + pill/accent swap + recalculateCounters + kanban:changed
 → PUT /applications/<id>{status} → ownership check → UPDATE → JSON → (fail: alert+reload)
Bulk: select stale/all → POST /api/applications/bulk{action,ids} → per-id loop → partial updated/failed → move/remove + toast
```

## Interview creation
```
Add/edit date inputs OR email modal Add-to-Calendar → POST /api/email-intelligence/add-calendar-event{app_id,date,type}
 → UPDATE interview/assessment_date + timeline(INTERVIEW|ASSESSMENT_SCHEDULED) → upcoming section + GET /api/calendar-events → calendar pills + selected-date summary + modal
```

## Document upload (resume/avatar)
```
Resume: dropzone → POST /api/resume/upload|/api/resume-versions (multipart, pdf/docx/txt) → extract_text_from_file → UPDATE users(+version) → recalculate (🔴 NameError) → preview + fit badges
Avatar: photo input → client ext/size check + FileReader preview → POST /api/profile/photo (magic+2MB) → instance/uploads/avatars/user_{id}.ext + avatar_url → cache-bust ?t= → header/profile imgs
```

## Dashboard analytics
```
GET / (server grouped/counts/upcoming/followups) → switchView('analytics') → GET /api/analytics (full-scan agg) → kpi-*/funnel-*/donut-* + MutationObserver count-up
 + GET /api/analytics/resume-versions → per-version cards/bars
```

---

# 20. COMPLETE FILE-BY-FILE MAP

| File | Purpose | Main Functions | Dependencies | Used By | Status |
|---|---|---|---|---|---|
| `app.py` | Factory, DB init, scheduler, blueprints | `create_app`, `inject_user_context` | `config.Config`, `database.db.init_db`, all blueprints, `scheduler` | Vercel/`__main__` | ✅ |
| `config.py` | Env config + defaults | `Config` | `dotenv`, `.env` | `app.py` | ⚠️ weak defaults |
| `database/db.py` | SQLite access + migrations + helpers | `get_db/close_db(unwired)/init_db`, `get/create/update_user*`, `delete_user_account`, email conn/msg, timeline | `sqlite3`, `schema.sql` | All routes | ✅; leak + `pass` hides errors |
| `database/schema.sql` | 7-table DDL | — | — | `init_db` | ⚠️ missing migrated cols |
| `routes/auth.py` | Session + Google auth | `login_required`, `welcome/login/signup/google_auth/logout` | `db`, `werkzeug.security`, `google-auth`, `jwt` | Templates, GSI, all guards | ✅; bypass+redirect issues |
| `routes/applications.py` | Apps CRUD + calendar/analytics/autofill/bulk/timeline | `index`, `calendar/analytics/list/autofill/create/get/timeline/update/delete/bulk`, `parse_url_job_details`, `format_application_row` | `db`, `groq_service`, `urllib` | Dashboard, calendar, fit, email | ✅; dead deadline fields + SSRF |
| `routes/chatbot.py` | AI chat | `chat POST /api/chat` | `db`, `resume_context`, Groq via `urllib` | Chat widget | ✅ |
| `routes/profile.py` | Profile/resume/settings/account/photo | `extract_text_from_file`, `recalculate(NAMEERROR)`, 11 endpoints | `db`, `pypdf/docx` | Profile/fit/photo | 🔴 recalc broken; settings UI dead |
| `routes/resume_versions.py` | Version CRUD + activate + analytics | `get/create/delete`, `select-version`, `analytics` | `db`, `profile.extract`, `applications.format` | Versions/fit/analytics | 🔴 recalc path broken |
| `routes/fit_analysis.py` | AI fit detail | `analyze_fit` | `db`, `resume_context`, `groq_service.analyze` | FIT Analyze | ✅ |
| `routes/email_intelligence.py` | Gmail OAuth + ingest + actions | `connect/callback/status/disconnect/sync/emails/stats/confirm/ignore/calendar/draft` | `db`, `gmail_service`, `classifier`, Groq | EI view, dashboard mini | ✅; mock paths |
| `services/groq_service.py` | LLM fit + validation | `compute_fit_score`, `analyze_resume_fit`, `validate_fit_analysis` | Groq API | Apps/fit/chat | ✅ |
| `services/gmail_service.py` | Google/Gmail REST | `auth_url/exchange/refresh/verify/list/detail/parse/sanitize`, `GmailApiError` | Google APIs | EI sync | ✅; regex sanitize |
| `services/email_service.py` | SMTP reminders | `send_followup/event`, `process_stale/events` | `smtplib`, `db` | Scheduler | ✅ |
| `services/email_classifier_service.py` | Heuristic+AI classify/match | `relevance/classify/classify_extract/match`, extractors | Groq (optional) | EI sync | ✅ |
| `services/resume_context.py` | Active resume | `get_active_resume`, `build_resume_brief` | `db` | Chat/fit | ✅ |
| `services/scheduler.py` | Daemon loop | `start_email_scheduler` | `email_service` | `app.py` | ✅ local only |
| `templates/base.html` | Auth shell | — | `style/theme-3d/refine.css`, `app/theme-3d/refine.js` | `dashboard.html` | ✅ |
| `templates/dashboard.html` | Whole app (6 views + modals + chat) | — | Partials, `g.user` | `GET /` | ✅; no settings view |
| `templates/landing/login/signup.html` | Public/auth pages | — | GSI, `theme-3d.js` | `/welcome/login/signup` | ✅ |
| `templates/partials/*` | Card components | — | `format_application_row` fields | Dashboard loops | ✅ |
| `static/js/app.js` | Core client logic | CRUD/calendar/analytics/chat/profile/fit/email + `switchView` | All JSON APIs | `base/login/signup` | ✅; ~40% dead refs |
| `static/js/refine.js` | Toasts/preview/FIT popover/photo/bulk/hooks | `showToast`, `renderFitAnalysis`, bulk `selectedIds` | Same APIs | `base` | ✅ |
| `static/js/theme-3d.js` | Visual only | Parallax/scramble/feed/accordion/tilt/count-up | GSAP (landing) | `base/landing` | ✅; mock feed |
| `static/css/*` | 3-layer styles | — | `hero_bg.jpg` (disabled) | `base/landing/auth` | ✅ |
| `components/*.jsx/.css` | React prototypes | `JobCalendar/GradientMenu/ScrambledText/Highlighter/AccordionGallery` | None (unbundled) | Nobody | 🔵 dead |
| `tests/* (9)` | ~66 mocked tests | Per-file suites | `create_app(dict)`, tempfile DB | CI (none) | ✅; gaps §16 |
| `vercel.json` | Serverless route | — | `app.py` | Vercel | ⚠️ legacy style |
| `check_users.py` | Debug dump | — | `database/tracker.db` | Manual | 🔵 dev-only |
| `.env*`/`sample.env`/`.gitignore` | Env + ignores | — | `config.py` | Dev/deploy | 🔴 live secrets in `.env` |
| `README.md`/`summary.md` | Docs | — | — | Humans | 🔴 stale |

---

# 21. ISSUES & TECHNICAL DEBT

## 🔴 CRITICAL (security / data-loss / functionality blockers)

* **Live secrets in `.env`** — `SECRET_KEY, GOOGLE_* , MAIL_USER/PASS, GROQ_KEY` present. Impact: account/Gmail/LLM takeover. Fix: rotate all keys, purge from VCS history, move to secret manager. File: `.env:2-19`.
* **Weak default `SECRET_KEY` + `DEBUG True` + `debug=True`** — session forgery + debugger. Fix: require env, `DEBUG` default False, remove `debug=True`. Files: `config.py:8-10`, `app.py:53`.
* **Google JWT verify bypass** — `jwt.decode(verify=False)` when placeholder ID. Impact: forged login. Fix: refuse auth when placeholder; always verify. File: `routes/auth.py:107-110`.
* **`recalculate_user_fit_scores` NameError** — `compute_fit_score` unimported in `profile.py:100`; every resume upload/activate crashes after DB write. Fix: `from services.groq_service import compute_fit_score`. Files: `routes/profile.py:90-112`, callers `resume_versions.py:51,67,111`.
* **SQLite on ephemeral/serverless + 3 divergent `.db` files** — data loss/confusion. Fix: single canonical path now; Postgres before prod. Files: `config.py:9`, `database/*.db`, `instance/tracker.db`.
* **Missing `PyJWT` in requirements** — `import jwt` crashes fresh installs when placeholder path hit. Fix: add `PyJWT`. Files: `routes/auth.py:108`, `requirements.txt`.

## 🟠 HIGH (fix before production)

* Gmail mock backdoor + fake sync success (placeholder → fake connected). Files: `routes/email_intelligence.py:57-67,191-198`. Fix: fail closed.
* Open redirect `next`. File: `routes/auth.py:56`. Fix: allowlist `/`-relative.
* SSRF autofill (arbitrary URL fetch). File: `routes/applications.py:791-`. Fix: allowlist/SSR F-guard, size cap, timeout, no creds.
* No CSRF/rate-limit; plaintext OAuth tokens; OAuth `state` unverified. Fix: Flask-WTF/CSRF, Flask-Limiter, Fernet for tokens, verify state, short session lifetime + `Secure/HttpOnly/SameSite`.
* Resume upload no size cap (DB bloat). File: `routes/profile.py:126-`. Fix: 5-10MB cap + store blobs in object storage.
* `close_db` never wired → handle leak. Fix: `app.teardown_appcontext(close_db)`. Files: `database/db.py:16-`, `app.py`.
* FKs unenforced. Fix: `PRAGMA foreign_keys=ON` per connection + test orphans.
* Scheduler dead on Vercel (reminders stop silently). Fix: external cron + queue.
* `deadline_date/followup_date` silently nulled — user-visible data loss. Fix: accept/store or remove inputs. Files: `applications.py:831,833,940,946`.
* Account delete without confirmation; Google-only pw-set without current (document or gate). File: `profile.py:185-,218-`.

## 🟡 MEDIUM (arch / perf / maintainability)

* No pagination/indexes; full-table scans + large JSON; blocking Groq/SMTP in request thread. Fix: `LIMIT/OFFSET` + indexes `(user_id,status,archived)`, background queue, fit-score cache.
* Regex HTML sanitizer (not parser); error strings leak internals (`str(e)`, Groq slice). Fix: bleach allowlist, generic 500s + log IDs.
* XSS-adjacent: render `body_html_sanitized` in sandbox/escaped context; add CSP.
* Inconsistent `GROQ_MODEL` defaults; unused `OPENROUTER_KEY`; `sample.env` vs `.env.example` drift. Fix: single source.
* Settings/password/delete APIs live but UI absent → untestable paths + stale JS refs (`#setting-*`, `#view-settings`, notes/analytics-modal IDs). Fix: build settings view or remove dead JS.
* `vercel.json` legacy style; unpinned deps; no gunicorn/healthz/logging. Fix: modernize + pin + `/healthz`.
* Date-hardcoded tests; `TestConfig` class-vs-dict; template-coupled asserts. Fix: freeze time, unify fixture.
* Avatars under `instance/` (not gitignored, ephemeral). Fix: object storage + gitignore.

## 🔵 LOW (minor)

* `components/*` dead React (10 files) + `check_users.py` debug + `database/app.db` orphan + `hero_bg.jpg` disabled ref + `.analytics-modal-card` CSS remnants + `setAvatarImages` no-op + `style.css` old modal. Fix: delete or quarantine to `prototypes/`.
* Landing mock visuals + hero feed loop (marketing only — keep but label illustrative).
* Funnel/donut hand-rolled (fine); consider lib only if analytics grows.

---

# 22. CURRENT PROJECT SPECIFICATION (actual, not aspirational)

* **Product purpose:** Single-user job/internship tracker: kanban of applications, calendar, funnel analytics, resume FIT scoring, AI chat prep, Gmail intake, email reminders.
* **Target users:** Individual job seekers (students incl. internships). No teams/recruiters/admins.
* **Core features:** Auth (session+Google), CRUD + bulk + archive + duplicate guard + URL autofill, 4-status lifecycle + timeline, interview/assessment dates + upcoming + calendar + tomorrow/stale email reminders, resume upload/versions + FIT heuristic + AI analysis + version analytics, chatbot with app/email/resume context, Gmail OAuth + sync/classify/match/confirm/ignore/draft-reply/calendar, profile/photo/settings APIs, landing/marketing page.
* **User roles:** One role (owner of own data). No RBAC.
* **Application lifecycle:** `Applied → Interviewing → Offered | Rejected` (+`archived`). Dates parallel. Timeline is audit trail.
* **Data model:** 7 SQLite tables (§6); resumes denormalized; emails linked by `matched_application_id`; tokens plaintext.
* **Backend arch:** Flask monolith, blueprints-as-controllers, services for externals, raw SQL, daemon scheduler, no cache/queue.
* **Frontend arch:** Jinja SSR + vanilla JS multi-view page (`switchView`), optimistic status/bulk, full reload on create/edit/delete, toast/modal patterns, layered CSS.
* **API arch:** Mixed HTML+JSON, no versioning/docs, manual validation, session auth, per-query authz, structured errors on AI/email paths.
* **Authentication:** Session cookie + Google One-Tap (with bypass caveat); pbkdf2 hashing; no 2FA/verify/reset.
* **Storage:** SQLite file + local avatar FS + resume text in DB.
* **Integrations:** Groq LLM (chat/fit/autofill/draft), Google OAuth/Gmail read, Gmail SMTP send. No S3/payments/analytics/APM.
* **Deployment:** Local `app.run(debug)` or Vercel serverless (legacy config); no Docker/CI; env-file secrets.
* **Current limitations:** 4 statuses only; no rounds/cover-letters/offers store/resume download/team/sharing/pagination/offline/push; deadlines dead; resume recalc crashes; reminders stop on Vercel; settings UI missing; docs stale; secrets leaked; scale capped by SQLite + sync I/O.

---

# 23. FINAL PROJECT MATURITY ASSESSMENT

```text
Frontend:        Partial
Backend:         Partial
Database:        Partial
Authentication:  Partial
Security:        Incomplete
Testing:         Partial
Deployment:      Incomplete
Cloud readiness: Incomplete
```

* **Frontend Partial:** Dashboard/kanban/calendar/analytics/chat/email/fit/versions all render and call live APIs, but settings/password/delete views absent, ~40% JS refs dead, no tests, no bundler/types, search un-debounced.
* **Backend Partial:** ~40 endpoints work with ownership checks, but resume recalc crashes, deadline fields dead, mock Gmail paths fake success, SSRF/CSRF/limit gaps, blocking I/O, no pagination.
* **Database Partial:** 7-table model + migrations work, but FKs unenforced, indexes missing, schema file incomplete, 3 divergent files, leak on teardown.
* **Authentication Partial:** Login/signup/Google/session/isolation work and are tested, but verify bypass, open redirect, no verify/reset/2FA/rate-limit/session flags.
* **Security Incomplete:** Leaked secrets + weak defaults + DEBUG + bypass + SSRF + plaintext tokens + no CSRF/limiter + ephemeral prod = not prod-safe.
* **Testing Partial:** ~66 mocked tests cover happy paths + isolation + reminders + classifier, but no frontend/E2E/prod-path/security/perf tests; fragile fixtures.
* **Deployment Incomplete:** Vercel demo possible, but legacy config, unpinned/missing deps, no Docker/CI/health/logs, SQLite ephemeral, scheduler dead on serverless.
* **Cloud readiness Incomplete:** Everything local (SQLite, FS, thread, SMTP, `.env`); needs Postgres + storage + secrets + cron + email service before cloud prod.

---

# 24. WHAT I NEED TO KNOW BEFORE MODIFYING THIS PROJECT

1. **Flask Jinja monolith, no SPA framework** — `app.py:create_app()` + 7 blueprints; frontend is `dashboard.html` (1781L) + `app.js` (4046L). Don't add React; extend `switchView` + `fetch`.
2. **Canonical DB is `database/tracker.db` (SQLite, raw SQL)** — ignore `database/app.db` and `instance/tracker.db` orphans; FKs unenforced; no ORM.
3. **Auth = session cookie `user_id`, gate = `login_required`** — every object query must keep `AND user_id=?` (IDOR-safe today; keep it).
4. **Only 4 statuses exist** — `Applied|Interviewing|Offered|Rejected` (`CHECK`). Interviews are dates, not statuses. Don't assume `Saved/Assessment`.
5. **`deadline_date/followup_date` are dead** — server forces `None` (`applications.py:831,833,940,946`). Fix before using.
6. **`recalculate_user_fit_scores` is broken (NameError)** — `compute_fit_score` not imported in `profile.py`. Any resume work must fix import first.
7. **Resumes are text in DB; versions copy into `users.resume_text`** — no download endpoint; no cover-letter model.
8. **Avatars are local FS `instance/uploads/avatars/user_{id}.*`** — breaks on serverless; magic+2MB validated.
9. **Gmail has mock paths** — placeholder `GOOGLE_CLIENT_ID` → fake connected + fake sync. Don't trust EI without real creds.
10. **Groq key drives chat/fit/autofill/draft** — missing key = fallbacks/502s, not crashes (except recalc). Model defaults differ per caller.
11. **Scheduler is a daemon thread, off on Vercel/TESTING** — reminders stop in prod serverless; needs cron.
12. **API is mixed `/applications*` + `/api/*`, no versioning** — use relative `fetch`; `POST /applications` takes JSON or form; bulk is partial-success.
13. **Settings/password/delete APIs live but UI absent** — `#view-settings`, `#setting-*`, password/delete DOM don't exist; don't call dead JS.
14. **`components/*.jsx` are dead prototypes** — live calendar/nav/scramble/accordion are re-implemented in `app.js`/`theme-3d.js`/HTML. Don't wire JSX.
15. **Secrets are leaked in `.env`** — rotate before any deploy; `SECRET_KEY`/`DEBUG` defaults unsafe.
16. **No pagination/indexes; full scans + blocking I/O** — add `LIMIT/OFFSET` + indexes before scale; don't add chat/autofill in request hot path without queue.
17. **Docs are stale (counts/models/endpoints/schema)** — trust code (§17 table), not README/summary.
18. **Tests are ~66 mocked, no E2E/frontend** — run `pytest`; use tempfile pattern; `TestConfig` class-vs-dict quirk in `test_routes.py`.
19. **Deploy = Vercel legacy `vercel.json` demo only** — needs Postgres + secrets + storage + cron + gunicorn pins for real prod.
20. **Sanitize carefully** — regex HTML sanitizer + `escapeHtml` + Jinja autoescape; render Gmail HTML defensively + add CSP.

---

# AUDIT SUMMARY

1. **What it currently does:** Single-user tracker with kanban, calendar, funnel/donut analytics, resume FIT + versions, AI chat, Gmail intake/classify/match/draft, interview/stale email reminders, profile/avatar, landing page.
2. **Actually implemented:** ~40 endpoints across 7 blueprints; 7-table SQLite; session+Google auth with isolation; bulk/archive/duplicate-guard/autofill; timeline; photo/resume/versions; EI sync pipeline; scheduler (local); ~66 tests.
3. **Incomplete:** Settings/password/delete UI, resume download, cover-letters/rounds, pagination/indexes, prod deploy (Docker/CI/health/pins), cloud (Postgres/storage/cron/email), frontend tests.
4. **Broken:** Resume recalc `NameError`, deadline/followup forced None, `PyJWT` missing, `close_db` unwired, settings JS refs, scheduler on Vercel.
5. **Mocked:** Gmail connect/sync on placeholder ID, Google JWT bypass, landing hero/feed/feature visuals, profile placeholders, test-only SMTP/Groq mocks.
6. **Current architecture:** Jinja SSR + vanilla JS → Flask blueprints → services (Groq/Gmail/SMTP/classifier) → SQLite file + local avatar FS + thread scheduler.
7. **Current data flow:** Session → `login_required` → `WHERE user_id` queries → HTML/JSON → optimistic status / reload CRUD / lazy views; background thread → SMTP stamps.
8. **Security state:** Not prod-safe — leaked secrets, weak defaults, DEBUG, verify bypass, SSRF, no CSRF/limiter, plaintext tokens, open redirect, ephemeral store. Isolation (IDOR/SQLi) is correctly implemented.
9. **Deployment state:** Local + Vercel-demo only; blockers = SQLite ephemeral, secrets, DEBUG, deps, dead scheduler, local FS, legacy vercel config.
10. **Cloud opportunities:** Postgres → secrets rotation → storage → transactional email + cron → Sentry/health → AI cache → Gmail incremental → backup/analytics (ranked §14).
11. **Recommended next phases:** P0: rotate secrets, fix recalc + PyJWT + SECRET/DEBUG, fail-closed mocks, wire `close_db`, fix deadlines or remove inputs. P1: Postgres plan, storage, CSRF/limiter/session flags, token encryption, SSRF guard, pagination+indexes, cron+email service, settings UI or prune dead JS, modern vercel/pins/health. P2: tests (E2E/security/perf), docs refresh, remove dead `components`/orphan DBs, CSP, backup/export.

*End of report — all findings traced to source; no files modified during audit.*
