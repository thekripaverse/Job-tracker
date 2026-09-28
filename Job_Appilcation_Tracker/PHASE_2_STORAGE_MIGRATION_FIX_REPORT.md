# PHASE 2 STORAGE MIGRATION FIX REPORT

## Root cause

`scripts/migrate_files_to_supabase.py:142` did
`from services.storage_service import PHOTO_MIMETYPES`, but that constant was
defined in `routes/profile.py:379` — the service only had `RESUME_MIMETYPES`.
The script was written against an assumed (older/planned) service API.
The 95/95 suite never caught it because the import sits in the **live-upload
branch**, which `--check` mode and all tests skip (tests cover the service's
own upload path, not the script's).

A second, latent failure surfaced on the live run: `SUPABASE_URL` held the
`postgresql://` database string instead of the `https://` Storage REST URL,
producing `URLError: unknown url type`. A scheme guard was added so this
misconfiguration fails closed with a warning instead of a cryptic error.

## Fix (3 small changes, no arch/schema/security changes)

1. `services/storage_service.py`: defined `PHOTO_MIMETYPES` (same 4 formats:
   png/jpg/jpeg/webp) as the single source of truth.
2. `routes/profile.py`: removed the local duplicate; now imports the constant
   from the service (verified `is`-identical object; upload/serve/delete paths
   unchanged).
3. `services/storage_service.py::supabase_url()`: rejects non-http(s) values
   with a warning (fail closed). `SUPABASE_URL` corrected to
   `https://sfiyouyzxzqlwvfibyrp.supabase.co` with owner approval
   (service key untouched, never printed).

## Tests

| TEST | RESULT | DETAIL |
|---|---|---|
| Full suite | **95/95 PASS** | No reduction from baseline |
| `--check` | PASS | `LOCAL avatar files: 1`, `user_7.jpg → avatars/user-7/profile.jpg [WOULD-MIGRATE]`, resumes correctly nothing-to-migrate |
| Live migration (1st) | PASS | `TOTAL=1 MIGRATED=1 FAILED=0 SKIPPED=0`, exit 0 |
| Live migration (2nd) | PASS | `MIGRATED=0 SKIPPED=1 [SKIP-already-migrated]`, exit 0 — no duplicate |

## Migration (actual numbers)

```text
Local avatar files: 1
Migrated:           1  (instance/uploads/avatars/user_7.jpg → avatars/user-7/profile.jpg)
Skipped:            0  (1st run) / 1 already-migrated (2nd run)
Failed:             0
Supabase objects:   1
Database references:1  (users.id=7.avatar_storage_path)
```

## Verification (all confirmed live)

- Object exists: `file_exists('avatars/user-7/profile.jpg')` → True.
- Bytes verified: remote 159,023 B == local 159,023 B, byte-identical.
- Metadata updated: `avatar_storage_path='avatars/user-7/profile.jpg'`;
  `avatar_url` and all other user fields untouched.
- No duplicate: single upsert path; 2nd run skipped.
- Local source preserved: `user_7.jpg` present, same size; nothing deleted.
- No test pollution: `instance/uploads/` contains only `avatars/user_7.jpg`.

## Remaining issues

- Bucket `job-tracker-files` must already exist (owner-created); the script
  does not create buckets.
- Resume binaries: none existed locally (text-only architecture) — future
  uploads persist via the Phase 2 routes; legacy text-only resumes still
  return the documented 404-with-guidance on file download.
- Direct `db.*` Postgres host is IPv6-only from some networks; Storage REST
  (IPv4) is unaffected. Unrelated to this fix.
