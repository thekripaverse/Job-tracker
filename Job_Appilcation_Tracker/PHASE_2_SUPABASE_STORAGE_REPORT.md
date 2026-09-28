# PHASE 2 — SUPABASE STORAGE REPORT

**Scope:** file storage → Supabase Storage only. NOT done (per instructions):
Auth/Realtime/Edge/Scheduler/deploy/Flask rewrite/React/Gmail/Groq changes.
No unrelated refactors; no local files deleted.

## 1. Existing file-storage architecture (audited, not assumed)

| FILE TYPE | Storage | Upload route | DB record | Download route | Delete route | Frontend usage |
|---|---|---|---|---|---|---|
| Avatar image | `instance/uploads/avatars/user_{id}.{jpg,png,webp}` | `POST /api/profile/photo` (ext allowlist + magic bytes + 2 MB) | `users.avatar_url` = `/api/profile/photo/file` | `GET /api/profile/photo/file` (own only) | `DELETE /api/profile/photo` | header/profile `<img>` via `avatar_url` |
| Resume binary | **nowhere** — parsed to text then discarded | `POST /api/resume/upload`, `POST /api/resume-versions` (pdf/docx/doc/txt/rtf/md, 8 MB) | `users.resume_text/filename`, `resume_versions.resume_text/filename` | **none existed** | `DELETE /api/resume`, `DELETE /api/resume-versions/<id>` (text only) | text preview + FIT/AI |
| Application docs / cover letters / certificates | **not implemented** | — | — | — | — | — |

## 2. Files discovered

- 1 avatar: `instance/uploads/avatars/user_7.jpg` (159,023 B, valid JPEG,
  owner `users.id=7` exists). Untouched (same size/mtime after phase).
- Resume binaries on disk: **0** (by architecture — text-only).
- Application documents: **none** (feature does not exist).

## 3. Storage bucket(s) created

None created by this phase (needs owner Dashboard click + service key, both
outside the repo). Code targets a single private bucket `job-tracker-files`
(override `SUPABASE_STORAGE_BUCKET`). Creation command documented in
`docs/SUPABASE_STORAGE.md` §1.

## 4. Storage path structure

`avatars/user-{id}/profile.{ext}` · `resumes/user-{id}/master/{file}` ·
`resumes/user-{id}/{version_id}/{file}` — all server-generated from session IDs
+ `secure_filename` basenames. `documents/` reserved but unused (no feature).

## 5. Files migrated

Live object migration is **ready but not executed**: it requires
`SUPABASE_SERVICE_ROLE_KEY`, which is absent from `.env` (verified — only
`DATABASE_URL` etc. present) and was deferred by owner choice ("verify later").
`--check` (credential-free) run offline: `TOTAL=1 MIGRATED=1(would) FAILED=0
SKIPPED=0` → `user_7.jpg` → `avatars/user-7/profile.jpg`; resumes correctly
reported as nothing-to-migrate. The script verifies by re-download byte-compare
before recording metadata, skips byte-identical objects on re-run, and never
deletes originals.

## 6. Files not migrated and why

- `user_7.jpg`: pending live run (no service key available in this environment).
- Resume binaries: do not exist (nothing to migrate; future uploads persist).
- Application documents: feature absent (per instructions, not added).

## 7. Database metadata changes

New nullable TEXT columns (idempotent `ADD COLUMN` both backends + both
schema files; verified present in SQLite): `users.avatar_storage_path`,
`users.resume_storage_path`, `resume_versions.storage_path`. Reused existing
`avatar_url`/`filename` columns where possible; no other schema change; all
reads tolerate pre-migration DBs missing the columns.

## 8. Files changed in repository

