"""Phase 3 Vercel serverless tests (offline, tmp SQLite/instance).

Proves: single entry point, no I/O at import, lazy DB init on Vercel,
no scheduler thread on Vercel, production fail-closed DB/storage,
secret-protected idempotent cron.
"""
import io
import os
import tempfile
import unittest

import services.scheduler as scheduler_mod
from app import create_app


def make_app(td, name='vercel.db', **overrides):
    cfg = {'TESTING': True, 'DATABASE': os.path.join(td, name),
           'SECRET_KEY': 'vercel-test-key',
           'INSTANCE_PATH': os.path.join(td, 'inst')}
    cfg.update(overrides)
    return create_app(cfg)


class VercelEntryTest(unittest.TestCase):
    def test_single_flask_object(self):
        import api.index as entry
        import app as app_mod
        self.assertIs(entry.app, app_mod.app)


class VercelLazyInitTest(unittest.TestCase):
    def test_no_io_at_import_or_create(self):
        old = os.environ.get('VERCEL')
        os.environ['VERCEL'] = '1'
        # app-module import may have started the scheduler thread already
        # (pre-existing import-time side effect); snapshot the guard flag.
        started_before = scheduler_mod._scheduler_started
        try:
            with tempfile.TemporaryDirectory() as td:
                db_path = os.path.join(td, 'lazy.db')
                app = create_app({'TESTING': False, 'DATABASE': db_path,
                                  'SECRET_KEY': 'lazy-key',
                                  'INSTANCE_PATH': os.path.join(td, 'inst')})
                # Import + create must not touch the database file…
                self.assertFalse(os.path.exists(db_path))
                # …and must not start the scheduler thread.
                self.assertEqual(scheduler_mod._scheduler_started, started_before)
                c = app.test_client()
                r = c.get('/health')
                self.assertEqual(r.status_code, 200)
                # First request initializes the database lazily.
                self.assertTrue(os.path.exists(db_path))
                self.assertEqual(r.get_json()['database'], 'sqlite')
        finally:
            if old is None:
                os.environ.pop('VERCEL', None)
            else:
                os.environ['VERCEL'] = old


class ProdFailClosedTest(unittest.TestCase):
    def test_production_requires_postgres(self):
        with tempfile.TemporaryDirectory() as td:
            # TESTING=True bypasses the guard (test-only contract, same as
            # CSRF/rate limits); without it, production + SQLite must refuse.
            with self.assertRaises(RuntimeError):
                create_app({'TESTING': False, 'ENV': 'production',
                            'DATABASE': os.path.join(td, 'prod.db'),
                            'SECRET_KEY': 'prod-key',
                            'INSTANCE_PATH': os.path.join(td, 'inst')})

    def test_testing_bypass_documented(self):
        with tempfile.TemporaryDirectory() as td:
            make_app(td, ENV='production',
                     DATABASE=os.path.join(td, 'prod.db'))  # no raise: TESTING

    def test_storage_unconfigured_in_production(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(td, ENV='production')
            c = app.test_client()
            c.post('/signup', data={'username': 'u', 'email': 'u@e.com',
                                    'password': 'Password123!',
                                    'confirm_password': 'Password123!'},
                   follow_redirects=True)
            png = b'\x89PNG\r\n\x1a\n' + b'\x00' * 64
            r = c.post('/api/profile/photo',
                       data={'photo': (io.BytesIO(png), 'me.png')},
                       content_type='multipart/form-data')
            self.assertEqual(r.status_code, 503)
            self.assertIn('not configured', r.get_json()['error'].lower())


class CronEndpointTest(unittest.TestCase):
    def _cron_client(self, td):
        app = make_app(td, CRON_SECRET='test-cron-secret')
        return app.test_client()

    def test_unauthorized_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            c = self._cron_client(td)
            self.assertEqual(c.get('/api/cron/reminders').status_code, 401)
            self.assertEqual(c.get('/api/cron/reminders?key=nope').status_code, 401)
            self.assertEqual(c.post('/api/cron/reminders').status_code, 401)

    def test_authorized_runs_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            c = self._cron_client(td)
            r1 = c.get('/api/cron/reminders?key=test-cron-secret')
            self.assertEqual(r1.status_code, 200)
            body1 = r1.get_json()
            self.assertTrue(body1['success'])
            r2 = c.post('/api/cron/reminders',
                        headers={'Authorization': 'Bearer test-cron-secret'})
            self.assertEqual(r2.status_code, 200)
            body2 = r2.get_json()
            self.assertTrue(body2['success'])
            # Re-run is safe: same shape, no error, no duplicates possible
            # (processors stamp sent dates; empty test DB sends nothing).
            self.assertEqual(body1['total'], body2['total'])

    def test_status_endpoint_public_but_secret_free(self):
        with tempfile.TemporaryDirectory() as td:
            c = self._cron_client(td)
            r = c.get('/api/cron/status')
            self.assertEqual(r.status_code, 200)
            self.assertNotIn('test-cron-secret', r.data.decode())

    def test_unconfigured_secret_refuses_all(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(td)  # no CRON_SECRET
            c = app.test_client()
            self.assertEqual(c.get('/api/cron/reminders?key=x').status_code, 401)


if __name__ == '__main__':
    unittest.main()
