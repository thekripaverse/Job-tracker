"""Verify SQLite vs PostgreSQL row counts + FK integrity. Read-only on both.

Usage:
  set DATABASE_URL=postgresql://... (Supabase)
  python scripts/verify_migration.py [--sqlite database/tracker.db]

Prints TABLE/SQLITE/POSTGRES/DIFF table plus FK orphan checks. Exit 1 on diff.
"""
import argparse
import os
import sqlite3
import sys

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
except Exception:
    pass

TABLES = ['users', 'user_settings', 'resume_versions', 'applications',
          'email_connections', 'email_messages', 'application_timeline_events']

CHECKS = [
    ('orphan applications.user_id',
     'SELECT COUNT(*) FROM applications a LEFT JOIN users u ON a.user_id=u.id WHERE u.id IS NULL', 0),
    ('orphan timeline.application_id',
     'SELECT COUNT(*) FROM application_timeline_events t LEFT JOIN applications a ON t.application_id=a.id WHERE a.id IS NULL', 0),
    ('orphan timeline.user_id',
     'SELECT COUNT(*) FROM application_timeline_events t LEFT JOIN users u ON t.user_id=u.id WHERE u.id IS NULL', 0),
    ('orphan emails.user_id',
     'SELECT COUNT(*) FROM email_messages m LEFT JOIN users u ON m.user_id=u.id WHERE u.id IS NULL', 0),
    ('orphan versions.user_id',
     'SELECT COUNT(*) FROM resume_versions v LEFT JOIN users u ON v.user_id=u.id WHERE u.id IS NULL', 0),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sqlite', default='database/tracker.db')
    args = ap.parse_args()
    pg_url = os.environ.get('DATABASE_URL', '').strip()
    if not pg_url.startswith(('postgresql://', 'postgres://')):
        print('ERROR: set DATABASE_URL to your Supabase Postgres URL.', file=sys.stderr)
        sys.exit(2)

    scon = sqlite3.connect(f'file:{os.path.abspath(args.sqlite)}?mode=ro', uri=True)
    import psycopg
    pcon = psycopg.connect(pg_url)
    print(f"{'TABLE':28} {'SQLITE':>8} {'POSTGRES':>9} {'DIFF':>6}")
    bad = 0
    for t in TABLES:
        s = scon.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        with pcon.cursor() as cur:
            cur.execute(f'SELECT COUNT(*) FROM "{t}"')
            p = cur.fetchone()[0]
        d = p - s
        if d != 0:
            bad += 1
        print(f'{t:28} {s:8} {p:9} {d:6}')
    print('\nFK / ownership checks (both DBs, expect 0 orphans):')
    for label, sql, _ in CHECKS:
        s = scon.execute(sql).fetchone()[0]
        with pcon.cursor() as cur:
            cur.execute(sql)
            p = cur.fetchone()[0]
        flag = '' if (s == 0 and p == 0) else '  <-- ORPHANS!'
        if s != 0 or p != 0:
            bad += 1
        print(f'  {label:32} sqlite={s} pg={p}{flag}')
    scon.close()
    pcon.close()
    print('\nRESULT:', 'MISMATCH — investigate' if bad else 'OK — counts match, no orphans')
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