New: `services/storage_service.py`, `scripts/migrate_files_to_supabase.py`,
`tests/test_storage.py` (14 tests), `docs/SUPABASE_STORAGE.md`, this report.
Modified: `config.py` (SUPABASE_* env), `database/{db,schema.sql,
schema_postgres.sql}.py` (3 columns), `routes/profile.py` (avatar +
master-resume flows, `GET /api/resume/file`, storage-aware account cleanup),
`routes/resume_versions.py` (binary persist, `GET …/file`, binary cleanup),
`sample.env`/`.env.example` (placeholders). No frontend/JS changes needed
(contracts preserved).

## 9-11. Upload / download / delete implementation

Upload: Browser → Flask (auth, Phase-1.5/2 validation: ext allowlist,
executable blocklist, magic bytes for images, 2 MB avatars / 8 MB resumes) →
`storage_service.upload_file` (Supabase PUT upsert, else local; avatar local
writes keep the exact legacy layout) → metadata columns → same JSON contract
plus `storage_backend`. Resume binaries are best-effort (`storage_warning` on
outage; text flow never breaks). Download: owner-checked Flask routes return
bytes with correct MIME (404 legacy-text-only/missing, 503 outage); no public
or signed URLs by design. Delete: storage object first (missing tolerated,
outage → 503 with metadata kept), then metadata; account delete sweeps all
three path columns best-effort.

## 12. Ownership/security implementation

Session-derived `user_id` only (no path/ID/filename trusted); per-row
`user_id` filters on versions; serving endpoints take no target IDs;
traversal/absolute/foreign-bucket paths rejected; private bucket; service key
in server-side headers only, never logged/returned/rendered (verified by
grep); `.env` git-ignored; non-owner download → 404; manipulated `user_id`
or `../../` filenames neutralized (tested).

## 13-14. Tests performed / results

`tests/test_storage.py` — **14/14 passed** (offline, tmp instance dirs +
mocked Supabase REST): upload validation (5 cases), path builders/traversal,
local roundtrip/exists/delete/idempotency, legacy avatar read, Supabase
PUT/auth-headers/404+401 mapping with key-redaction assert, avatar
owner/cross-user/invalid flows, resume binary persist + version
traversal + non-owner 404 + owner delete + legacy-text 404, PDF persist.
Full regression: **95/95 passed** (81 prior + 14 new). Migration `--check`
executed for real (§5). Live-bucket assertions await the keyed run.

## 15. Migration statistics (actual)

| Measure | Value |
|---|---|
| LOCAL FILES TOTAL | 1 (avatar) + 0 resume binaries |
| MIGRATED (live objects) | 0 — pending service key (script ready, verified `--check`) |
| FAILED / SKIPPED | 0 / 0 |
| SUPABASE OBJECTS | 0 (bucket creation + keyed run pending) |
| DATABASE REFERENCES | 0 set (`user_7.avatar_storage_path` NULL — correct pre-migration) |
| ORPHAN OBJECTS / REFERENCES | 0 |

## 16. Remaining local-storage dependencies

Default backend is still local (`instance/uploads/` intact, served first when
unconfigured). By design until the keyed migration + verification completes;
rollback is byte-identical (proven by suite).

## 17. Known issues

1. Live migration/verification needs `SUPABASE_URL` + service key + private
   bucket (owner action; exact commands in docs §10).
2. Sandbox reaches Supabase REST over IPv4 but direct `db.*` only over IPv6 —
   unrelated to Storage REST, noted for ops.
3. No UI buttons for the two new resume-file download endpoints (API-only;
   frontend out of scope).
4. `GROQ_MODEL` drift (pre-existing, AI phase).

## 18. Recommended Phase 3

1. Owner: create bucket, set key, run keyed migration, confirm
   `avatar_storage_path` set + download works, then keep locals as backup.
2. Supabase Auth / Scheduler / deploy per roadmap — reusing the session-derived
   ownership and no-public-URL conventions established here.

**Not claimed:** live-bucket migration complete. Everything except the keyed
upload + bucket-side verification is implemented, tested (95/95), and
documented; locals preserved.
