"""Supabase Storage abstraction with local-filesystem fallback (Phase 2).

Backend selection: Supabase Storage REST is used when SUPABASE_URL and
SUPABASE_SERVICE_ROLE_KEY are configured; otherwise the pre-existing local
layout under <instance>/uploads/ is used (same files as today, so rollback
and offline development keep working).

All storage paths are generated server-side from authenticated IDs — never
from browser input. Ownership is derived from the session user in routes;
this module only enforces path-shape safety (no traversal, single bucket).

The service-role key is used ONLY in server-side Authorization headers and is
never returned, logged, or rendered.
"""
import json
import logging
import os
import urllib.parse
import urllib.request

from flask import current_app
from werkzeug.utils import secure_filename

logger = logging.getLogger(__name__)

DEFAULT_BUCKET = 'job-tracker-files'

# Globally rejected extensions (executables / scripts / anything runnable).
BLOCKED_EXTS = {
    '.exe', '.bat', '.cmd', '.sh', '.msi', '.dll', '.so', '.dylib', '.dmg',
    '.jar', '.js', '.vbs', '.ps1', '.scr', '.com', '.pif', '.lnk', '.reg',
    '.apk', '.app', '.run', '.bin',
}

AVATAR_EXTS = {'.jpg', '.jpeg', '.png', '.webp'}
RESUME_EXTS = {'.pdf', '.docx', '.doc', '.txt', '.rtf', '.md'}

# Single source of truth for avatar MIME types (same 4 formats the app has
# always supported). routes/profile.py and scripts/migrate_files_to_supabase.py
# both reference this — do not duplicate it elsewhere.
PHOTO_MIMETYPES = {'.png': 'image/png', '.jpg': 'image/jpeg',
                   '.jpeg': 'image/jpeg', '.webp': 'image/webp'}

RESUME_MIMETYPES = {
    '.pdf': 'application/pdf',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.doc': 'application/msword',
    '.txt': 'text/plain',
    '.rtf': 'application/rtf',
    '.md': 'text/markdown',
}


class StorageError(Exception):
    """User-safe storage failure (message is safe to return in API errors)."""


# --------------------------------------------------------------------------
# Configuration / backend selection
# --------------------------------------------------------------------------
def _conf(key, default=''):
    try:
        return (current_app.config.get(key) or default)
    except RuntimeError:
        return os.environ.get(key, default)


def supabase_url():
    raw = (_conf('SUPABASE_URL', '').strip().rstrip('/'))
    if raw and not raw.lower().startswith(('https://', 'http://')):
        # Fail closed on wrong-kind values (e.g. a postgresql:// DATABASE_URL
        # pasted into SUPABASE_URL): never build storage requests from them.
        logger.warning('SUPABASE_URL is not an http(s) REST URL; Supabase Storage disabled.')
        return ''
    return raw


def storage_bucket():
    return (_conf('SUPABASE_STORAGE_BUCKET', '') or DEFAULT_BUCKET).strip()


def storage_enabled():
    """True only when Supabase Storage is fully configured (server-side)."""
    return bool(supabase_url() and _service_key())


def _service_key():
    try:
        key = (current_app.config.get('SUPABASE_SERVICE_ROLE_KEY') or '').strip()
    except RuntimeError:
        key = ''
    return key or os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '').strip()


def backend_name():
    return 'supabase' if storage_enabled() else 'local'


# --------------------------------------------------------------------------
# Server-side path builders (no user-controlled directories)
# --------------------------------------------------------------------------
def _clean_segment(value):
    cleaned = secure_filename(str(value))
    if not cleaned or cleaned in ('.', '..'):
        raise StorageError('Invalid storage identifier.')
    return cleaned


def avatar_storage_path(user_id, ext):
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        raise StorageError('Invalid storage identifier.')
    ext = str(ext).lower()
    if ext not in AVATAR_EXTS:
        raise StorageError('Unsupported avatar format.')
    return f'avatars/user-{uid}/profile{ext}'


def resume_storage_path(user_id, resume_id, filename):
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        raise StorageError('Invalid storage identifier.')
    if str(resume_id).lower() in ('master', 'default', '0'):
        rid = 'master'
    else:
        try:
            rid = str(int(resume_id))
        except (TypeError, ValueError):
            raise StorageError('Invalid storage identifier.')
    safe = _clean_segment(os.path.basename(filename or 'resume'))
    return f'resumes/user-{uid}/{rid}/{safe}'


