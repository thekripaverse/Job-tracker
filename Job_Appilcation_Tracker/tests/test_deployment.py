"""Phase 3 deployment readiness tests (offline, tmp SQLite)."""
import os
import tempfile
import unittest

from app import create_app


def make_app(td):
    return create_app({'TESTING': True, 'DATABASE': os.path.join(td, 'd.db'),
                       'SECRET_KEY': 'deploy-test-key'})


class HealthTest(unittest.TestCase):
    def test_health_shape_and_safety(self):
        with tempfile.TemporaryDirectory() as td:
            c = make_app(td).test_client()
            r = c.get('/health')
            self.assertEqual(r.status_code, 200)
            body = r.get_json()
            self.assertEqual(body['status'], 'ok')
            self.assertIn(body['database'], ('sqlite', 'postgres'))
            self.assertEqual(body['db_reachable'], 'ok')
            blob = r.data.decode()
            for secret_word in ('SECRET', 'PASSWORD', 'SERVICE_ROLE', 'postgres://'):
                self.assertNotIn(secret_word, blob)


class WsgiEntryTest(unittest.TestCase):
    def test_wsgi_app_object(self):
        import wsgi
        self.assertTrue(hasattr(wsgi, 'app'))
        # Same object gunicorn will serve: has routes incl. health.
        rules = {str(r) for r in wsgi.app.url_map.iter_rules()}
        self.assertIn('/health', rules)
        self.assertIn('/', rules)

    def test_gunicorn_dependency(self):
        reqs = open('requirements.txt', encoding='utf-8').read().lower()
        self.assertIn('gunicorn', reqs)

    def test_render_blueprint(self):
        import re
        text = open('render.yaml', encoding='utf-8').read()
        self.assertIn('wsgi:app', text)
        self.assertIn('--workers 1', text)
        self.assertIn('/health', text)
        for secret in ['SECRET_KEY', 'DATABASE_URL', 'SUPABASE_SERVICE_ROLE_KEY']:
            self.assertIn(secret, text)
        # No secret VALUES in render.yaml (sync:false only).
        self.assertNotIn('eyJ', text)
        self.assertNotIn('postgres://', text)

    def test_gitignore_coverage(self):
        gi = open('.gitignore', encoding='utf-8').read()
        for pattern in ['.env', '__pycache__', '*.db', '*.bak', '*.log']:
            self.assertIn(pattern, gi)


if __name__ == '__main__':
    unittest.main()
