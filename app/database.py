#!/usr/bin/env python3
"""Database abstraction layer supporting SQLite and PostgreSQL.

When DATABASE_URL is set (e.g. postgresql://user:pass@host:5432/dbname),
this module uses psycopg2 with a connection pool. Otherwise it falls
back to SQLite using the DB_PATH environment variable.

The rest of the application interacts only with the context manager
and helper functions exported here; no other module imports sqlite3
or psycopg2 directly.
"""

import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

try:
    import psycopg2
    from psycopg2 import pool as pg_pool
    from psycopg2.extras import RealDictCursor
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False

ROOT = Path(__file__).resolve().parents[1]


def database_url_from_env(env=None):
    """Build a PostgreSQL DSN from ECS-injected secret fields.

    ``DATABASE_URL`` remains supported for local/container deployments. On
    ECS, username/password are injected as separate Secrets Manager JSON
    fields so the complete credential never appears in OpenTofu state.
    """
    env = os.environ if env is None else env
    explicit = str(env.get('DATABASE_URL', '')).strip()
    if explicit:
        return explicit
    required = ('DB_HOST', 'DB_NAME', 'DB_USERNAME', 'DB_PASSWORD')
    if not all(str(env.get(name, '')).strip() for name in required):
        return ''
    host = str(env['DB_HOST']).strip()
    if any(ch in host for ch in '/?#@'):
        raise RuntimeError('invalid DB_HOST')
    port = str(env.get('DB_PORT', '5432')).strip()
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise RuntimeError('invalid DB_PORT')
    name = str(env['DB_NAME']).strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,63}', name):
        raise RuntimeError('invalid DB_NAME')
    sslmode = str(env.get('DB_SSLMODE', 'require')).strip()
    if sslmode not in {'require', 'verify-ca', 'verify-full'}:
        raise RuntimeError('unsafe DB_SSLMODE')
    user = quote(str(env['DB_USERNAME']), safe='')
    password = quote(str(env['DB_PASSWORD']), safe='')
    return f'postgresql://{user}:{password}@{host}:{port}/{name}?sslmode={sslmode}'


DATABASE_URL = database_url_from_env()
DB_PATH = Path(os.getenv('DB_PATH', str(ROOT / 'data/project_xray.db')))

# Dialect flag
IS_POSTGRES = bool(DATABASE_URL)

_pool = None
_pool_slots = None
_pool_lock = __import__('threading').Lock()


def _get_pool():
    """Thread-safe lazy initialization of the PostgreSQL connection pool."""
    global _pool, _pool_slots
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is None and HAS_PSYCOPG2 and DATABASE_URL:
            capacity = int(os.getenv('DB_POOL_MAX', '10'))
            if capacity < 1:
                raise ValueError('DB_POOL_MAX must be positive')
            _pool_slots = __import__('threading').BoundedSemaphore(capacity)
            _pool = pg_pool.ThreadedConnectionPool(
                minconn=min(2, capacity),
                maxconn=capacity,
                dsn=DATABASE_URL,
                cursor_factory=RealDictCursor,
            )
    return _pool