def validate_storage_path(path):
    """Reject traversal / absolute / cross-bucket paths. Returns clean path."""
    if not path or not isinstance(path, str):
        raise StorageError('Invalid storage path.')
    p = path.strip().replace('\\', '/')
    if p.startswith('/') or '..' in p.split('/') or p.startswith('avatars/../'):
        raise StorageError('Invalid storage path.')
    parts = [seg for seg in p.split('/') if seg not in ('', '.')]
    if not parts or parts[0] not in ('avatars', 'resumes', 'documents'):
        raise StorageError('Invalid storage path.')
    if any(seg in ('..',) or len(seg) > 128 for seg in parts):
        raise StorageError('Invalid storage path.')
    return '/'.join(parts)


# --------------------------------------------------------------------------
# Upload validation (extension + size + executable block)
# --------------------------------------------------------------------------
def validate_upload(filename, data, allowed_exts, max_bytes):
    if not filename or not str(filename).strip():
        raise StorageError('No file selected for upload.')
    if data is None:
        raise StorageError('Could not read the uploaded file.')
    ext = os.path.splitext(str(filename))[1].lower()
    if not ext or ext in BLOCKED_EXTS:
        raise StorageError('This file type is not allowed.')
    if ext not in {e.lower() for e in allowed_exts}:
        raise StorageError('Unsupported file format.')
    if len(data) > max_bytes:
        raise StorageError(
            f'File is too large ({len(data) / 1048576:.1f} MB). '
            f'Maximum size is {max_bytes // 1048576} MB.'
        )
    return ext


# --------------------------------------------------------------------------
# Supabase Storage REST client (urllib — no new dependencies)
# --------------------------------------------------------------------------
def _sb_request(method, path, data=None, content_type=None, timeout=20):
    base = supabase_url()
    key = _service_key()
    if not base or not key:
        raise StorageError('Supabase Storage is not configured on this server.')
    url = f'{base}/storage/v1/object/{storage_bucket()}/{path}'
    headers = {'apikey': key, 'Authorization': f'Bearer {key}'}
    if content_type:
        headers['Content-Type'] = content_type
    if method in ('POST', 'PUT'):
        headers['x-upsert'] = 'true'
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise StorageError('File not found in storage.')
        if e.code in (401, 403):
            logger.error('Supabase Storage auth failed (key redacted)')
            raise StorageError('Storage service rejected the request.')
        logger.error('Supabase Storage HTTP %s on %s %s', e.code, method, path)
        raise StorageError('Storage operation failed. Please try again.')
    except urllib.error.URLError as e:
        logger.error('Supabase Storage unreachable: %s', type(e).__name__)
        raise StorageError('Storage service is unavailable. Please try again.')
    except TimeoutError:
        raise StorageError('Storage operation timed out. Please try again.')


def _sb_exists(path):
    base = supabase_url()
    key = _service_key()
    parent, _, name = path.rpartition('/')
    body = json.dumps({'prefix': parent + '/', 'limit': 100,
                       'search': name}).encode()
    url = f'{base}/storage/v1/object/list/{storage_bucket()}'
    req = urllib.request.Request(url, data=body,
                                 headers={'apikey': key,
                                          'Authorization': f'Bearer {key}',
                                          'Content-Type': 'application/json'},
                                 method='POST')
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            items = json.loads(resp.read().decode() or '[]')
        return any((it.get('name') == name) for it in items if isinstance(it, dict))
    except Exception:
        return False


# --------------------------------------------------------------------------
# Local backend (pre-existing layout — rollback identical)
# --------------------------------------------------------------------------
def _local_root():
    import tempfile
    # Vercel/serverless filesystems are ephemeral (and largely read-only
    # outside tmp): never touch instance/ there. Supabase is the persistent
    # store; local files on serverless are per-invocation scratch only.
    if os.environ.get('VERCEL') == '1':
        root = os.path.join(tempfile.gettempdir(), 'job-tracker-uploads')
    else:
        root = os.path.join(current_app.instance_path, 'uploads')
    os.makedirs(root, exist_ok=True)
    return root


def _local_fs_path(storage_path):
    """Map a storage path onto the local mirror. Legacy avatar files
    (avatars/user_<id>.<ext>) pre-date the user-{id}/ layout; they are
    resolved by _local_avatar_candidates(), not here."""
    clean = validate_storage_path(storage_path)
    full = os.path.abspath(os.path.join(_local_root(), *clean.split('/')))
    if not full.startswith(os.path.abspath(_local_root()) + os.sep):
        raise StorageError('Invalid storage path.')
    return full


def _local_avatar_candidates(user_id):
    """Legacy local avatar files for a user (rollback layout)."""
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return []
    found = []
    try:
        root = os.path.join(_local_root(), 'avatars')
    except Exception:
        return []
    for ext in ('.png', '.jpg', '.jpeg', '.webp'):
        cand = os.path.join(root, f'user_{uid}{ext}')
        if os.path.exists(cand):
            found.append(cand)
    return found


