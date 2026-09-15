#!/usr/bin/env python3
"""Authenticated backup / restore for SQLite and PostgreSQL.

SQLite path:
  - online backup via sqlite3 backup API
  - authenticated manifest
  - clean restore verification

PostgreSQL path (requires DATABASE_URL and pg_dump/pg_restore on PATH):
  - custom-format dump via pg_dump
  - restore via pg_restore --clean --if-exists
  - integrity via shared database abstraction

Legacy SQLite migration tools remain SQLite-only; this script is the supported
runtime recovery entrypoint for both backends.
"""
import argparse
import re
import json
import os
import sqlite3
from contextlib import closing
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse, unquote, parse_qsl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.manifest import create as create_manifest, verify as verify_manifest, file_sha256
from app.audit import verify as verify_audit
from app.database import (
    IS_POSTGRES,
    database_url_from_env,
    connect,
    integrity_check as db_integrity_check,
    get_schema_version,
    table_exists,
)

REQUIRED_TABLES = {
    'projects', 'sources', 'documents', 'claims', 'claim_reviews', 'audit_events'
}


def _pg_env():
    """Preserve libpq TLS policy and decode URI credentials, never on argv."""
    url = database_url_from_env()
    env = dict(os.environ)
    env.pop('PGPASSWORD', None)
    if not url:
        return env
    parsed = urlparse(url)
    if parsed.password:
        env['PGPASSWORD'] = unquote(parsed.password)
    supported = {
        'sslmode': 'PGSSLMODE', 'sslrootcert': 'PGSSLROOTCERT',
        'sslcert': 'PGSSLCERT', 'sslkey': 'PGSSLKEY',
        'connect_timeout': 'PGCONNECT_TIMEOUT',
        'application_name': 'PGAPPNAME', 'channel_binding': 'PGCHANNELBINDING',
    }
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key not in supported:
            raise ValueError('unsupported recovery connection parameter: ' + key)
        env[supported[key]] = value
    return env


def _redact_pg_text(text, parsed=None):
    """Remove credential material from tool output before raising/logging."""
    if text is None:
        return ''
    if isinstance(text, bytes):
        text = text.decode(errors='replace')
    url = database_url_from_env()
    if url:
        text = text.replace(url, '***REDACTED_DATABASE_URL***')
    if parsed is None and url:
        parsed = urlparse(url)
    if parsed is not None:
        if parsed.password:
            text = text.replace(parsed.password, '***REDACTED***')
            text = text.replace(unquote(parsed.password), '***REDACTED***')
        if parsed.username:
            # Avoid leaking username in odd error paths only when paired with host
            pass
        netloc = parsed.netloc or ''
        if netloc and netloc in text:
            host = parsed.hostname or ''
            port = parsed.port or 5432
            text = text.replace(netloc, f'***@{host}:{port}')
    # Generic patterns
    text = re.sub(r'postgres(?:ql)?://[^\s]+', '***REDACTED_DATABASE_URL***', text)
    return text


def integrity(path=None, audit_key=None):
    """Validate DB integrity and optionally the audit chain."""
    if IS_POSTGRES:
        c = connect()
        try:
            result = db_integrity_check(c)
            if result != 'ok':
                raise RuntimeError(f'invalid database: integrity={result}')
            audit = {'events': 0, 'head': ''}
            if audit_key:
                audit = verify_audit(c, audit_key)
            tables = sum(1 for t in REQUIRED_TABLES if table_exists(c, t))
            return {
                'integrity': result,
                'tables': tables,
                'user_version': get_schema_version(c),
                'audit_events': audit['events'],
                'audit_head': audit['head'],
            }
        finally:
            c.close()

    if path is None:
        raise ValueError('path is required for SQLite integrity checks')
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    try:
        result = c.execute('PRAGMA integrity_check').fetchone()[0]
        tables = {
            r[0]
            for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if result != 'ok' or not REQUIRED_TABLES.issubset(tables):
            raise RuntimeError(
                f'invalid database: integrity={result}, missing={sorted(REQUIRED_TABLES - tables)}'
            )
        audit = {'events': 0, 'head': ''}
        if 'audit_checkpoints' in tables:
            if not audit_key:
                raise RuntimeError('audit key required for checkpoint verification')
            audit = verify_audit(c, audit_key)
        return {
            'integrity': result,
            'tables': len(tables),
            'user_version': c.execute('PRAGMA user_version').fetchone()[0],
            'audit_events': audit['events'],
            'audit_head': audit['head'],
        }
    finally:
        c.close()


def backup(source, destination, key=None, audit_key=None):
    key = key or os.getenv('BACKUP_HMAC_KEY', 'development-backup-key-not-for-production')
    audit_key = audit_key or os.getenv('AUDIT_HMAC_KEY', 'development-audit-key-not-for-production')
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_suffix(destination.suffix + '.creating')
    if tmp.exists():
        tmp.unlink()

    if IS_POSTGRES:
        url = database_url_from_env()
        if not url:
            raise RuntimeError('DATABASE_URL is required for PostgreSQL backup')
        # Parse DATABASE_URL into host/port/db/user components to avoid
        # passing credentials on the command line (visible in ps aux).
        parsed = urlparse(url)
        pg_host = parsed.hostname or 'localhost'
        pg_port = str(parsed.port or 5432)
        pg_db = unquote(parsed.path.lstrip('/'))
        pg_user = unquote(parsed.username or '')
        result = subprocess.run(
            ['pg_dump', '--host', pg_host, '--port', pg_port,
             '--username', pg_user, '--format=custom',
             f'--file={tmp}', pg_db],
            capture_output=True,
            env=_pg_env(),
        )
        if result.returncode:
            stderr = _redact_pg_text(result.stderr, parsed)
            raise RuntimeError(f'pg_dump failed: {stderr}')
        checks = integrity(None, audit_key)
        os.chmod(tmp, 0o600)
        tmp.replace(destination)
    else:
        source = Path(source)
        if not source.is_file():
            raise FileNotFoundError(source)
        with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(tmp)) as dst:
            src.backup(dst)
        checks = integrity(tmp, audit_key)
        os.chmod(tmp, 0o600)
        tmp.replace(destination)

    manifest = destination.with_suffix(destination.suffix + '.manifest.json')
    create_manifest(manifest, destination, key, checks)
    return {
        'operation': 'backup',
        'path': str(destination),
        'manifest': str(manifest),
        'sha256': file_sha256(destination),
        **checks,
    }


