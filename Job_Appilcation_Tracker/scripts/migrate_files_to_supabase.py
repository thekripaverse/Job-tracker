"""Migrate local files (avatars) to Supabase Storage. READ-ONLY on originals.

Usage:
  set SUPABASE_URL=https://<ref>.supabase.co
  set SUPABASE_SERVICE_ROLE_KEY=<service-role key>   # server-side only
  python scripts/migrate_files_to_supabase.py [--check] [--bucket NAME]

  --check works WITHOUT Supabase credentials: it lists local files,
  ownership matches, and what would happen (discovery only).

Behavior:
  - Discovers instance/uploads/avatars/user_<id>.<ext>, matches each to a
    users.id row (ownership), validates magic bytes, uploads to
    avatars/user-<id>/profile<ext>, verifies by re-download + byte compare,
    then records users.avatar_storage_path.
  - Resume binaries: the app never stored them locally (text-only in DB), so
    there is nothing to migrate — reported explicitly, never fabricated.
  - Idempotent: skips when avatar_storage_path is set and the object exists
    with identical bytes. Safe to re-run / resume after failure.
  - NEVER deletes local originals (rollback copies stay in place).
  - Prints FILE/OWNER/TYPE/SOURCE/DESTINATION/STATUS report. No credentials printed.

Requires the Phase 2 schema columns (avatar_storage_path); the Flask app's
init_db() creates them automatically on next boot, or run this script after
booting the app once with the same DATABASE_URL.
"""
import argparse
import glob
import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
except Exception:
    pass

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

REPORT = []


def log(f, owner, typ, src, dst, status):
    REPORT.append((f, owner, typ, src, dst, status))
    print(f'{f:28} {owner:>6} {typ:8} {src} -> {dst}  [{status}]')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true', help='Dry run: list only.')
    ap.add_argument('--bucket', default=None)
    args = ap.parse_args()

    url = os.environ.get('SUPABASE_URL', '').strip()
    key = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '').strip()
    if not args.check and (not url or not key):
        print('ERROR: set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY first.', file=sys.stderr)
        sys.exit(2)
    if url:
        os.environ['SUPABASE_URL'] = url
    if key:
        os.environ['SUPABASE_SERVICE_ROLE_KEY'] = key
    if args.bucket:
        os.environ['SUPABASE_STORAGE_BUCKET'] = args.bucket

    from flask import Flask
    from config import Config
    from database.db import init_db
    app = Flask(__name__)
    app.config.from_object(Config)
    with app.app_context():
        init_db()
        from services import storage_service as store
        from database.db import get_db
        if not args.check:
            assert store.storage_enabled(), 'storage backend did not activate'
        print(f'backend={"supabase" if store.storage_enabled() else "local-discovery"} '
              f'bucket={store.storage_bucket()} check={args.check}')

        db = get_db()
        try:
            user_ids = {r['id'] for r in
                        db.execute('SELECT id FROM users').fetchall()}
        except Exception as e:
            print(f'ERROR reading users: {type(e).__name__}', file=sys.stderr)
            sys.exit(1)

        base = os.path.join(app.instance_path, 'uploads', 'avatars')
        files = sorted(glob.glob(os.path.join(base, 'user_*.*')))
        print(f'LOCAL avatar files: {len(files)}')

        migrated = failed = skipped = 0
        for path in files:
            name = os.path.basename(path)
            stem, ext = os.path.splitext(name)
            ext = ext.lower()
            try:
                uid = int(stem.split('_', 1)[1])
            except (IndexError, ValueError):
                log(name, '?', 'avatar', path, '-', 'SKIP-unmatched-name')
                skipped += 1
                continue
            if uid not in user_ids:
                log(name, uid, 'avatar', path, '-', 'SKIP-no-such-user')
                skipped += 1
                continue
            if ext not in store.AVATAR_EXTS:
                log(name, uid, 'avatar', path, '-', 'SKIP-bad-ext')
                skipped += 1
                continue
            with open(path, 'rb') as f:
                raw = f.read()
            # Magic-byte ownership-independent validity check.
            magic_ok = (raw[:8] == b'\x89PNG\r\n\x1a\n' or raw[:2] == b'\xff\xd8'
                        or (raw[:4] == b'RIFF' and raw[8:12] == b'WEBP'))
            if not magic_ok or len(raw) < 12:
                log(name, uid, 'avatar', path, '-', 'SKIP-invalid-image')
                skipped += 1
                continue
            dest = store.avatar_storage_path(uid, '.jpg' if ext == '.jpeg' else ext)
            row = db.execute('SELECT avatar_storage_path FROM users WHERE id = ?',
                             (uid,)).fetchone()
            current = None
            try:
                current = row['avatar_storage_path']
            except (KeyError, IndexError, TypeError):
                pass
            if current == dest and not args.check:
                try:
                    back = store.download_file(dest)
                    if back == raw:
                        log(name, uid, 'avatar', path, dest, 'SKIP-already-migrated')
                        skipped += 1
                        continue
                except store.StorageError:
                    pass  # fall through to re-upload
            if args.check:
                log(name, uid, 'avatar', path, dest, 'WOULD-MIGRATE')
                migrated += 1
                continue
            try:
                from services.storage_service import PHOTO_MIMETYPES
                ext_key = dest[dest.rfind('.'):].lower()
                store.upload_file(dest, raw,
                                  content_type=PHOTO_MIMETYPES.get(ext_key,
                                                                   'application/octet-stream'))
            except store.StorageError as e:
                log(name, uid, 'avatar', path, dest, f'FAIL-{type(e).__name__}')
                failed += 1
                continue
            try:
                back = store.download_file(dest)
                if back != raw:
                    log(name, uid, 'avatar', path, dest, 'FAIL-verify-mismatch')
                    failed += 1
                    continue
            except store.StorageError:
                log(name, uid, 'avatar', path, dest, 'FAIL-verify-unreadable')
                failed += 1
                continue
            db.execute('UPDATE users SET avatar_storage_path = ? WHERE id = ?',
                       (dest, uid))
            db.commit()
            log(name, uid, 'avatar', path, dest, 'MIGRATED')
            migrated += 1

        print('\nResumes: no local binaries exist (app stores extracted text + '
              'filename in DB only) — nothing to migrate, nothing fabricated.')
        print(f'\nTOTAL={len(files)} MIGRATED={migrated} FAILED={failed} SKIPPED={skipped}')
        print('Local originals preserved (nothing deleted).')
        sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
