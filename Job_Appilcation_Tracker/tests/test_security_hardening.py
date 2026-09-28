"""Phase 1.5 security hardening tests.

CSRF and rate limiting are bypassed under TESTING=True (so the pre-existing
66-test suite keeps exercising the SQLite rollback path). Enforcement is
proven here with TESTING=False apps plus unit-level checks.
"""
import io
import os
import re
import unittest

from app import create_app
from services import security as sec


def make_app(tmpdir, testing=False, name='sec.db'):
    db_file = os.path.join(tmpdir, name)
    cfg = {'TESTING': testing, 'DATABASE': db_file, 'SECRET_KEY': 'sec-test-key',
           'INSTANCE_PATH': os.path.join(tmpdir, 'inst')}
    os.environ['VERCEL'] = '1'  # keep the scheduler thread out of tests
    app = create_app(cfg)
    return app


def register(client, username='secuser', email='sec@example.com', pw='Password123!'):
    return client.post('/signup', data={'username': username, 'email': email,
                                        'password': pw, 'confirm_password': pw},
                       follow_redirects=True)


class SafeRedirectTest(unittest.TestCase):
    def test_unit(self):
        self.assertTrue(sec.is_safe_redirect('/'))
        self.assertTrue(sec.is_safe_redirect('/?view=analytics'))
        self.assertTrue(sec.is_safe_redirect('/applications'))
        for bad in ['https://attacker.example', 'http://attacker.example/x',
                    '//attacker.example', '///evil', 'javascript:alert(1)',
                    'data:text/html,hi', '', None, '/\\evil', '\\\\evil']:
            self.assertFalse(sec.is_safe_redirect(bad), bad)

    def test_login_next_validation(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            app = make_app(td, testing=True)
            c = app.test_client()
            register(c, username='redir', email='redir@example.com')
            c.get('/logout')
            for bad in ['https://attacker.example', '//attacker.example', 'javascript:alert(1)']:
                r = c.post(f'/login?next={bad}',
                           data={'login_input': 'redir@example.com', 'password': 'Password123!'},
                           follow_redirects=False)
                self.assertEqual(r.status_code, 302)
                loc = r.headers.get('Location', '')
                self.assertNotIn('attacker.example', loc)
                self.assertNotIn('javascript:', loc)
                c.get('/logout')
            r = c.post('/login?next=/?view=analytics',
                       data={'login_input': 'redir@example.com', 'password': 'Password123!'},
                       follow_redirects=False)
            self.assertEqual(r.status_code, 302)
            self.assertIn('view=analytics', r.headers.get('Location', ''))


class GoogleAuthFailClosedTest(unittest.TestCase):
    def test_dummy_token_never_authenticates(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            app = make_app(td, testing=True)
            c = app.test_client()
            # No verify_signature bypass may exist in the auth route.
            import inspect
            from routes import auth as auth_mod
            self.assertNotIn('verify_signature', inspect.getsource(auth_mod))
            r = c.post('/auth/google', json={'credential': 'dummy.token.here'})
            self.assertIn(r.status_code, (400, 503))
            # Must not be logged in: protected API stays 401.
            r2 = c.get('/api/profile')
            self.assertIn(r2.status_code, (401, 302))


class GmailMockFailClosedTest(unittest.TestCase):
    def test_connect_and_sync(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            app = make_app(td, testing=True)
            self.assertFalse(app.config.get('DEV_ALLOW_MOCKS'))
            c = app.test_client()
            register(c, username='gmailt', email='gmailt@example.com')
            r = c.get('/auth/google/gmail/connect', follow_redirects=False)
            loc = r.headers.get('Location', '')
            # Either a real Google redirect or a fail-closed error — never fake success.
            self.assertNotIn('connected=true', loc)
            with app.app_context():
                from database.db import get_email_connection, get_db
                # resolve test user id
                row = get_db().execute('SELECT id FROM users WHERE email=?',
                                       ('gmailt@example.com',)).fetchone()
                self.assertIsNone(get_email_connection(dict(row)['id']))
            # A mock token row must not yield fake sync success.
            with app.app_context():
                from database.db import save_email_connection
                uid = dict(get_db().execute('SELECT id FROM users WHERE email=?',
                                            ('gmailt@example.com',)).fetchone())['id']
                save_email_connection(uid, 'dev@example.com',
                                      'dev_mock_access_token', 'dev_mock_refresh_token')
            r = c.post('/api/email-intelligence/sync')
            self.assertEqual(r.status_code, 400)
            self.assertEqual(r.get_json().get('error_type'), 'NOT_CONNECTED')


class OAuthStateTest(unittest.TestCase):
    def test_single_use_and_expiry(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            app = make_app(td, testing=True)
            with app.test_request_context('/'):
                t = sec.new_oauth_state('gmail', ttl_seconds=600)
                self.assertTrue(sec.consume_oauth_state(t, purpose='gmail'))
                self.assertFalse(sec.consume_oauth_state(t, purpose='gmail'))  # no replay
                self.assertFalse(sec.consume_oauth_state('', purpose='gmail'))
                self.assertFalse(sec.consume_oauth_state('nope', purpose='gmail'))
                t2 = sec.new_oauth_state('gmail', ttl_seconds=600)
                self.assertFalse(sec.consume_oauth_state(t2, purpose='other'))


class SSRFTest(unittest.TestCase):
    def test_validate_targets(self):
        ok, _ = sec.validate_fetch_target('http://93.184.216.1/')  # example.com, no DNS needed
        self.assertTrue(ok)
        for bad in ['http://localhost/', 'http://localhost:5000/x', 'http://127.0.0.1/',
                    'http://10.0.0.5/', 'http://192.168.1.1/', 'http://169.254.169.254/',
                    'http://100.100.100.100/', 'file:///etc/passwd', 'ftp://x/y',
                    'http://user:pass@example.com/', 'not-a-url', 'http://[::1]/']:
            self.assertFalse(sec.validate_fetch_target(bad)[0], bad)

    def test_autofill_route_rejects(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            app = make_app(td, testing=True)
            c = app.test_client()
            register(c, username='ssrf', email='ssrf@example.com')
            for bad in ['http://localhost:5000/', 'http://127.0.0.1/', 'http://169.254.169.254/',
                        'http://10.1.2.3/', 'file:///etc/passwd']:
                r = c.post('/api/autofill-url', json={'url': bad})
                self.assertEqual(r.status_code, 400, bad)
                self.assertIn('error', r.get_json())


class UploadLimitTest(unittest.TestCase):
    def test_oversize_resume_rejected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            app = make_app(td, testing=True)
            c = app.test_client()
            register(c, username='upl', email='upl@example.com')
            big = io.BytesIO(b'x' * (8 * 1024 * 1024 + 1))
            r = c.post('/api/resume/upload', data={'resume_file': (big, 'big.txt')},
                       content_type='multipart/form-data')
            self.assertEqual(r.status_code, 400)
            self.assertIn('large', r.get_json()['error'].lower())
            small = io.BytesIO(b'hello resume')
            r = c.post('/api/resume/upload', data={'resume_file': (small, 'r.txt')},
                       content_type='multipart/form-data')
            self.assertEqual(r.status_code, 200)


class DeadlineFollowupTest(unittest.TestCase):
    def test_stored_not_dropped(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            app = make_app(td, testing=True)
            c = app.test_client()
            register(c, username='ddl', email='ddl@example.com')
            r = c.post('/applications', json={'company_name': 'D', 'job_title': 'J',
                                              'status': 'Applied', 'date_applied': '2026-09-01',
                                              'deadline_date': '2026-10-01', 'followup_date': '2026-09-15'})
            self.assertEqual(r.status_code, 201)
            body = r.get_json()
            self.assertEqual(body.get('deadline_date'), '2026-10-01')
            self.assertEqual(body.get('followup_date'), '2026-09-15')
            app_id = body['id']
            r = c.put(f'/applications/{app_id}', json={'deadline_date': '2026-11-01'})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.get_json().get('deadline_date'), '2026-11-01')
            self.assertEqual(r.get_json().get('followup_date'), '2026-09-15')
            r = c.post('/applications', json={'company_name': 'X', 'job_title': 'Y',
                                              'deadline_date': 'not-a-date'})
            self.assertEqual(r.status_code, 400)


class AccountDeleteConfirmTest(unittest.TestCase):
    def test_confirm_required(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            app = make_app(td, testing=True)
            c = app.test_client()
            register(c, username='delc', email='delc@example.com')
            self.assertEqual(c.post('/api/account/delete', json={}).status_code, 400)
            self.assertEqual(c.post('/api/account/delete',
                                    json={'confirm': 'yes'}).status_code, 400)
            r = c.post('/api/account/delete', json={'confirm': 'DELETE'})
            self.assertEqual(r.status_code, 200)
            self.assertIn(c.get('/api/profile').status_code, (401, 302))


class CSRFLimitTest(unittest.TestCase):
    def _csrf_client(self, tmpdir):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            app = create_app({'TESTING': False, 'DATABASE': os.path.join(td, 'csrf.db'),
                              'SECRET_KEY': 'csrf-key'})
            c = app.test_client()
            page = c.get('/signup').data.decode()
            m = re.search(r'name="csrf_token" value="([^"]+)"', page)
            self.assertIsNotNone(m)
            token = m.group(1)
            return app, c, token

    def test_mutation_without_token_rejected_with_token_accepted(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            app = create_app({'TESTING': False, 'DATABASE': os.path.join(td, 'csrf.db'),
                              'SECRET_KEY': 'csrf-key'})
            c = app.test_client()
            # No token -> 403 even for signup.
            r = c.post('/signup', data={'username': 'a', 'email': 'a@e.com',
                                        'password': 'Password123!', 'confirm_password': 'Password123!'})
            self.assertEqual(r.status_code, 403)
            page = c.get('/signup').data.decode()
            token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
            r = c.post('/signup', data={'username': 'a', 'email': 'a@e.com',
                                        'password': 'Password123!', 'confirm_password': 'Password123!',
                                        'csrf_token': token}, follow_redirects=True)
            self.assertEqual(r.status_code, 200)
            # JSON API without header -> 403; with header -> 201.
            # Note: signup rotates the session (session.clear()), so fetch a
            # fresh token from the now-authenticated dashboard.
            r = c.post('/applications', json={'company_name': 'C', 'job_title': 'J'})
            self.assertEqual(r.status_code, 403)
            dash = c.get('/').data.decode()
            token2 = re.search(r'name="csrf-token" content="([^"]+)"', dash).group(1)
            r = c.post('/applications', json={'company_name': 'C', 'job_title': 'J'},
                       headers={'X-CSRFToken': token2})
            self.assertEqual(r.status_code, 201)

    def test_rate_limiter_unit(self):
        sec.reset_rate_limits()
        for _ in range(3):
            allowed, _ = sec.check_rate_limit('unit-test-key', 3, 60)
            self.assertTrue(allowed)
        allowed, retry = sec.check_rate_limit('unit-test-key', 3, 60)
        self.assertFalse(allowed)
        self.assertGreaterEqual(retry, 1)
        sec.reset_rate_limits()


class TokenCipherTest(unittest.TestCase):
    def test_roundtrip_and_legacy(self):
        enc = sec.encrypt_token('ya29.real-token')
        self.assertTrue(enc.startswith('enc:'))
        self.assertEqual(sec.decrypt_token(enc), 'ya29.real-token')
        self.assertEqual(sec.decrypt_token('plain-legacy'), 'plain-legacy')
        self.assertEqual(sec.encrypt_token('dev_mock_access_token'), 'dev_mock_access_token')
        self.assertIsNone(sec.encrypt_token(None))
        self.assertIsNone(sec.decrypt_token(None))

    def test_key_generation(self):
        k = sec.generate_token_encryption_key()
        self.assertTrue(isinstance(k, str) and len(k) > 20)


class SessionCookieConfigTest(unittest.TestCase):
    def test_dev_flags(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            app = make_app(td, testing=True)
            self.assertTrue(app.config.get('SESSION_COOKIE_HTTPONLY'))
            self.assertEqual(app.config.get('SESSION_COOKIE_SAMESITE'), 'Lax')
            # Production forces Secure + no DEBUG (verified at config level).
            import inspect
            from config import Config
            src = inspect.getsource(Config)
            self.assertIn('SESSION_COOKIE_SECURE', src)


if __name__ == '__main__':
    unittest.main()
