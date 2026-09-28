"""SQLite -> PostgreSQL (Supabase) data migration. READ-ONLY on source.

Usage:
  set DATABASE_URL=postgresql://postgres:<pw>@db.<ref>.supabase.co:5432/postgres
  python scripts/migrate_sqlite_to_postgres.py [--sqlite database/tracker.db] [--truncate]

Behavior:
  - Never modifies the SQLite file (opens read-only via URI mode=ro).
  - Creates schema via database.db:init_db() against DATABASE_URL.
  - Copies tables in FK-safe order, preserving IDs (explicit id insert +
    setval() sequence reset afterwards).
  - Preserves NULLs, timestamps (as stored strings), statuses, JSON blobs.
  - Reports per-table counts + failures. Exit non-zero on any failure.

Tables (actual repo truth): users, user_settings, resume_versions,
applications, email_connections, email_messages, application_timeline_events.
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

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

TABLES_IN_ORDER = [
    'users',
    'user_settings',
    'resume_versions',
    'applications',
    'email_connections',
    'email_messages',
    'application_timeline_events',
]
# Tables with SERIAL sequences to reset after explicit-id inserts.
SEQUENCED = {'users', 'resume_versions', 'applications',
             'email_connections', 'email_messages', 'application_timeline_events'}


def get_pg_url():
    url = os.environ.get('DATABASE_URL', '').strip()
    if not url.startswith(('postgresql://', 'postgres://')):
        print('ERROR: DATABASE_URL env must be a postgresql:// URL (Supabase).', file=sys.stderr)
        print('Refusing to run without an explicit Postgres target.', file=sys.stderr)
        sys.exit(2)
    return url


def read_sqlite(path):
    # Read-only open: guarantees source DB is never modified.
    uri = f'file:{os.path.abspath(path)}?mode=ro'
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    data = {}
    for t in TABLES_IN_ORDER:
        rows = con.execute(f'SELECT * FROM "{t}"').fetchall()
        cols = [d[0] for d in con.execute(f'SELECT * FROM "{t}" LIMIT 0').description]
        data[t] = (cols, [dict(r) for r in rows])
    con.close()
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sqlite', default='database/tracker.db')
    ap.add_argument('--truncate', action='store_true',
                    help='TRUNCATE Postgres tables (CASCADE) before loading. Default: insert with ON CONFLICT DO NOTHING.')
    args = ap.parse_args()

    pg_url = get_pg_url()
    if not os.path.exists(args.sqlite):
        print(f'ERROR: sqlite file not found: {args.sqlite}', file=sys.stderr)
        sys.exit(2)

    # 1. Ensure Postgres schema exists (same code path as the Flask app).
    os.environ['DATABASE_URL'] = pg_url
    from database.db import init_db
    from config import Config

    class _Cfg(Config):
        pass
    # init_db reads current_app.config; emulate via minimal Flask app.
    from flask import Flask
    app = Flask(__name__)
    app.config.from_object(_Cfg)
    app.config['DATABASE_URL'] = pg_url
    app.config['DATABASE'] = pg_url
    with app.app_context():
        init_db()

    # 2. Read source (read-only).
    data = read_sqlite(args.sqlite)
    for t, (cols, rows) in data.items():
        print(f'SQLITE {t}: {len(rows)} rows, {len(cols)} cols')

    # 3. Load into Postgres (SAVEPOINT per row: one bad/orphan row never
    # wipes prior good inserts; SQLite source has known orphans, e.g.
    # user_settings.user_id=6 with no users row — skipped + logged).
    import psycopg
    failures = {}
    inserted = {}
    with psycopg.connect(pg_url) as conn:
        conn.autocommit = False
        with conn.cursor() as cur:
            if args.truncate:
                cur.execute(
                    'TRUNCATE application_timeline_events, email_messages, '
                    'email_connections, applications, resume_versions, '
                    'user_settings, users RESTART IDENTITY CASCADE'
                )
            for t in TABLES_IN_ORDER:
                cols, rows = data[t]
                ok = 0
                fail = 0
                for r in rows:
                    col_list = ', '.join(f'"{c}"' for c in cols)
                    ph = ', '.join(['%s'] * len(cols))
                    vals = [r[c] for c in cols]
                    cur.execute('SAVEPOINT mig_row')
                    try:
                        cur.execute(
                            f'INSERT INTO "{t}" ({col_list}) VALUES ({ph}) '
                            f'ON CONFLICT DO NOTHING', vals)
                        n = cur.rowcount or 0
                        cur.execute('RELEASE SAVEPOINT mig_row')
                        ok += n
                    except Exception as e:  # noqa: BLE001 - report then continue
                        cur.execute('ROLLBACK TO SAVEPOINT mig_row')
                        cur.execute('RELEASE SAVEPOINT mig_row')
                        fail += 1
                        failures.setdefault(t, []).append(
                            f"row id={r.get('id', r.get('user_id', '?'))}: {e}".split('\n')[0])
                inserted[t] = (ok, fail)
                print(f'PG LOAD {t}: inserted={ok} skipped/conflict-or-existing={len(rows)-ok-fail} failed={fail}')
                conn.commit()  # per-table commit: later tables never wipe earlier ones
            # Reset SERIAL sequences to max(id) so new app inserts don't collide.
            for t in SEQUENCED:
                try:
                    cur.execute(
                        f"SELECT setval(pg_get_serial_sequence('\"{t}\"', 'id'), "
                        f"COALESCE((SELECT MAX(id) FROM \"{t}\"), 1), true)")
                except Exception as e:  # noqa: BLE001
                    failures.setdefault(t, []).append(f'setval: {e}')
                    conn.rollback()
        conn.commit()

    if failures:
        print('\nFAILURES (orphan/conflict rows skipped, source untouched):')
        for t, errs in failures.items():
            print(f'  {t}: {len(errs)} errors, e.g. {errs[:2]}')
    print('\nDone. Source SQLite was NOT modified.')
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