def read_avatar(user_id, storage_path):
    """Return (bytes, ext) for a user's avatar: storage object first
    (Supabase or local mirror), then legacy local files. Ownership comes
    from the caller's session user_id — never from the path."""
    if storage_path:
        try:
            data = download_file(storage_path)
            return data, os.path.splitext(storage_path)[1].lower()
        except StorageError as e:
            if 'not found' not in str(e).lower():
                raise
    for cand in _local_avatar_candidates(user_id):
        try:
            with open(cand, 'rb') as f:
                return f.read(), os.path.splitext(cand)[1].lower()
        except OSError:
            continue
    raise StorageError('No profile photo found.')


def delete_avatar(user_id, storage_path):
    """Delete every avatar copy for a user (storage object + legacy files).
    Returns True if anything was removed."""
    removed = False
    if storage_path:
        try:
            removed = delete_file(storage_path) or removed
        except StorageError as e:
            if 'not found' not in str(e).lower():
                raise
    for cand in _local_avatar_candidates(user_id):
        try:
            os.remove(cand)
            removed = True
        except OSError:
            pass
    return removed


# --------------------------------------------------------------------------
# Public operations (routes call these — never Supabase directly)
# --------------------------------------------------------------------------
def upload_file(storage_path, data, content_type='application/octet-stream'):
    clean = validate_storage_path(storage_path)
    if storage_enabled():
        status, _ = _sb_request('PUT', clean, data=data, content_type=content_type)
        if status not in (200, 201):
            raise StorageError('Storage upload failed. Please try again.')
        return clean
    # Production without Supabase Storage must fail clearly, never silently
    # persist user files to an ephemeral disk.
    try:
        prod = (current_app.config.get('ENV') or '') == 'production'
    except RuntimeError:
        prod = os.environ.get('ENV') == 'production'
    if prod:
        raise StorageError('File storage is not configured on this server.')
    full = _local_fs_path(clean)
    # Local backend keeps the pre-Phase-2 avatar layout (avatars/user_<id>.<ext>)
    # so rollback to the old code serves new uploads byte-identically.
    import re as _re
    m = _re.match(r'^avatars/user-(\d+)/profile(\.[a-z0-9]+)$', clean)
    if m:
        full = os.path.join(os.path.dirname(os.path.dirname(full)),
                            f"user_{m.group(1)}{m.group(2)}")
    os.makedirs(os.path.dirname(full), exist_ok=True)
    try:
        with open(full, 'wb') as f:
            f.write(data)
    except OSError:
        raise StorageError('Could not save the uploaded file.')
    return clean


def download_file(storage_path):
    clean = validate_storage_path(storage_path)
    if storage_enabled():
        status, body = _sb_request('GET', clean)
        if status != 200:
            raise StorageError('File not found in storage.')
        return body
    full = _local_fs_path(clean)
    if not os.path.exists(full):
        raise StorageError('File not found in storage.')
    try:
        with open(full, 'rb') as f:
            return f.read()
    except OSError:
        raise StorageError('Could not read the stored file.')


def delete_file(storage_path):
    clean = validate_storage_path(storage_path)
    if storage_enabled():
        try:
            _sb_request('DELETE', clean)
        except StorageError as e:
            if 'not found' in str(e).lower():
                return False
            raise
        return True
    full = _local_fs_path(clean)
    try:
        if os.path.exists(full):
            os.remove(full)
            return True
        return False
    except OSError:
        raise StorageError('Could not delete the stored file.')


def file_exists(storage_path):
    try:
        clean = validate_storage_path(storage_path)
    except StorageError:
        return False
    if storage_enabled():
        return _sb_exists(clean)
    return os.path.exists(_local_fs_path(clean))


def get_file_url(storage_path, expires_in=300):
    """No permanently public URLs for private files. Supabase signed URLs
    could be minted here in the future; currently returns None so all
    downloads flow through authenticated Flask routes (ownership-checked).
    Avatars are likewise served via /api/profile/photo/file."""
    validate_storage_path(storage_path)
    return None


def persist_resume_binary(user_id, resume_id, filename, data):
    """Best-effort binary persistence for a resume upload.

    Returns (storage_path | None, error | None). The extracted-text flow is
    the functional core and must never break because binary persistence
    failed — callers save text first, then record the path only on success.
    """
    import os as _os
    ext = _os.path.splitext(str(filename or ''))[1].lower()
    if ext not in RESUME_EXTS:
        return None, 'Unsupported resume format.'
    try:
        path = resume_storage_path(user_id, resume_id, filename)
        upload_file(path, data,
                    content_type=RESUME_MIMETYPES.get(ext, 'application/octet-stream'))
        return path, None
    except StorageError as e:
        logger.warning('Resume binary persistence failed: %s', type(e).__name__)
        return None, str(e)