def restore(source, destination, force=False, key=None, audit_key=None, manifest=None):
    key = key or os.getenv('BACKUP_HMAC_KEY', 'development-backup-key-not-for-production')
    audit_key = audit_key or os.getenv('AUDIT_HMAC_KEY', 'development-audit-key-not-for-production')
    source = Path(source)
    manifest = Path(manifest) if manifest else source.with_suffix(source.suffix + '.manifest.json')

    # Authenticate before any destructive action, for BOTH backends.
    verify_manifest(manifest, source, key)

    if IS_POSTGRES:
        if not force:
            raise RuntimeError('PostgreSQL restore replaces DATABASE_URL; explicit --force required')
        url = database_url_from_env()
        if not url:
            raise RuntimeError('DATABASE_URL is required for PostgreSQL restore')
        parsed = urlparse(url)
        pg_host = parsed.hostname or 'localhost'
        pg_port = str(parsed.port or 5432)
        pg_db = unquote(parsed.path.lstrip('/'))
        pg_user = unquote(parsed.username or '')
        result = subprocess.run(
            ['pg_restore', '--host', pg_host, '--port', pg_port,
             '--username', pg_user, '--clean', '--if-exists',
             '--single-transaction', '--exit-on-error',
             '--dbname', pg_db, str(source)],
            capture_output=True,
            env=_pg_env(),
        )
        if result.returncode:
            stderr = _redact_pg_text(result.stderr, parsed)
            raise RuntimeError(f'pg_restore failed: {stderr}')
        restored = integrity(None, audit_key)
        return {
            'operation': 'restore',
            'path': 'DATABASE_URL',
            'sha256': file_sha256(source),
            **restored,
        }

    # SQLite path
    destination = Path(destination)

    source_checks = integrity(source, audit_key)
    if destination.exists() and not force:
        raise FileExistsError(f'{destination} exists; pass --force')
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_suffix(destination.suffix + '.restoring')
    if tmp.exists():
        tmp.unlink()
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(tmp)) as dst:
        src.backup(dst)
    restored = integrity(tmp, audit_key)
    if restored != source_checks:
        raise RuntimeError('restored database verification differs from source')
    os.chmod(tmp, 0o600)
    tmp.replace(destination)
    return {
        'operation': 'restore',
        'path': str(destination),
        'sha256': file_sha256(destination),
        **restored,
    }


def main():
    p = argparse.ArgumentParser(description='Authenticated backup/restore')
    sub = p.add_subparsers(dest='command', required=True)
    for name in ('backup', 'restore'):
        x = sub.add_parser(name)
        x.add_argument('source')
        x.add_argument('destination')
        x.add_argument('--force', action='store_true')
        x.add_argument('--manifest')
    a = p.parse_args()
    try:
        if a.command == 'backup':
            result = backup(a.source, a.destination)
        else:
            result = restore(a.source, a.destination, a.force, manifest=a.manifest)
    except Exception as e:
        print(json.dumps({'status': 'error', 'error': str(e)}))
        return 1
    print(json.dumps({'status': 'ok', **result}, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
