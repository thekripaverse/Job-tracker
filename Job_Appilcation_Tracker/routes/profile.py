import os
import io
import json
import pypdf
import docx
from flask import Blueprint, request, jsonify, session, g
from werkzeug.security import generate_password_hash, check_password_hash
from routes.auth import login_required
from services.groq_service import compute_fit_score
from database.db import (
    get_db, 
    get_user_by_id, 
    update_user_profile, 
    get_user_settings, 
    update_user_settings, 
    delete_user_account
)

profile_bp = Blueprint('profile', __name__)

@profile_bp.route('/api/profile', methods=['GET'])
@login_required
def get_profile():
    user_id = session['user_id']
    user = get_user_by_id(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    
    user_dict = dict(user)
    user_dict.pop('password_hash', None)
    return jsonify(user_dict)

@profile_bp.route('/api/profile', methods=['PUT'])
@login_required
def update_profile():
    user_id = session['user_id']
    data = request.get_json() or request.form
    
    updated_user = update_user_profile(user_id, data)
    user_dict = dict(updated_user)
    user_dict.pop('password_hash', None)
    return jsonify({'success': True, 'user': user_dict})

def extract_text_from_file(file_storage):
    from flask import current_app
    filename = file_storage.filename or ''
    ext = os.path.splitext(filename)[1].lower()

    file_bytes = file_storage.read()
    # Upload-size guard (Phase 1.5 P15): default 8 MB. Protects the DB/file
    # growth path shared by master-resume upload and resume-version creation.
    max_bytes = int(current_app.config.get('MAX_RESUME_MB', 8)) * 1024 * 1024
    if len(file_bytes) > max_bytes:
        raise ValueError(
            f"File is too large ({len(file_bytes) / 1048576:.1f} MB). "
            f"Maximum resume size is {max_bytes // 1048576} MB."
        )
    file_stream = io.BytesIO(file_bytes)
    
    extracted_text = ''
    
    if ext == '.pdf':
        try:
            reader = pypdf.PdfReader(file_stream)
            page_texts = []
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    page_texts.append(text)
            extracted_text = '\n'.join(page_texts)
        except Exception as e:
            raise ValueError(f"Error parsing PDF file: {str(e)}")
            
    elif ext in ['.docx', '.doc']:
        try:
            doc = docx.Document(file_stream)
            full_text = []
            for para in doc.paragraphs:
                if para.text:
                    full_text.append(para.text)
            for table in doc.tables:
                for row in table.rows:
                    row_text = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                    if row_text:
                        full_text.append(' | '.join(row_text))
            extracted_text = '\n'.join(full_text)
        except Exception as e:
            raise ValueError(f"Error parsing Word document: {str(e)}")
            
    elif ext in ['.txt', '.rtf', '.md']:
        try:
            extracted_text = file_bytes.decode('utf-8', errors='ignore')
        except Exception as e:
            raise ValueError(f"Error reading text file: {str(e)}")
    else:
        raise ValueError("Unsupported file format. Please upload a PDF (.pdf), Word document (.docx), or text (.txt) file.")

    return filename, extracted_text.strip()

def recalculate_user_fit_scores(user_id, resume_text):
    db = get_db()
    apps = db.execute('SELECT * FROM applications WHERE user_id = ?', (user_id,)).fetchall()
    
    for app in apps:
        if not resume_text:
            db.execute('UPDATE applications SET fit_score = NULL, missing_skills = NULL WHERE id = ?', (app['id'],))
            continue
        jd_text = f"{app['company_name']} {app['job_title']}. {app['notes'] or ''}"
        try:
            fit_score, missing_skills = compute_fit_score(jd_text, resume_text)
            missing_skills_json = json.dumps(missing_skills) if missing_skills else None
            
            db.execute('''
                UPDATE applications
                SET fit_score = ?, missing_skills = ?
                WHERE id = ? AND user_id = ?
            ''', (fit_score, missing_skills_json, app['id'], user_id))
        except Exception as e:
            print(f"Error computing fit score for app {app['id']}: {e}")
    
    db.commit()
    return len(apps)

@profile_bp.route('/api/resume', methods=['GET'])
@login_required
def get_resume():
    user_id = session['user_id']
    user = get_user_by_id(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    return jsonify({
        'resume_text': user['resume_text'] or '',
        'resume_filename': user['resume_filename'] or ''
    })

@profile_bp.route('/api/resume/upload', methods=['POST'])
@login_required
def upload_resume():
    user_id = session['user_id']
    file_storage = request.files.get('resume_file') or request.files.get('file')
    
    if not file_storage or not file_storage.filename:
        return jsonify({'error': 'No file selected for upload.'}), 400
        
    try:
        filename, extracted_text = extract_text_from_file(file_storage)
    except ValueError as ve:
        return jsonify({'error': str(ve)}), 400
    except Exception as err:
        return jsonify({'error': f'Failed to process file: {str(err)}'}), 500
        
    if not extracted_text:
        return jsonify({'error': 'Could not extract text from this document. If it is a PDF, please ensure it contains selectable text (not a scanned image) or try a Word (.docx) file.'}), 400

    db = get_db()
    # Re-read raw bytes for binary persistence (extract_text_from_file consumes the stream).
    try:
        file_storage.stream.seek(0)
        raw_binary = file_storage.stream.read()
    except Exception:
        raw_binary = None

    db.execute('UPDATE users SET resume_text = ?, resume_filename = ? WHERE id = ?', (extracted_text, filename, user_id))
    db.commit()

    # Phase 2: persist the original binary alongside the extracted text.
    # Best-effort: text is the functional core; a storage outage must not
    # break resume upload. Status is reported explicitly.
    storage_path, storage_error = None, None
    if raw_binary:
        from services import storage_service as store
        storage_path, storage_error = store.persist_resume_binary(
            user_id, 'master', filename, raw_binary)
        if storage_path and 'resume_storage_path' in _user_columns(db):
            db.execute('UPDATE users SET resume_storage_path = ? WHERE id = ?',
                       (storage_path, user_id))
            db.commit()

    # Recalculate fit scores for user applications
    apps_updated = recalculate_user_fit_scores(user_id, extracted_text)

    resp = {'success': True, 'filename': filename, 'resume_text': extracted_text,
            'apps_updated': apps_updated}
    if storage_path:
        from services import storage_service as store
        resp['storage_path'] = storage_path
        resp['storage_backend'] = store.backend_name()
    elif storage_error:
        resp['storage_warning'] = storage_error
    return jsonify(resp)

@profile_bp.route('/api/resume', methods=['DELETE'])
@login_required
def delete_resume():
    from services import storage_service as store
    user_id = session['user_id']
    db = get_db()
    # Best-effort binary cleanup; text clearing always proceeds.
    try:
        cols = _user_columns(db)
        user = get_user_by_id(user_id)
        if user and 'resume_storage_path' in cols:
            try:
                old_path = user['resume_storage_path']
            except (KeyError, IndexError, TypeError):
                old_path = None
            if old_path:
                try:
                    store.delete_file(old_path)
                except store.StorageError:
                    pass
    except Exception:
        pass
    db.execute('UPDATE users SET resume_text = NULL, resume_filename = NULL WHERE id = ?', (user_id,))
    try:
        if 'resume_storage_path' in _user_columns(db):
            db.execute('UPDATE users SET resume_storage_path = NULL WHERE id = ?', (user_id,))
    except Exception:
        pass
    db.execute('UPDATE applications SET fit_score = NULL, missing_skills = NULL WHERE user_id = ?', (user_id,))
    db.commit()
    return jsonify({'success': True})


@profile_bp.route('/api/resume/file', methods=['GET'])
@login_required
def download_resume():
    """Download the owner's original master-resume binary (Phase 2).

    Resumes uploaded before Phase 2 have extracted text only and no stored
    binary → 404 with guidance. Ownership from session (no ID parameter)."""
    from flask import Response
    from services import storage_service as store
    user_id = session['user_id']
    user = get_user_by_id(user_id)
    if not user:
        return jsonify({'error': 'User not found.'}), 404
    try:
        storage_path = user['resume_storage_path']
        filename = user['resume_filename'] or 'resume'
    except (KeyError, IndexError, TypeError):
        storage_path, filename = None, 'resume'
    if not storage_path:
        return jsonify({'error': 'No stored resume file for this account. '
                                 'Upload a resume to download the original.'}), 404
    try:
        data = store.download_file(storage_path)
    except store.StorageError as e:
        msg = str(e).lower()
        if 'unavailable' in msg or 'timed out' in msg or 'rejected' in msg:
            return jsonify({'error': str(e)}), 503
        return jsonify({'error': 'Stored resume file not found.'}), 404
    ext = os.path.splitext(filename)[1].lower()
    mimetype = store.RESUME_MIMETYPES.get(ext, 'application/octet-stream')
    return Response(data, mimetype=mimetype,
                    headers={'Content-Disposition': f'attachment; filename="{filename}"'})

@profile_bp.route('/api/settings', methods=['GET'])
@login_required
def get_settings_route():
    user_id = session['user_id']
    settings = get_user_settings(user_id)
    return jsonify(settings)

@profile_bp.route('/api/settings', methods=['PUT'])
@login_required
def update_settings_route():
    user_id = session['user_id']
    data = request.get_json() or request.form
    
    updated = update_user_settings(user_id, data)
    return jsonify({'success': True, 'settings': updated})

@profile_bp.route('/api/account/change-password', methods=['POST'])
@login_required
def change_password():
    user_id = session['user_id']
    data = request.get_json() or request.form
    
    current_pw = data.get('current_password', '')
    new_pw = data.get('new_password', '')
    confirm_pw = data.get('confirm_password', '')
    
    if not current_pw or not new_pw:
        return jsonify({'error': 'Please provide current and new password.'}), 400
    
    if new_pw != confirm_pw:
        return jsonify({'error': 'New passwords do not match.'}), 400
        
    if len(new_pw) < 6:
        return jsonify({'error': 'New password must be at least 6 characters long.'}), 400
        
    user = get_user_by_id(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404
        
    if user['password_hash'] and not check_password_hash(user['password_hash'], current_pw):
        return jsonify({'error': 'Incorrect current password.'}), 400
        
    new_hash = generate_password_hash(new_pw)
    db = get_db()
    db.execute('UPDATE users SET password_hash = ? WHERE id = ?', (new_hash, user_id))
    db.commit()
    
    return jsonify({'success': True, 'message': 'Password updated successfully.'})

@profile_bp.route('/api/account/delete', methods=['POST'])
@login_required
def delete_account():
    user_id = session['user_id']
    data = request.get_json(silent=True) or request.form
    # Explicit confirmation required: prevents accidental/CSRF-adjacent
    # deletion and guarantees intent. Ordinary profile edits are unaffected.
    if data.get('confirm') != 'DELETE':
        return jsonify({'error': 'Account deletion requires explicit confirmation '
                                 '(send {"confirm": "DELETE"}).'}), 400
    user = get_user_by_id(user_id)
    if not user:
        return jsonify({'error': 'User not found.'}), 404
    # Phase 2: remove the user's storage objects first (best-effort; a sealed
    # account row delete must not leave orphaned private objects behind).
    _delete_user_storage_objects(user_id)
    _remove_existing_avatars(user_id)
    delete_user_account(user_id)
    session.clear()
    return jsonify({'success': True, 'redirect': '/welcome'})


def _delete_user_storage_objects(user_id):
    """Best-effort cleanup of a user's Supabase/local objects on account delete."""
    from services import storage_service as store
    db = get_db()
    paths = []
    try:
        cols = _user_columns(db)
        user = get_user_by_id(user_id)
        if user:
            for key in ('avatar_storage_path', 'resume_storage_path'):
                try:
                    if key in cols and user[key]:
                        paths.append(user[key])
                except (KeyError, IndexError, TypeError):
                    pass
        try:
            rows = db.execute('SELECT storage_path FROM resume_versions WHERE user_id = ?',
                              (user_id,)).fetchall()
            for r in rows:
                try:
                    if r['storage_path']:
                        paths.append(r['storage_path'])
                except (KeyError, IndexError, TypeError):
                    pass
        except Exception:
            pass
    except Exception:
        pass
    for p in paths:
        try:
            store.delete_file(p)
        except Exception:
            pass
    try:
        store.delete_avatar(user_id, None)
    except Exception:
        pass


# --------------------------------------------------------------------------
# Profile photo management (upload / replace / remove / serve).
# Phase 2: binaries live in Supabase Storage (or the local fallback) behind
# services.storage_service; avatar_url keeps holding the serving endpoint path
# (API contract unchanged; remote OAuth URLs from Google sign-in unaffected).
# users.avatar_storage_path records the storage object; NULL means legacy
# local file under instance/uploads/avatars/ (still served as fallback).
# --------------------------------------------------------------------------
PHOTO_ALLOWED_EXTS = {'.jpg', '.jpeg', '.png', '.webp'}
PHOTO_MAX_BYTES = 2 * 1024 * 1024  # 2 MB (unchanged from Phase 1.5)

# Canonical MIME map lives in services.storage_service (single source of truth).
from services.storage_service import PHOTO_MIMETYPES


def _avatar_dir():
    from flask import current_app
    upload_dir = os.path.join(current_app.instance_path, 'uploads', 'avatars')
    os.makedirs(upload_dir, exist_ok=True)
    return upload_dir


def _avatar_path(user_id):
    for ext in ('.png', '.jpg', '.jpeg', '.webp'):
        candidate = os.path.join(_avatar_dir(), f"user_{user_id}{ext}")
        if os.path.exists(candidate):
            return candidate
    return None


def _remove_existing_avatars(user_id):
    """Remove legacy local avatar files (rollback copies / pre-Phase-2)."""
    for ext in ('.png', '.jpg', '.jpeg', '.webp'):
        candidate = os.path.join(_avatar_dir(), f"user_{user_id}{ext}")
        try:
            if os.path.exists(candidate):
                os.remove(candidate)
        except OSError:
            pass


def _delete_storage_avatars(user_id, keep_ext=None):
    """Delete storage objects for all avatar exts except keep_ext (post-replace cleanup)."""
    from services import storage_service as store
    for ext in ('.png', '.jpg', '.jpeg', '.webp'):
        if ext == keep_ext:
            continue
        try:
            store.delete_file(store.avatar_storage_path(user_id, ext))
        except Exception:
            pass


def _validate_image_bytes(raw):
    """Magic-byte validation (no Pillow dependency). Returns ext or None."""
    if not raw or len(raw) < 12:
        return None
    if raw[:8] == b'\x89PNG\r\n\x1a\n':
        return '.png'
    if raw[:2] == b'\xff\xd8':
        return '.jpg'
    if raw[:4] == b'RIFF' and raw[8:12] == b'WEBP':
        return '.webp'
    return None


@profile_bp.route('/api/profile/photo', methods=['POST'])
@login_required
def upload_profile_photo():
    from services import storage_service as store
    user_id = session['user_id']
    file_storage = request.files.get('photo') or request.files.get('file')

    if not file_storage or not file_storage.filename:
        return jsonify({'error': 'No photo selected for upload.'}), 400

    filename = file_storage.filename or ''
    claimed_ext = os.path.splitext(filename)[1].lower()
    if claimed_ext not in PHOTO_ALLOWED_EXTS:
        return jsonify({'error': 'Unsupported format. Please upload a JPG, PNG, or WEBP image.'}), 400

    try:
        raw = file_storage.read()
    except Exception:
        return jsonify({'error': 'Could not read the uploaded file.'}), 400

    if len(raw) > PHOTO_MAX_BYTES:
        return jsonify({'error': 'Photo is too large. Maximum size is 2 MB.'}), 400

    detected_ext = _validate_image_bytes(raw)
    if not detected_ext:
        return jsonify({'error': 'This file does not appear to be a valid image.'}), 400

    # Phase 2: single write through the storage abstraction (Supabase when
    # configured, local fallback otherwise). Paths are server-generated.
    dest = store.avatar_storage_path(user_id, detected_ext)
    try:
        store.upload_file(dest, raw, content_type=PHOTO_MIMETYPES[detected_ext])
    except store.StorageError as e:
        return jsonify({'error': str(e)}), 503
    _delete_storage_avatars(user_id, keep_ext=detected_ext)
    if store.backend_name() != 'local':
        # Supabase is now source of truth; drop legacy local copies.
        # On the local backend the legacy file IS the stored upload.
        _remove_existing_avatars(user_id)

    avatar_path = '/api/profile/photo/file'
    db = get_db()
    if 'avatar_storage_path' in _user_columns(db):
        db.execute('UPDATE users SET avatar_url = ?, avatar_storage_path = ? WHERE id = ?',
                   (avatar_path, dest, user_id))
    else:  # legacy DB without the Phase 2 column (SQLite fallback safety)
        db.execute('UPDATE users SET avatar_url = ? WHERE id = ?', (avatar_path, user_id))
    db.commit()

    return jsonify({'success': True, 'avatar_url': avatar_path, 'size_bytes': len(raw),
                    'storage_backend': store.backend_name(), 'storage_path': dest})


def _user_columns(db):
    try:
        from database.db import is_postgres
        if is_postgres():
            rows = db.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'users'""").fetchall()
            return {r['column_name'] for r in rows}
        rows = db.execute('PRAGMA table_info(users)').fetchall()
        cols = set()
        for r in rows:
            try:
                cols.add(r['name'])
            except Exception:
                cols.add(r[1])
        return cols
    except Exception:
        return set()


@profile_bp.route('/api/profile/photo/file', methods=['GET'])
@login_required
def serve_profile_photo():
    from flask import Response
    from services import storage_service as store
    user_id = session['user_id']
    # Ownership: only the session user's own photo (no ID parameter → no IDOR).
    user = get_user_by_id(user_id)
    storage_path = None
    if user:
        try:
            storage_path = user['avatar_storage_path']
        except (KeyError, IndexError, TypeError):
            storage_path = None
    try:
        raw, ext = store.read_avatar(user_id, storage_path)
    except store.StorageError as e:
        msg = str(e).lower()
        if 'unavailable' in msg or 'timed out' in msg or 'rejected' in msg:
            return jsonify({'error': str(e)}), 503
        return jsonify({'error': 'No profile photo found.'}), 404
    mimetype = PHOTO_MIMETYPES.get(ext, 'application/octet-stream')
    return Response(raw, mimetype=mimetype, headers={'Cache-Control': 'private, max-age=3600'})


@profile_bp.route('/api/profile/photo', methods=['DELETE'])
@login_required
def remove_profile_photo():
    from services import storage_service as store
    user_id = session['user_id']
    user = get_user_by_id(user_id)
    storage_path = None
    if user:
        try:
            storage_path = user['avatar_storage_path']
        except (KeyError, IndexError, TypeError):
            storage_path = None
    # Delete the storage object first; on service failure report 503 without
    # claiming success (metadata is kept so nothing is orphaned silently).
    try:
        store.delete_avatar(user_id, storage_path)
    except store.StorageError as e:
        return jsonify({'error': str(e)}), 503
    _remove_existing_avatars(user_id)
    db = get_db()
    if 'avatar_storage_path' in _user_columns(db):
        db.execute('UPDATE users SET avatar_url = NULL, avatar_storage_path = NULL WHERE id = ?', (user_id,))
    else:
        db.execute('UPDATE users SET avatar_url = NULL WHERE id = ?', (user_id,))
    db.commit()
    return jsonify({'success': True})