def _convert_sql(query):
    """Convert SQLite-style ? placeholders to PostgreSQL %s.

    Replaces bind-parameter ``?`` tokens only. Question marks inside:

    - single-quoted string literals (with '' escape)
    - double-quoted identifiers
    - PostgreSQL dollar-quoted strings ($$...$$ / $tag$...$tag$)
    - line comments (-- ...)
    - block comments (/* ... */)

    are left unchanged. Unclosed quotes/comments remain deterministic:
    the remainder of the query stays in that mode and is not converted.
    """
    if not IS_POSTGRES:
        return query
    if not isinstance(query, str):
        return query

    result = []
    i = 0
    n = len(query)
    # modes: code | sq | dq | dollar | line_comment | block_comment
    mode = 'code'
    dollar_tag = None

    while i < n:
        ch = query[i]

        if mode == 'sq':
            result.append(ch)
            if ch == "'":
                if i + 1 < n and query[i + 1] == "'":
                    result.append(query[i + 1])
                    i += 2
                    continue
                mode = 'code'
            i += 1
            continue

        if mode == 'dq':
            result.append(ch)
            if ch == '"':
                if i + 1 < n and query[i + 1] == '"':
                    result.append(query[i + 1])
                    i += 2
                    continue
                mode = 'code'
            i += 1
            continue

        if mode == 'dollar':
            # Look for closing $tag$
            if ch == '$' and query.startswith(dollar_tag, i):
                result.append(dollar_tag)
                i += len(dollar_tag)
                mode = 'code'
                dollar_tag = None
                continue
            result.append(ch)
            i += 1
            continue

        if mode == 'line_comment':
            result.append(ch)
            if ch == '\n':
                mode = 'code'
            i += 1
            continue

        if mode == 'block_comment':
            if ch == '*' and i + 1 < n and query[i + 1] == '/':
                result.append('*/')
                i += 2
                mode = 'code'
                continue
            result.append(ch)
            i += 1
            continue

        # mode == 'code'
        if ch == "'":
            mode = 'sq'
            result.append(ch)
            i += 1
            continue
        if ch == '"':
            mode = 'dq'
            result.append(ch)
            i += 1
            continue
        if ch == '-' and i + 1 < n and query[i + 1] == '-':
            mode = 'line_comment'
            result.append('--')
            i += 2
            continue
        if ch == '/' and i + 1 < n and query[i + 1] == '*':
            mode = 'block_comment'
            result.append('/*')
            i += 2
            continue
        if ch == '$':
            # Dollar-quote start: $$ or $tag$
            m = re.match(r'\$([A-Za-z_][A-Za-z0-9_]*)?\$', query[i:])
            if m:
                dollar_tag = m.group(0)
                result.append(dollar_tag)
                i += len(dollar_tag)
                mode = 'dollar'
                continue
            result.append(ch)
            i += 1
            continue
        if ch == '?':
            result.append('%s')
            i += 1
            continue
        # Escape bare % for psycopg2, but preserve existing pyformat tokens:
        # %%, %s, and %(name)s must pass through unchanged so native PG SQL works.
        if ch == '%':
            rest = query[i:]
            if rest.startswith('%%'):
                result.append('%%')
                i += 2
                continue
            if rest.startswith('%s'):
                result.append('%s')
                i += 2
                continue
            m = re.match(r'%\([A-Za-z_][A-Za-z0-9_]*\)s', rest)
            if m:
                result.append(m.group(0))
                i += len(m.group(0))
                continue
            result.append('%%')
            i += 1
            continue
        result.append(ch)
        i += 1

    return ''.join(result)


class RowAdapter:
    """Wraps a psycopg2 RealDictRow or sqlite3.Row for dict-like access."""

    def __init__(self, row):
        if isinstance(row, dict):
            self._data = row
        elif isinstance(row, sqlite3.Row):
            self._data = dict(row)
        else:
            self._data = dict(row)

    def __getitem__(self, key):
        return self._data[key]

    def __contains__(self, key):
        return key in self._data

    def get(self, key, default=None):
        return self._data.get(key, default)

    def keys(self):
        return self._data.keys()

    def items(self):
        return self._data.items()

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)

    def __repr__(self):
        return repr(self._data)


