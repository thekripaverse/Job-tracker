# Supabase Storage Guide (Phase 2)

No real credentials appear in this document.

## 1. Supabase Storage setup

1. In the Supabase Dashboard → **Storage**, create a bucket named
   `job-tracker-files`. Recommended settings: **private** (not public),
   single bucket for all app files.
2. No folders need pre-creating — the app creates object paths on upload:
   `avatars/…`, `resumes/…`. (`documents/` is reserved; see §4.)
3. Copy the project URL (`https://<ref>.supabase.co`) and a
   **service-role** key (server-side only) into `.env` (never commit).

## 2. Bucket configuration

| Item | Value |
|---|---|
| Bucket | `job-tracker-files` (override: `SUPABASE_STORAGE_BUCKET`) |
| Visibility | Private — no public URLs are minted |
| Avatars | `avatars/user-{id}/profile.{jpg,png,webp}` |
| Master resume | `resumes/user-{id}/master/{filename}` |
| Version resume | `resumes/user-{id}/{version_id}/{filename}` |

## 3. Environment variables

```
SUPABASE_URL=https://<ref>.supabase.co
SUPABASE_STORAGE_BUCKET=job-tracker-files
SUPABASE_SERVICE_ROLE_KEY=<secret: .env / hosting secrets only>
```

Unset → local fallback under `instance/uploads/` (yesterday's layout for
avatars, mirrored layout for resumes). The key travels only in server-side
`Authorization: Bearer` headers; it is never logged, returned, or rendered.

## 4. Storage architecture

`services/storage_service.py` is the only module that talks to Supabase
(REST via stdlib `urllib`, no new dependencies) or the local mirror:

- `upload_file / download_file / delete_file / file_exists / get_file_url`
  (URL minting intentionally returns `None` — see §7)
- `avatar_storage_path / resume_storage_path` (server-generated),
  `validate_storage_path`, `validate_upload` (allowlist + executable blocklist
  + size), `read_avatar / delete_avatar` (storage-first, legacy-local fallback),
  `persist_resume_binary` (best-effort).
- Only `avatars/` and `resumes/` exist. **Application documents, cover
  letters, and certificates are not implemented by the app** — no `documents/`
  objects are created; do not add the feature here.

## 5. File path conventions

Paths derive from the session `user_id` (+ DB-owned version IDs) and
`secure_filename` basenames. Browser input can never choose directories or
buckets; `validate_storage_path` rejects traversal/absolute/foreign roots.

## 6. Upload flow

Browser → Flask (auth, ext + magic bytes + size: avatars 2 MB, resumes 8 MB)
→ `storage_service.upload_file` (Supabase upsert, else local) → DB metadata
(`users.avatar_storage_path`, `users.resume_storage_path`,
`resume_versions.storage_path`; `avatar_url` keeps `/api/profile/photo/file`)
→ JSON response (same contract + `storage_backend`).

## 7. Download flow

Browser → Flask (`GET /api/profile/photo/file`, `/api/resume/file`,
`/api/resume-versions/<id>/file`) → session ownership check (no ID params for
own files; version rows filtered by `user_id`) → `download_file` → bytes with
correct MIME. 404 for missing/legacy-text-only, 503 for storage outages.
Resumes/documents are never publicly linkable by design.

## 8. Delete flow

Ownership check → storage delete first → DB metadata cleared on success.
Missing objects are tolerated (idempotent); outages return 503 and keep
metadata so nothing is orphaned silently. Account deletion cleans all three
path columns best-effort before row delete.

## 9. Security model

Server-generated paths; session-derived ownership; no path/filename/ID
trusted from the browser; private bucket; no signed/public URLs; service key
server-only; `.env` git-ignored; executable types blocked globally.

## 10. Migration procedure

```
# 1. Boot once so Phase-2 columns exist (both backends, idempotent).
# 2. Discovery (no credentials needed):
python scripts/migrate_files_to_supabase.py --check
# 3. Live run (needs SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY):
python scripts/migrate_files_to_supabase.py
# Re-runs skip byte-identical objects; originals are never deleted.
```

Expected on this repo: 1 avatar (`user_7.jpg` → `avatars/user-7/profile.jpg`,
verified by re-download byte-compare); resumes report "no local binaries"
(text already in DB; binaries persist on next upload).

## 11. Rollback procedure

Unset `SUPABASE_URL` (and/or `SUPABASE_SERVICE_ROLE_KEY`) → local backend.
Local avatar writes use the exact pre-Phase-2 layout, so old code serves new
uploads. `instance/uploads/` is never deleted by the app or the script.

## 12. Troubleshooting

| Symptom | Fix |
|---|---|
| `Storage is not configured` / local backend in prod | Set both `SUPABASE_URL` and key; restart |
| `Storage service rejected the request` (401/403) | Wrong/rotated service key; check Dashboard |
| `unavailable / timed out` (503 to user) | Network or Supabase incident; metadata kept, retry |
| Direct DB host unresolvable but app works | REST (`https://<ref>.supabase.co`) is IPv4; direct `db.*` may be IPv6-only — use the pooler for psql, REST is unaffected |
| Legacy resume has no download | Pre-Phase-2 uploads are text-only → 404 guidance; re-upload stores the binary |
