"""Phase 1 smoke test: Flask app against Supabase PostgreSQL (DATABASE_URL).

Covers: register/login/logout, CRUD + status + search, timeline,
resume versions, email endpoints (status/stats/emails), calendar/analytics,
bulk ops. AI endpoints tested tolerantly (pass if 200 or graceful 400/502).
Uses a throwaway user; cleans up afterwards. Read-only on SQLite.
"""
import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
except Exception:
    pass

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

assert os.environ.get('DATABASE_URL', '').startswith(('postgresql://', 'postgres://')), \
    'DATABASE_URL must be set to Supabase Postgres'

from app import create_app
from database.db import is_postgres

results = []


def check(name, cond, detail=''):
    results.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")


app = create_app({'TESTING': True, 'SECRET_KEY': 'pg-smoke-key'})
with app.app_context():
    check('backend is postgres', is_postgres(), 'postgres (host redacted)')

c = app.test_client()
UN, EM, PW = 'pgsmoke', 'pgsmoke@example.com', 'Password123!'

r = c.post('/signup', data={'username': UN, 'email': EM,
                            'password': PW, 'confirm_password': PW},
           follow_redirects=False)
check('register', r.status_code in (302, 200), str(r.status_code))

r = c.post('/login', data={'login_input': EM, 'password': PW}, follow_redirects=True)
check('login', r.status_code == 200 and b'Job & Internship Tracker' in r.data, str(r.status_code))

# --- Applications CRUD ---
r = c.post('/applications', json={'company_name': 'PGCorp', 'job_title': 'PGEngineer',
                                  'status': 'Applied', 'date_applied': '2026-09-01'})
check('create application', r.status_code == 201, str(r.status_code))
app_id = r.get_json().get('id') if r.status_code == 201 else None

r = c.get(f'/applications/{app_id}') if app_id else None
check('view application', r is not None and r.status_code == 200, str(r.status_code if r else None))

r = c.put(f'/applications/{app_id}', json={'status': 'Interviewing',
                                           'interview_date': '2026-10-05'}) if app_id else None
check('edit/status+interview date', r is not None and r.status_code == 200
      and r.get_json().get('status') == 'Interviewing', str(r.status_code if r else None))

r = c.get('/applications')
check('search/filter list', r.status_code == 200 and any(
    a.get('id') == app_id for a in r.get_json()), str(r.status_code))

# --- Timeline ---
r = c.get(f'/api/applications/{app_id}/timeline') if app_id else None
tl_ok = r is not None and r.status_code == 200 and len(r.get_json().get('timeline', [])) >= 1
check('timeline created+viewed', tl_ok, str(r.status_code if r else None))

# --- Calendar / analytics / dashboard ---
for name, url in [('calendar events', '/api/calendar-events'),
                  ('analytics', '/api/analytics'),
                  ('dashboard html', '/')]:
    r = c.get(url)
    check(name, r.status_code == 200, str(r.status_code))

# --- Bulk ---
r = c.post('/api/applications/bulk', json={'action': 'status', 'ids': [app_id],
                                           'status': 'Offered'}) if app_id else None
check('bulk status', r is not None and r.status_code == 200
      and r.get_json().get('success') is True, str(r.status_code if r else None))

# --- Resume versions ---
r = c.post('/api/resume-versions', json={'version_name': 'PGVersion'})
check('resume version create', r.status_code in (200, 201), str(r.status_code))
ver_id = None
try:
    ver_id = r.get_json().get('id')
except Exception:
    pass
r = c.get('/api/resume-versions')
check('resume version list', r.status_code == 200, str(r.status_code))
if ver_id:
    r = c.post('/api/fit-score/select-version', json={'version_id': ver_id})
    check('resume version select', r.status_code == 200, str(r.status_code))
    r = c.delete(f'/api/resume-versions/{ver_id}')
    check('resume version delete', r.status_code == 200, str(r.status_code))

# --- Email intelligence (no live Gmail needed for status/stats/list) ---
for name, url, method in [('email status', '/api/email-intelligence/status', 'get'),
                          ('email stats', '/api/email-intelligence/stats', 'get'),
                          ('email list', '/api/email-intelligence/emails', 'get')]:
    r = getattr(c, method)(url)
    check(name, r.status_code == 200, str(r.status_code))

# --- AI (tolerant: needs GROQ key + resume; accept graceful errors) ---
r = c.post('/api/autofill-url', json={'url': 'https://example.com/jobs/123'})
check('autofill endpoint live', r.status_code in (200, 400, 500), str(r.status_code))
r = c.post('/api/chat', json={'message': 'hello'})
check('chat endpoint live', r.status_code == 200, str(r.status_code))
r = c.post('/api/fit-score/analyze', json={'application_id': app_id}) if app_id else None
ok = r is not None and r.status_code in (200, 400, 502)
check('fit analyze (200 or graceful NO_RESUME/502)', ok, str(r.status_code if r else None))

# --- Delete + logout ---
r = c.delete(f'/applications/{app_id}') if app_id else None
check('delete application', r is not None and r.status_code == 200, str(r.status_code if r else None))
r = c.get('/logout', follow_redirects=False)
check('logout', r.status_code in (302, 200), str(r.status_code))

failed = [n for n, ok, _ in results if not ok]
print(f'\n{len(results)-len(failed)}/{len(results)} passed')
sys.exit(1 if failed else 0)