class CursorAdapter:
    """Wraps a cursor to convert ? placeholders and normalize row types."""

    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, query, params=()):
        try:
            self._cursor.execute(_convert_sql(query), params)
        except Exception as e:
            # Wrap sqlite3.IntegrityError and psycopg2.IntegrityError
            if isinstance(e, sqlite3.IntegrityError):
                raise IntegrityError(str(e))
            if HAS_PSYCOPG2 and isinstance(e, psycopg2.IntegrityError):
                raise IntegrityError(str(e))
            if isinstance(e, sqlite3.OperationalError) and ('locked' in str(e).lower() or 'busy' in str(e).lower()):
                raise DatabaseBusy('database is busy, retry') from e
            raise
        return self

    def executemany(self, query, params_seq):
        try:
            self._cursor.executemany(_convert_sql(query), params_seq)
        except Exception as e:
            if isinstance(e, sqlite3.IntegrityError):
                raise IntegrityError(str(e))
            if HAS_PSYCOPG2 and isinstance(e, psycopg2.IntegrityError):
                raise IntegrityError(str(e))
            if isinstance(e, sqlite3.OperationalError) and ('locked' in str(e).lower() or 'busy' in str(e).lower()):
                raise DatabaseBusy('database is busy, retry') from e
            raise

    def executescript(self, script):
        if IS_POSTGRES:
            self._cursor.execute(script)
        else:
            self._cursor.executescript(script)

    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        return RowAdapter(row)

    def fetchall(self):
        return [RowAdapter(r) for r in self._cursor.fetchall()]

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def lastrowid(self):
        if IS_POSTGRES:
            return None
        return self._cursor.lastrowid

    def close(self):
        self._cursor.close()

    def __iter__(self):
        for row in self._cursor:
            yield RowAdapter(row)


class ConnectionAdapter:
    """Wraps a connection to provide a uniform interface."""

    def __init__(self, conn):
        self._conn = conn
        self._is_pg = IS_POSTGRES

    def cursor(self):
        if self._is_pg:
            return CursorAdapter(self._conn.cursor(cursor_factory=RealDictCursor))
        cur = self._conn.cursor()
        cur.row_factory = sqlite3.Row
        return CursorAdapter(cur)

    def commit(self):
        try:
            self._conn.commit()
        except Exception as e:
            if isinstance(e, sqlite3.OperationalError) and ('locked' in str(e).lower() or 'busy' in str(e).lower()):
                raise DatabaseBusy('database is busy, retry') from e
            raise

    def rollback(self):
        self._conn.rollback()

    def close(self):
        if self._is_pg:
            try:
                _get_pool().putconn(self._conn)
            finally:
                if getattr(self, '_pool_slot', False):
                    self._pool_slot = False
                    _pool_slots.release()
        else:
            self._conn.close()

    def backup(self, dst_conn):
        """SQLite-only: backup to another SQLite connection."""
        if self._is_pg:
            raise RuntimeError('Use pg_dump for PostgreSQL backup')
        if hasattr(dst_conn, '_conn'):
            self._conn.backup(dst_conn._conn)
        else:
            self._conn.backup(dst_conn)

    def execute(self, query, params=()):
        cur = self.cursor()
        cur.execute(query, params)
        return cur

    def executescript(self, script):
        cur = self.cursor()
        cur.executescript(script)
        cur.close()
        return self

    @property
    def row_factory(self):
        return None

    @row_factory.setter
    def row_factory(self, value):
        # No-op for PostgreSQL; SQLite uses cursor-level setting
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


_WAL_LOCK = __import__('threading').Lock()
_WAL_READY = set()


def _wal_identity(path):
    try:
        st = path.stat()
        return (str(path), st.st_dev, st.st_ino)
    except OSError:
        return (str(path), None, None)


def _ensure_wal(conn, path):
    """Switch the database file to WAL exactly once per process.

    ``PRAGMA journal_mode=WAL`` takes an exclusive lock on the file even when
    the mode is already WAL, so issuing it on every ``connect()`` serialises
    every reader behind every other connection under load. Journal mode is a
    persistent property of the file, so one successful switch is sufficient;
    the (path, device, inode) key re-arms the switch if the file is recreated.
    """
    key = _wal_identity(path)
    if key in _WAL_READY:
        return
    with _WAL_LOCK:
        if key in _WAL_READY:
            return
        mode = conn.execute('PRAGMA journal_mode=WAL').fetchone()[0]
        if str(mode).lower() == 'wal':
            _WAL_READY.add(key)


