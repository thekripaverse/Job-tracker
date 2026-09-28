"""Phase 2 Supabase Storage tests (offline-capable).

Local-backend integration runs against a patched instance dir (never touches
real instance/uploads). Supabase REST behavior is unit-tested with mocked
HTTP. Route tests use TESTING=True (CSRF/rate bypasses as documented).
"""
import io
import json
import os
import tempfile
import unittest
import urllib.error
from unittest.mock import patch, MagicMock

from app import create_app
from services import storage_service as store


def make_app(tmpdir, **overrides):
    db_file = os.path.join(tmpdir, 'test_storage.db')
    inst_dir = os.path.join(tmpdir, 'inst')
    cfg = {'TESTING': True, 'DATABASE': db_file, 'SECRET_KEY': 'storage-test-key',
           'INSTANCE_PATH': inst_dir}
    cfg.update(overrides)
    os.environ['VERCEL'] = '1'
    app = create_app(cfg)
    return app


def register(client, username='stuser', email='st@example.com'):
    return client.post('/signup', data={'username': username, 'email': email,
                                        'password': 'Password123!',
                                        'confirm_password': 'Password123!'},
                       follow_redirects=True)


PNG = (b'\x89PNG\r\n\x1a\n' + b'\x00' * 64)
JPG = (b'\xff\xd8\xff\xe0' + b'\x00' * 64)


class ValidationTest(unittest.TestCase):
    def test_valid_and_rejected(self):
        self.assertEqual(store.validate_upload('r.pdf', b'x' * 10, store.RESUME_EXTS, 100), '.pdf')
        with self.assertRaises(store.StorageError):
            store.validate_upload('big.pdf', b'x' * 101, store.RESUME_EXTS, 100)
        with self.assertRaises(store.StorageError):
            store.validate_upload('evil.exe', b'x', store.RESUME_EXTS | {'.exe'}, 100)
        with self.assertRaises(store.StorageError):
            store.validate_upload('run.sh', b'x', {'.sh', '.pdf'}, 100)
        with self.assertRaises(store.StorageError):
            store.validate_upload('', b'x', store.RESUME_EXTS, 100)
        with self.assertRaises(store.StorageError):
            store.validate_upload('a.pdf', None, store.RESUME_EXTS, 100)


class PathSecurityTest(unittest.TestCase):
    def test_builders(self):
        self.assertEqual(store.avatar_storage_path(7, '.jpg'), 'avatars/user-7/profile.jpg')
        self.assertEqual(store.resume_storage_path(7, 3, 'cv.pdf'), 'resumes/user-7/3/cv.pdf')
        self.assertEqual(store.resume_storage_path(7, 'master', '../evil.pdf'),
                         'resumes/user-7/master/evil.pdf')
        with self.assertRaises(store.StorageError):
            store.avatar_storage_path('7 OR 1=1', '.jpg')
        with self.assertRaises(store.StorageError):
            store.avatar_storage_path(7, '.exe')
        with self.assertRaises(store.StorageError):
            store.resume_storage_path(7, '../../x', 'cv.pdf')

    def test_validate_storage_path(self):
        self.assertEqual(store.validate_storage_path('avatars/user-7/profile.jpg'),
                         'avatars/user-7/profile.jpg')
        for bad in ['../secret', '/avatars/x', 'avatars/../../etc/passwd',
                    'other/user-1/f', '', None, 'avatars//..//x',
                    'unknownroot/a/b', 'a' * 200]:
            with self.assertRaises(store.StorageError, msg=str(bad)):
                store.validate_storage_path(bad)


class LocalBackendTest(unittest.TestCase):
    def test_roundtrip_and_missing(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(td)
            with app.app_context():
                self.assertFalse(store.storage_enabled())
                p = 'resumes/user-9/3/cv.pdf'
                self.assertFalse(store.file_exists(p))
                store.upload_file(p, b'%PDF-1.4 binary', content_type='application/pdf')
                self.assertTrue(store.file_exists(p))
                self.assertEqual(store.download_file(p), b'%PDF-1.4 binary')
                self.assertTrue(store.delete_file(p))
                self.assertFalse(store.file_exists(p))
                self.assertFalse(store.delete_file(p))  # idempotent
                try:
                    store.download_file(p)
                    self.fail('expected StorageError')
                except store.StorageError:
                    pass

    def test_resume_binary_persist_pdf(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(td)
            with app.app_context():
                path, err = store.persist_resume_binary(5, 9, 'cv.pdf', b'%PDF-binary')
                self.assertIsNone(err)
                self.assertEqual(path, 'resumes/user-5/9/cv.pdf')
                self.assertEqual(store.download_file(path), b'%PDF-binary')

    def test_legacy_avatar_read(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(td)
            legacy = os.path.join(td, 'inst', 'uploads', 'avatars')
            os.makedirs(legacy)
            with open(os.path.join(legacy, 'user_11.jpg'), 'wb') as f:
                f.write(JPG)
            with app.app_context():
                data, ext = store.read_avatar(11, None)
                self.assertEqual(data, JPG)
                self.assertEqual(ext, '.jpg')
                try:
                    store.read_avatar(12, None)
                    self.fail('expected StorageError')
                except store.StorageError:
                    pass


class SupabaseRestTest(unittest.TestCase):
    def _sb_app(self, td):
        return make_app(td, SUPABASE_URL='https://ref.supabase.co',
                        SUPABASE_SERVICE_ROLE_KEY='test-service-key')

    def test_upload_download_delete(self):
        with tempfile.TemporaryDirectory() as td:
            app = self._sb_app(td)
            with app.app_context():
                self.assertTrue(store.storage_enabled())
                resp = MagicMock()
                resp.status = 200
                resp.read.return_value = b'ok'
                resp.__enter__.return_value = resp
                with patch.object(store.urllib.request, 'urlopen', return_value=resp) as m:
                    store.upload_file('avatars/user-1/profile.png', PNG, 'image/png')
                    args, _ = m.call_args
                    req = args[0]
                    # Service key travels in headers only, never in URL/body.
                    self.assertNotIn('test-service-key', req.full_url)
                    self.assertEqual(req.get_header('Authorization'), 'Bearer test-service-key')
                    self.assertEqual(store.download_file('avatars/user-1/profile.png'), b'ok')

    def test_errors_redacted_and_mapped(self):
        with tempfile.TemporaryDirectory() as td:
            app = self._sb_app(td)
            with app.app_context():
                err404 = urllib.error.HTTPError('url', 404, 'nf', {}, None)
                with patch.object(store.urllib.request, 'urlopen', side_effect=err404):
                    with self.assertRaises(store.StorageError):
                        store.download_file('avatars/user-1/profile.png')
                err401 = urllib.error.HTTPError('url', 401, 'unauth', {}, None)
                with patch.object(store.urllib.request, 'urlopen', side_effect=err401):
                    try:
                        store.download_file('avatars/user-1/profile.png')
                        self.fail('expected StorageError')
                    except store.StorageError as e:
                        self.assertNotIn('test-service-key', str(e))


class AvatarRouteTest(unittest.TestCase):
    def _client(self, td):
        app = make_app(td)
        return app, app.test_client()

    def _png_file(self, data=PNG, name='me.png'):
        return {'photo': (io.BytesIO(data), name)}

    def test_upload_serve_delete_owner(self):
        with tempfile.TemporaryDirectory() as td:
            app, c = self._client(td)
            register(c)
            r = c.post('/api/profile/photo', data=self._png_file(),
                       content_type='multipart/form-data')
            self.assertEqual(r.status_code, 200)
            body = r.get_json()
            self.assertTrue(body['success'])
            r = c.get('/api/profile/photo/file')
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.data, PNG)
            self.assertEqual(r.content_type, 'image/png')
            r = c.delete('/api/profile/photo')
            self.assertEqual(r.status_code, 200)
            self.assertEqual(c.get('/api/profile/photo/file').status_code, 404)

    def test_cross_user_isolation(self):
        with tempfile.TemporaryDirectory() as td:
            app, c = self._client(td)
            register(c, username='anna', email='anna@example.com')
            c.post('/api/profile/photo', data=self._png_file(),
                   content_type='multipart/form-data')
            c.get('/logout')
            register(c, username='bob', email='bob@example.com')
            # Bob's own endpoint serves only Bob (404) — Anna's bytes unreachable.
            r = c.get('/api/profile/photo/file')
            self.assertEqual(r.status_code, 404)
            # Bob cannot delete Anna's photo to any effect on Anna.
            c.delete('/api/profile/photo')
            c.get('/logout')
            c.post('/login', data={'login_input': 'anna@example.com',
                                   'password': 'Password123!'})
            self.assertEqual(c.get('/api/profile/photo/file').status_code, 200)

    def test_invalid_uploads_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            app, c = self._client(td)
            register(c)
            r = c.post('/api/profile/photo',
                       data={'photo': (io.BytesIO(b'MZ...'), 'evil.exe')},
                       content_type='multipart/form-data')
            self.assertEqual(r.status_code, 400)
            r = c.post('/api/profile/photo',
                       data={'photo': (io.BytesIO(b'not an image'), 'x.png')},
                       content_type='multipart/form-data')
            self.assertEqual(r.status_code, 400)


class ResumeBinaryRouteTest(unittest.TestCase):
    def test_upload_persists_binary_and_downloads(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(td)
            c = app.test_client()
            register(c)
            payload = b'John Doe resume text here'
            r = c.post('/api/resume/upload',
                       data={'resume_file': (io.BytesIO(payload), 'john.txt')},
                       content_type='multipart/form-data')
            self.assertEqual(r.status_code, 200)
            with app.app_context():
                from database.db import get_db
                row = get_db().execute(
                    'SELECT resume_storage_path FROM users').fetchone()
                try:
                    sp = row['resume_storage_path']
                except (KeyError, IndexError, TypeError):
                    sp = None
            self.assertTrue(sp and sp.startswith('resumes/user-'))
            r = c.get('/api/resume/file')
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.data, payload)

    def test_version_binary_and_traversal_filename(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(td)
            c = app.test_client()
            register(c)
            payload = b'version binary'
            r = c.post('/api/resume-versions',
                       data={'version_name': 'V1',
                             'resume_file': (io.BytesIO(payload), '../../evil.txt')},
                       content_type='multipart/form-data')
            # .txt passes ext allowlist; traversal neutralized into user dir.
            self.assertIn(r.status_code, (200, 201))
            vid = r.get_json()['id']
            with app.app_context():
                from database.db import get_db
                row = get_db().execute(
                    'SELECT storage_path FROM resume_versions WHERE id = ?',
                    (vid,)).fetchone()
                try:
                    sp = row['storage_path']
                except (KeyError, IndexError, TypeError):
                    sp = None
            self.assertTrue(sp and '..' not in sp and sp.startswith('resumes/user-'))
            r = c.get(f'/api/resume-versions/{vid}/file')
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.data, payload)
            # Non-owner cannot fetch it.
            c.get('/logout')
            register(c, username='intruder', email='intruder@example.com')
            self.assertEqual(c.get(f'/api/resume-versions/{vid}/file').status_code, 404)
            # Owner delete removes binary + row.
            c.get('/logout')
            c.post('/login', data={'login_input': 'st@example.com',
                                   'password': 'Password123!'})
            self.assertEqual(c.delete(f'/api/resume-versions/{vid}').status_code, 200)
            self.assertEqual(c.get(f'/api/resume-versions/{vid}/file').status_code, 404)

    def test_legacy_text_only_download_404(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(td)
            c = app.test_client()
            register(c)
            with app.app_context():
                from database.db import get_db
                get_db().execute(
                    "UPDATE users SET resume_text='x', resume_filename='o.pdf', "
                    "resume_storage_path=NULL")
                get_db().commit()
            r = c.get('/api/resume/file')
            self.assertEqual(r.status_code, 404)


if __name__ == '__main__':
    unittest.main()