def reset_wal_cache():
    """Forget WAL bookkeeping (tests / after deleting database files)."""
    with _WAL_LOCK:
        _WAL_READY.clear()


def connect(path=None):
    """Return a ConnectionAdapter for the configured database."""
    if IS_POSTGRES:
        if not HAS_PSYCOPG2:
            raise RuntimeError('DATABASE_URL is set but psycopg2 is not installed')
        p = _get_pool()
        if not _pool_slots.acquire(timeout=5):
            raise DatabaseBusy('database capacity temporarily unavailable')
        conn = None
        try:
            conn = p.getconn()
            adapter = ConnectionAdapter(conn)
            adapter._pool_slot = True
            adapter.execute('SET statement_timeout = %s', (int(os.getenv('DB_STATEMENT_TIMEOUT_MS', '30000')),))
            return adapter
        except Exception:
            if conn is not None:
                p.putconn(conn, close=True)
            _pool_slots.release()
            raise
    else:
        p = Path(path or DB_PATH)
        p.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(p), timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('PRAGMA busy_timeout=10000')
        _ensure_wal(conn, p)
        conn.execute('PRAGMA synchronous=NORMAL')
        return ConnectionAdapter(conn)


@contextmanager
def db(write=False):
    """Context manager yielding a connection. Commits on write, rolls back on error."""
    c = connect()
    try:
        if write and not IS_POSTGRES:
            c.execute('BEGIN IMMEDIATE')
        elif write and IS_POSTGRES:
            # Serialize state transitions and the single global audit chain.
            c.execute('SELECT pg_advisory_xact_lock(1481785689)')
        yield c
        if write:
            c.commit()
    except Exception:
        if write:
            c.rollback()
        raise
    finally:
        c.close()


def get_schema_version(c):
    """Return the schema version integer."""
    if IS_POSTGRES:
        row = c.execute(
            "SELECT value FROM schema_version WHERE id = 1"
        ).fetchone()
        return int(row['value']) if row else 0
    else:
        row = c.execute('PRAGMA user_version').fetchone()
        # PRAGMA returns a row with integer-indexed columns
        if isinstance(row, RowAdapter):
            # Try string key first, then integer index
            try:
                return int(row['user_version'])
            except (KeyError, TypeError):
                return int(list(row._data.values())[0])
        return int(row[0])


def set_schema_version(c, version):
    """Set the schema version."""
    if IS_POSTGRES:
        c.execute(
            "UPDATE schema_version SET value = %s WHERE id = 1",
            (version,),
        )
    else:
        c.execute(f'PRAGMA user_version = {version}')


def table_exists(c, table_name):
    """Check if a table exists."""
    if IS_POSTGRES:
        row = c.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = %s",
            (table_name,),
        ).fetchone()
        return row is not None
    else:
        row = c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        return row is not None


def integrity_check(c):
    """Run database integrity check."""
    if IS_POSTGRES:
        # PostgreSQL doesn't have a single integrity check; verify core tables exist
        required = {'projects', 'sources', 'documents', 'claims', 'claim_reviews',
                    'audit_events', 'audit_checkpoints', 'idempotency_keys'}
        existing = set()
        for row in c.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        ).fetchall():
            existing.add(row['table_name'])
        missing = required - existing
        if missing:
            return f'missing tables: {sorted(missing)}'
        return 'ok'
    else:
        return c.execute('PRAGMA integrity_check').fetchone()[0]


class DatabaseBusy(Exception):
    """Admission timed out; clients can retry with the same idempotency key."""


class IntegrityError(Exception):
    """Raised on constraint violations. Maps sqlite3.IntegrityError and psycopg2 errors."""


def wrap_integrity_error(func):
    """Decorator to wrap database integrity errors uniformly."""
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except sqlite3.IntegrityError:
            raise IntegrityError('constraint violation')
        except Exception as e:
            if HAS_PSYCOPG2 and isinstance(e, psycopg2.IntegrityError):
                raise IntegrityError(str(e))
            raise
    return wrapper
