#!/usr/bin/env python3
import collections
import hmac
import hashlib
import ipaddress
import functools
import json
import math
import os
import re
import secrets
import signal
import time
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    from app import telemetry
    from app.audit import append as append_audit, verify as verify_audit, verify_head as verify_audit_head, verify_segment as verify_audit_segment, verify_orphans as verify_audit_orphans
    from app.capabilities import CapabilityPolicy, denial_reason
    from app.database import connect, db, IS_POSTGRES, IntegrityError, DatabaseBusy, get_schema_version, set_schema_version, table_exists, integrity_check
    from app.operations import send_alert
    from app.security import token_hash, verify_gateway_assertion, verify_proxy
    from app.storage import settings_from_env, verify_managed_object
except ModuleNotFoundError:
    import telemetry
    from audit import append as append_audit, verify as verify_audit, verify_head as verify_audit_head, verify_segment as verify_audit_segment, verify_orphans as verify_audit_orphans
    from capabilities import CapabilityPolicy, denial_reason
    from database import connect, db, IS_POSTGRES, IntegrityError, DatabaseBusy, get_schema_version, set_schema_version, table_exists, integrity_check
    from operations import send_alert
    from security import token_hash, verify_gateway_assertion, verify_proxy
    from storage import settings_from_env, verify_managed_object

ROOT = Path(__file__).resolve().parents[1]
DB = Path(os.getenv('DB_PATH', str(ROOT / 'data/project_xray.db')))
PORT = int(os.getenv('PORT', '8080'))
MAX = int(os.getenv('MAX_BODY_BYTES', '2097152'))
ENV = os.getenv('APP_ENV', 'development')
PUBLIC_BASE_URL = os.getenv('PUBLIC_BASE_URL', 'http://localhost:8080')

PUBLIC_READ_RATE_LIMIT = int(os.getenv('PUBLIC_READ_RATE_LIMIT', '300'))
AUTH_READ_RATE_LIMIT = int(os.getenv('AUTH_READ_RATE_LIMIT', '120'))
WRITE_RATE_LIMIT = int(os.getenv('WRITE_RATE_LIMIT', '60'))
EXPENSIVE_WRITE_RATE_LIMIT = int(os.getenv('EXPENSIVE_WRITE_RATE_LIMIT', '15'))
TRUST_PROXY_HEADERS = os.getenv('TRUST_PROXY_HEADERS', '0') == '1'
_RATE_LOCK = threading.Lock()
_METRICS_LOCK = threading.Lock()
RATE = {}
METRICS = {
    'requests': 0,
    'errors': 0,
    'writes': 0,
    'auth_failures': 0,
    'publications': 0,
    'rate_limited': 0,
    'idempotency_replays': 0,
    'idempotency_conflicts': 0,
    'idempotency_stuck_reclaims': 0,
    'idempotency_lease_lost': 0,
    'quarantine_blocks': 0,
    'readyz_verify_inflight': 0,
    'readyz_verify_coalesced': 0,
    'readyz_verify_events_scanned': 0,
    'readyz_verify_completed': 0,
    'readyz_verify_budget_exhausted': 0,
    'readyz_verify_ms': 0,
}


def _metric_inc(name, amount=1):
    with _METRICS_LOCK:
        METRICS[name] = METRICS.get(name, 0) + amount


def metrics_snapshot():
    with _METRICS_LOCK:
        return dict(METRICS)


# --- Abuse read model (BACKLOG P1: rate limits + abuse dashboard) ---------
# Bounded, privacy-preserving offender ledger.  Client identities are never
# stored raw: they are HMAC-SHA256 digests (truncated) keyed with the token
# pepper, so the dashboard can correlate repeat offenders without retaining
# IP addresses.  The ledger is hard-capped (ABUSE_MAX_OFFENDERS) and evicts the
# least-recently-seen entry, so hostile traffic cannot grow memory unboundedly.
ABUSE_MAX_OFFENDERS = max(16, int(os.getenv('ABUSE_MAX_OFFENDERS', '1024')))
ABUSE_TOP_N = 10
_ABUSE_LOCK = threading.Lock()
_ABUSE = collections.OrderedDict()
_ABUSE_BY_CATEGORY = {}


def _abuse_fingerprint(identity):
    key = os.getenv('TOKEN_PEPPER', 'development-token-pepper-not-for-production').encode()
    return hmac.new(key, str(identity).encode('utf-8', 'replace'), hashlib.sha256).hexdigest()[:16]


def record_abuse(category, identity, when=None):
    fp = _abuse_fingerprint(identity)
    ts = int(when if when is not None else time.time())
    with _ABUSE_LOCK:
        entry = _ABUSE.pop(fp, None) or {'fingerprint': fp, 'total': 0, 'categories': {}, 'first_seen': ts}
        entry['total'] += 1
        entry['categories'][category] = entry['categories'].get(category, 0) + 1
        entry['last_seen'] = ts
        _ABUSE[fp] = entry
        while len(_ABUSE) > ABUSE_MAX_OFFENDERS:
            _ABUSE.popitem(last=False)
        _ABUSE_BY_CATEGORY[category] = _ABUSE_BY_CATEGORY.get(category, 0) + 1


def abuse_snapshot(top_n=ABUSE_TOP_N):
    with _ABUSE_LOCK:
        offenders = sorted(
            (dict(e, categories=dict(e['categories'])) for e in _ABUSE.values()),
            key=lambda e: (-e['total'], e['fingerprint']),
        )[: max(0, int(top_n))]
        by_category = dict(_ABUSE_BY_CATEGORY)
        tracked = len(_ABUSE)
    with _RATE_LOCK:
        minute = int(time.time() // 60)
        active = {}
        for (category, _ident, m), count in RATE.items():
            if m == minute:
                active[category] = active.get(category, 0) + 1
    return {
        'limits_per_minute': {
            'public_read': PUBLIC_READ_RATE_LIMIT,
            'auth_read': AUTH_READ_RATE_LIMIT,
            'write': WRITE_RATE_LIMIT,
            'expensive_write': EXPENSIVE_WRITE_RATE_LIMIT,
        },
        'rate_limited_total': metrics_snapshot().get('rate_limited', 0),
        'rate_limited_by_category': by_category,
        'active_clients_current_minute': active,
        'tracked_offenders': tracked,
        'tracked_offenders_cap': ABUSE_MAX_OFFENDERS,
        'top_offenders': offenders,
        'identity_privacy': 'hmac-sha256-truncated; raw client addresses are never stored',
    }


def reset_abuse_state():
    with _ABUSE_LOCK:
        _ABUSE.clear()
        _ABUSE_BY_CATEGORY.clear()

TOKEN_PEPPER = os.getenv('TOKEN_PEPPER', 'development-token-pepper-not-for-production')
AUDIT_KEY = os.getenv('AUDIT_HMAC_KEY', 'development-audit-key-not-for-production')
OIDC_SECRET = os.getenv('OIDC_PROXY_SECRET', '')
GATEWAY_ASSERTION_VERSION = os.getenv(
    'GATEWAY_ASSERTION_VERSION', '1' if ENV == 'production' else 'legacy'
)
GATEWAY_ASSERTION_AUDIENCE = os.getenv('GATEWAY_ASSERTION_AUDIENCE', '')
GATEWAY_ASSERTION_ISSUERS = frozenset(
    value.strip()
    for value in os.getenv('GATEWAY_ASSERTION_ISSUERS', '').split(',')
    if value.strip()
)
GATEWAY_ASSERTION_KEY_ID = os.getenv('GATEWAY_ASSERTION_KEY_ID', '')
BACKUP_KEY = os.getenv('BACKUP_HMAC_KEY', '')
ADMIN_TOKEN = os.getenv('ADMIN_TOKEN', 'change-before-deploy')
REVIEWER_TOKENS = {
    k: v
    for k, v in (
        x.split(':', 1)
        for x in os.getenv('REVIEWER_TOKENS', '').split(',')
        if ':' in x
    )
}
SCANNER_TOKENS = {
    k: v
    for k, v in (
        x.split(':', 1)
        for x in os.getenv('SCANNER_TOKENS', '').split(',')
        if ':' in x
    )
}

CLAIM_TYPES = {
    'verified_fact',
    'reported_allegation',
    'official_claim',
    'expert_assessment',
    'data_inconsistency',
    'audit_finding',
    'court_finding',
}
PUBLIC_STATES = {'published', 'disputed', 'corrected', 'withdrawn'}
ID_RE = re.compile(r'^[a-z]{3}_[a-f0-9]{16}$')
IDEMPOTENCY_STUCK_FLOOR_SECONDS = 30


def resolve_stuck_seconds(raw):
    """Parse IDEMPOTENCY_STUCK_SECONDS and clamp it to a safe floor.

    A reservation younger than the per-request socket timeout (15 s) can
    still be owned by a live worker; reclaiming it would let a same-key
    retry create a duplicate resource.  Values below the floor (including 0
    and negatives) are therefore raised to the floor, never honoured.
    """
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        value = 300
    return max(IDEMPOTENCY_STUCK_FLOOR_SECONDS, value)


IDEMPOTENCY_STUCK_SECONDS = resolve_stuck_seconds(os.getenv('IDEMPOTENCY_STUCK_SECONDS', '300'))


class IdempotencyLeaseLost(Exception):
    """The reservation this worker held was reclaimed by another request."""


def idempotency_age_seconds(created_at):
    """Age of an idempotency reservation; None if the timestamp is unusable."""
    try:
        created = datetime.fromisoformat(created_at)
    except (TypeError, ValueError):
        return None
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - created).total_seconds()


def now():
    return datetime.now(timezone.utc).isoformat()


def uid(prefix):
    return prefix + '_' + uuid.uuid4().hex[:16]


# Kubernetes-style probe aliases: /healthz and /livez mirror /health (process alive), /readyz mirrors /ready (DB + audit chain verified).
# /healthz is the canonical liveness path named in the production runbook; /health and /livez are kept as compatibility aliases.
LIVENESS_PATHS = frozenset({'/health', '/healthz', '/livez'})
READINESS_PATHS = frozenset({'/ready', '/readyz'})
PROBE_PATHS = LIVENESS_PATHS | READINESS_PATHS


def connect_db(path=None):
    """Connect using the database abstraction layer."""
    return connect(path)


@contextmanager
def db(write=False):
    c = connect()
    try:
        if write and not IS_POSTGRES:
            c.execute('BEGIN IMMEDIATE')
        elif write and IS_POSTGRES:
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


def bootstrap(c, principal, role, secret, ttl=86400):
    if not secret or secret.startswith('change-'):
        return
    created = now()
    expires = (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat()
    digest = token_hash(secret, TOKEN_PEPPER)
    c.execute(
        'INSERT' + (' OR IGNORE' if not IS_POSTGRES else '') + ' INTO auth_tokens(id,principal,role,token_hash,expires_at,created_at) VALUES(?,?,?,?,?,?)'
        + (' ON CONFLICT DO NOTHING' if IS_POSTGRES else ''),
        (uid('tok'), principal, role, digest, expires, created),
    )


def init():
    capability_policy = CapabilityPolicy.from_mapping(os.environ)
    if not capability_policy.valid:
        raise RuntimeError('invalid operational capability configuration')
    if ENV == 'production':
        # A complete static credential pair remains supported for non-AWS
        # S3-compatible storage.  If both are absent, botocore resolves the
        # AWS default chain (including ECS task-role credentials).
        settings_from_env()
        required = [
            ('PUBLIC_BASE_URL', PUBLIC_BASE_URL.startswith('https://')),
            ('TOKEN_PEPPER', len(TOKEN_PEPPER) >= 32),
            ('AUDIT_HMAC_KEY', len(AUDIT_KEY) >= 32),
            ('BACKUP_HMAC_KEY', len(BACKUP_KEY) >= 32),
            ('OIDC_PROXY_SECRET', len(OIDC_SECRET) >= 32),
            ('GATEWAY_ASSERTION_VERSION', GATEWAY_ASSERTION_VERSION == '1'),
            ('GATEWAY_ASSERTION_AUDIENCE', bool(GATEWAY_ASSERTION_AUDIENCE)),
            ('GATEWAY_ASSERTION_ISSUERS', bool(GATEWAY_ASSERTION_ISSUERS)),
            ('GATEWAY_ASSERTION_KEY_ID', bool(GATEWAY_ASSERTION_KEY_ID)),
            ('OBJECT_STORAGE_MODE', os.getenv('OBJECT_STORAGE_MODE') == 'managed'),
            ('STORAGE_BUCKET', bool(os.getenv('STORAGE_BUCKET'))),
            ('MONITORING_WEBHOOK_URL', bool(os.getenv('MONITORING_WEBHOOK_URL'))),
            ('MONITORING_WEBHOOK_SECRET', len(os.getenv('MONITORING_WEBHOOK_SECRET', '')) >= 32),
        ]
        missing = [name for name, ok in required if not ok]
        if missing:
            raise RuntimeError('production configuration missing/unsafe: ' + ','.join(missing))

    with db(True) as c:
        if table_exists(c, 'sources') and get_schema_version(c) not in (0, 3):
            raise RuntimeError(
                'database migration required; run scripts/migrate_v2_to_v3.py '
                '(SQLite-only legacy path) or apply operator-managed PostgreSQL migrations'
            )
        if IS_POSTGRES:
            schema_path = ROOT / 'db/schema_postgres.sql'
            c.executescript(schema_path.read_text())
        else:
            c.executescript((ROOT / 'db/schema.sql').read_text())
        ttl = int(os.getenv('BOOTSTRAP_TOKEN_TTL_SECONDS', '86400'))
        bootstrap(c, 'admin', 'admin', ADMIN_TOKEN, ttl)
        for principal, secret in REVIEWER_TOKENS.items():
            bootstrap(c, principal, 'reviewer', secret, ttl)
        for principal, secret in SCANNER_TOKENS.items():
            bootstrap(c, principal, 'scanner', secret, ttl)
        verify_audit(c, AUDIT_KEY)


# /readyz previously re-verified the whole audit chain on every probe: O(n) CPU
# per probe turned readiness checks into a self-inflicted DoS as the chain grew.
# Steady state now costs O(1): every probe validates the signed head checkpoint
# (HMAC over event_id|count|head_hash), and the full-chain walk re-runs only when
# the head moved past the last fully-verified state or the periodic re-verify
# window (READYZ_FULL_VERIFY_INTERVAL_SECONDS, default 300, 0 = every probe) lapses.
READYZ_FULL_VERIFY_INTERVAL = float(os.getenv('READYZ_FULL_VERIFY_INTERVAL_SECONDS', '300'))
# Cold/head-change verification used to walk the whole chain inside the probe
# request, so a 100k-event ledger blew far past the 200 ms recovery ceiling and
# every concurrent probe started its own duplicate scan. The walk is now
# streamed in bounded segments (O(batch) RAM), coalesced behind a single-flight
# coordinator (one scan per process, never a duplicate), and capped by a hard
# per-probe budget. When the budget is exhausted the probe reports NOT ready
# with an explicit reason and resumable progress - it never reports ready to
# hide latency, and it never weakens tamper detection.
READYZ_VERIFY_BUDGET_MS = float(os.getenv('READYZ_VERIFY_BUDGET_MS', '25'))
# A coalesced waiter never burns the whole probe budget: it parks briefly and
# then answers not-ready, keeping probe latency flat under a cold-start storm.
READYZ_VERIFY_COALESCE_WAIT_MS = float(os.getenv('READYZ_VERIFY_COALESCE_WAIT_MS', '15'))
_READYZ_VERIFY_BATCH_ENV = int(os.getenv('READYZ_VERIFY_BATCH', '1000'))
READYZ_VERIFY_BATCH = _READYZ_VERIFY_BATCH_ENV if _READYZ_VERIFY_BATCH_ENV > 0 else 1000
_AUDIT_PROBE_LOCK = threading.Lock()
_AUDIT_PROBE_CACHE = {'head': None, 'events': -1, 'verified_at': 0.0}
_AUDIT_VERIFY_SINGLEFLIGHT = threading.Lock()
_AUDIT_VERIFY_DONE = threading.Event()
_AUDIT_VERIFY_PROGRESS = {'head_target': None, 'last_id': 0, 'previous': '', 'count': 0, 'scanned': 0}


class AuditVerificationPending(Exception):
    """Bounded verification budget elapsed; readiness is unproven, not failed."""

    def __init__(self, progress):
        super().__init__('audit verification in progress')
        self.progress = progress


def audit_verification_progress():
    with _AUDIT_PROBE_LOCK:
        return {
            'verified_events': _AUDIT_VERIFY_PROGRESS['count'],
            'cursor_id': _AUDIT_VERIFY_PROGRESS['last_id'],
            'events_scanned': _AUDIT_VERIFY_PROGRESS['scanned'],
        }


def reset_readiness_verifier():
    """Test/ops hook: drop cached verification state and resumable cursor."""
    with _AUDIT_PROBE_LOCK:
        _AUDIT_PROBE_CACHE.update(head=None, events=-1, verified_at=0.0)
        _AUDIT_VERIFY_PROGRESS.update(head_target=None, last_id=0, previous='', count=0, scanned=0)
    _AUDIT_VERIFY_DONE.clear()


def readiness_verify_audit(c, budget_ms=None):
    # Fast path: peek the head row only (indexed, O(1)). A COUNT(*) on every
    # probe is itself an O(n) scan, which is what made steady-state probes cost
    # ~7 ms on a 100k ledger. Cardinality is still proven by the signed head
    # checkpoint and the periodic full streaming re-verify.
    peek_state = verify_audit_head(c, AUDIT_KEY, strict_count=False)
    peek_head = peek_state['head']
    now_mono = time.monotonic()
    with _AUDIT_PROBE_LOCK:
        cached = dict(_AUDIT_PROBE_CACHE)
    if (
        cached['head'] == peek_head
        and cached['events'] >= 0
        and (now_mono - cached['verified_at']) < READYZ_FULL_VERIFY_INTERVAL
    ):
        return {'events': cached['events'], 'head': cached['head']}
    # Everyone that reaches this point (scanner *and* coalesced waiters) joins
    # on the O(1) signed head checkpoint. Running the strict O(n) COUNT(*) here
    # made every waiter in a cold-start probe storm pay a full table scan on the
    # same handle, starving the single scanner (observed: 47 scans started, 0
    # completed, cold p95 781 ms on a 100k ledger). The strict cardinality proof
    # now runs exactly once, inside the scanner, per head change.
    head_state = peek_state
    budget = READYZ_VERIFY_BUDGET_MS if budget_ms is None else float(budget_ms)
    deadline = now_mono + budget / 1000.0
    if not _AUDIT_VERIFY_SINGLEFLIGHT.acquire(blocking=False):
        # Coalesce: wait on the in-flight scan's result. No DB handle work and
        # no transaction/advisory lock is held while waiting, so a waiter can
        # never invert locks with the scanner.
        _metric_inc('readyz_verify_coalesced')
        remaining = deadline - time.monotonic()
        coalesce_wait = READYZ_VERIFY_COALESCE_WAIT_MS / 1000.0
        if remaining > coalesce_wait:
            remaining = coalesce_wait
        if _AUDIT_VERIFY_DONE.wait(remaining if remaining > 0 else 0.0):
            with _AUDIT_PROBE_LOCK:
                shared = (
                    _AUDIT_PROBE_CACHE['head'] == head_state['head']
                    and _AUDIT_PROBE_CACHE['events'] == head_state['events']
                )
            if shared:
                return head_state
        _metric_inc('readyz_verify_budget_exhausted')
        raise AuditVerificationPending(audit_verification_progress())
    started = time.monotonic()
    try:
        _metric_inc('readyz_verify_inflight')
        _AUDIT_VERIFY_DONE.clear()
        # Strict cardinality proof (O(n) COUNT(*)) runs only here, under the
        # single-flight lock, so it is paid once per head change, never per probe.
        head_state = verify_audit_head(c, AUDIT_KEY)
        with _AUDIT_PROBE_LOCK:
            if _AUDIT_VERIFY_PROGRESS['head_target'] != head_state['head']:
                _AUDIT_VERIFY_PROGRESS.update(head_target=head_state['head'], last_id=0, previous='', count=0, scanned=0)
            cursor = dict(_AUDIT_VERIFY_PROGRESS)
        while True:
            segment = verify_audit_segment(
                c, AUDIT_KEY, cursor['last_id'], cursor['previous'], READYZ_VERIFY_BATCH, cursor['count'],
            )
            cursor.update(
                last_id=segment['last_id'],
                previous=segment['previous'],
                count=segment['count'],
                scanned=cursor['scanned'] + segment['scanned'],
            )
            _metric_inc('readyz_verify_events_scanned', segment['scanned'])
            with _AUDIT_PROBE_LOCK:
                _AUDIT_VERIFY_PROGRESS.update(cursor)
            if segment['complete']:
                verify_audit_orphans(c, cursor['count'])
                result = {'events': cursor['count'], 'head': cursor['previous']}
                elapsed_ms = (time.monotonic() - started) * 1000.0
                _metric_inc('readyz_verify_completed')
                _metric_inc('readyz_verify_ms', int(elapsed_ms))
                with _AUDIT_PROBE_LOCK:
                    _AUDIT_PROBE_CACHE.update(head=result['head'], events=result['events'], verified_at=time.monotonic())
                    _AUDIT_VERIFY_PROGRESS.update(head_target=None, last_id=0, previous='', count=0, scanned=0)
                _AUDIT_VERIFY_DONE.set()
                return result
            time.sleep(0)  # yield between segments so probes are not starved
            if time.monotonic() >= deadline:
                _metric_inc('readyz_verify_budget_exhausted')
                raise AuditVerificationPending(audit_verification_progress())
    finally:
        _AUDIT_VERIFY_SINGLEFLIGHT.release()


def audit(c, actor, action, typ, oid, detail=''):
    append_audit(c, uid('evt'), actor, action, typ, oid, detail, now(), AUDIT_KEY)


def auth(headers):
    if ENV == 'production':
        if GATEWAY_ASSERTION_VERSION != '1':
            return (None, None)
        result = verify_gateway_assertion(
            headers,
            {GATEWAY_ASSERTION_KEY_ID: OIDC_SECRET},
            GATEWAY_ASSERTION_AUDIENCE,
            GATEWAY_ASSERTION_ISSUERS,
        )
        return result or (None, None)
    raw = headers.get('Authorization', '')
    token = raw[7:] if raw.startswith('Bearer ') else ''
    if not token:
        return (None, None)
    digest = token_hash(token, TOKEN_PEPPER)
    current = now()
    with db() as c:
        row = c.execute(
            "SELECT role,principal FROM auth_tokens WHERE token_hash=? AND revoked_at='' AND expires_at>?",
            (digest, current),
        ).fetchone()
    return (row['role'], row['principal']) if row else (None, None)


def clean(value, limit, required=False):
    if not isinstance(value, str):
        raise ValueError('expected string')
    value = value.strip()
    if required and not value:
        raise ValueError('required field is empty')
    if len(value) > limit:
        raise ValueError(f'field exceeds {limit} characters')
    return value


def valid_id(value, prefix=None):
    return bool(ID_RE.fullmatch(value)) and (not prefix or value.startswith(prefix + '_'))


def rows(cur):
    return [dict(r) for r in cur.fetchall()]


def source(c, sid, project_id=None):
    if project_id is not None:
        return c.execute('SELECT * FROM sources WHERE id=? AND project_id=?',
                         (sid, project_id)).fetchone()
    return c.execute('SELECT * FROM sources WHERE id=?', (sid,)).fetchone()


def source_publishable(c, sid):
    docs = rows(c.execute('SELECT storage_state FROM documents WHERE source_id=?', (sid,)))
    return not docs or all(d['storage_state'] == 'clean' for d in docs)


def project_public_view(row):
    return {
        'id': row['id'],
        'title': row['title'],
        'authority': row['authority'],
        'location': row['location'],
        'summary': row['summary'],
        'status': row['status'],
        'synthetic': row['synthetic'],
        'updated_at': row['updated_at'],
    }


def claim_public_view(row):
    return {
        'id': row['id'],
        'claim_type': row['claim_type'],
        'publication_state': row['publication_state'],
        'text': row['text'],
        'passage': row['passage'],
        'page_ref': row['page_ref'],
        'source_url': row['source_url'],
        'publisher': row['publisher'],
        'retrieved_at': row['retrieved_at'],
        'source_sha256': row['source_sha256'],
    }


def gap_public_view(row):
    return {
        'id': row['id'],
        'document_name': row['document_name'],
        'search_scope': row['search_scope'],
        'searched_at': row['searched_at'],
        'status': row['status'],
    }


def response_public_view(row):
    return {
        'id': row['id'],
        'responder': row['responder'],
        'text': row['text'],
        'source_url': row['source_url'],
        'created_at': row['created_at'],
    }


CSV_FORMULA_PREFIXES = ('=', '+', '-', '@', '\t', '\r')


def csv_cell(value):
    """Neutralise spreadsheet formula injection (CWE-1236) and normalise cells."""
    if value is None:
        return ''
    text = str(value)
    if text.startswith(CSV_FORMULA_PREFIXES):
        text = "'" + text
    return text


def dossier_csv(dossier):
    import csv
    import io
    project = dossier['project']
    label = 'SYNTHETIC' if project['synthetic'] else 'REVIEWED'
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator='\r\n')
    w.writerow(['record_kind', 'data_label', 'project_id', 'project_title', 'authority', 'claim_type', 'publication_state',
                'text', 'source_url', 'retrieved_at', 'source_sha256', 'anchor', 'generated_at'])
    generated = now()
    for claim in dossier['claims']:
        w.writerow([csv_cell(x) for x in ('claim', label, project['id'], project['title'], project['authority'], claim['claim_type'],
                    claim['publication_state'], claim['text'], claim['source_url'], claim['retrieved_at'], claim['source_sha256'],
                    claim['page_ref'] or claim['passage'], generated)])
    for gap in dossier['gaps']:
        w.writerow([csv_cell(x) for x in ('record_not_located', label, project['id'], project['title'], project['authority'], '', '',
                    gap['document_name'], '', gap['searched_at'], '', gap['search_scope'], generated)])
    return buf.getvalue()


def bundle(pid, private=False):
    with db() as c:
        project = c.execute('SELECT * FROM projects WHERE id=?', (pid,)).fetchone()
        if not project or (not private and project['status'] != 'published'):
            return None
        query = (
            'SELECT c.*,s.url source_url,s.publisher,s.retrieved_at,s.sha256 source_sha256 '
            'FROM claims c JOIN sources s ON s.id=c.source_id WHERE c.project_id=?'
        )
        args = [pid]
        if not private:
            query += ' AND c.publication_state IN (?,?,?,?)'
            args += sorted(PUBLIC_STATES)
        claims = rows(c.execute(query + ' ORDER BY c.created_at', args))
        gaps = rows(c.execute('SELECT * FROM gaps WHERE project_id=? ORDER BY created_at', (pid,)))
        responses = rows(
            c.execute(
                'SELECT r.*,s.url source_url FROM responses r '
                'LEFT JOIN sources s ON s.id=r.source_id WHERE r.project_id=? ORDER BY r.created_at',
                (pid,),
            )
        )
        if private:
            return {
                'project': dict(project),
                'claims': claims,
                'gaps': gaps,
                'responses': responses,
                'sources': rows(c.execute('SELECT * FROM sources WHERE project_id=?', (pid,))),
                'documents': rows(c.execute('SELECT * FROM documents WHERE project_id=?', (pid,))),
            }
        return {
            'project': project_public_view(project),
            'claims': [claim_public_view(row) for row in claims],
            'gaps': [gap_public_view(row) for row in gaps],
            'responses': [response_public_view(row) for row in responses],
        }


def stable_json_bytes(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()


def source_class_for_envelope(value):
    mapping = {
        'official': 'primary_official_record',
        'official_statement': 'official_statement',
        'independent': 'independent_technical',
        'reporting': 'reputable_reporting',
        'submission': 'public_submission',
    }
    return mapping.get((value or '').strip(), 'primary_official_record')


def anchor_from_claim(claim):
    page_ref = claim.get('page_ref') or ''
    match = re.search(r'(\d+)', page_ref)
    page = int(match.group(1)) if match else 1
    text = claim.get('passage') or page_ref or claim.get('text', '')[:400]
    return {'page': page, 'text': text}


def evidence_envelope_from_claim(claim):
    return {
        'id': 'ev_' + claim['id'],
        'artifact_sha256': claim['source_sha256'],
        'retrieved_at': claim['retrieved_at'],
        'source': {
            'url': claim['source_url'],
            'publisher': claim['publisher'] or claim['source_url'],
            'source_class': source_class_for_envelope(claim.get('source_class')),
        },
        'derivation': {
            'kind': 'snapshot',
            'tool': 'project-xray',
            'version': '0.4.8',
            'parent_sha256': None,
        },
        'anchors': [anchor_from_claim(claim)],
        'warnings': ['Public capsule: review conclusions remain subject to human verification.'],
    }


def dossier_capsule(bundle_data):
    claim_rows = bundle_data['claims']
    envelopes = [evidence_envelope_from_claim(claim) for claim in claim_rows]
    capsule = {
        'schema_version': '1',
        'kind': 'project_xray_public_dossier_capsule',
        'generated_at': now(),
        'project': bundle_data['project'],
        'claims': claim_rows,
        'gaps': bundle_data['gaps'],
        'responses': bundle_data['responses'],
        'evidence_envelopes': envelopes,
        'methodology': {
            'two_person_review_required': True,
            'unsupported_absence_rule': 'Not located means not located in searched sources, not proof of non-existence.',
            'risk_indicator_rule': 'Risk indicators do not prove corruption and require human review.',
        },
    }
    capsule['capsule_sha256'] = hashlib.sha256(stable_json_bytes(capsule)).hexdigest()
    return capsule


def strict_json(raw):
    def pairs(values):
        out = {}
        for k, v in values:
            if k in out:
                raise ValueError('duplicate JSON key')
            out[k] = v
        return out

    def invalid_constant(value):
        raise ValueError('non-finite JSON number')

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError('non-finite JSON number')
        return number

    try:
        result = json.loads(raw, object_pairs_hook=pairs,
                            parse_constant=invalid_constant, parse_float=finite_float)
    except (RecursionError, UnicodeDecodeError):
        raise ValueError('invalid JSON encoding or nesting') from None
    if not isinstance(result, dict):
        raise ValueError('JSON object required')
    return result


TRACEPARENT_RE = re.compile(r'00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})')


class H(BaseHTTPRequestHandler):
    server_version = 'ProjectXRay/0.4'
    sys_version = ''

    def setup(self):
        super().setup()
        self.request.settimeout(15)
        self.request_id = ''
        self.tx = None
        self.idem = None
        self._xray_headers_sent = False
        self._pending_response = None
        self._trace = None

    def safe_request_id(self):
        value = self.headers.get('X-Request-ID', '')
        return value if re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value) else uid('req')

    def trace_context(self):
        """Return (trace_id, span_id, traceparent) following W3C Trace Context.

        An inbound ``traceparent`` is honoured only when it is syntactically
        valid (version 00, 32-hex trace-id, 16-hex parent-id, 2-hex flags, not
        all-zero); anything else is dropped at the boundary and a fresh trace is
        minted. A new server span id is always generated. OpenTelemetry
        collectors correlate the JSON log lines on ``trace_id``/``span_id``.
        """
        span = getattr(self, '_otel_span', None)
        if span is not None:
            ctx = span.get_span_context()
            trace_id, span_id = f'{ctx.trace_id:032x}', f'{ctx.span_id:016x}'
            return trace_id, span_id, f'00-{trace_id}-{span_id}-{int(ctx.trace_flags):02x}'
        cached = getattr(self, '_trace', None)
        if cached:
            return cached
        header = self.headers.get('traceparent', '') if getattr(self, 'headers', None) else ''
        m = TRACEPARENT_RE.fullmatch(header.strip()) if header else None
        if m and set(m.group(1)) != {'0'} and set(m.group(2)) != {'0'}:
            trace_id, flags = m.group(1), m.group(3)
        else:
            trace_id, flags = secrets.token_hex(16), '01'
        span_id = secrets.token_hex(8)
        self._trace = (trace_id, span_id, f'00-{trace_id}-{span_id}-{flags}')
        return self._trace

    def log_message(self, fmt, *args):
        trace_id, span_id, _ = self.trace_context()
        print(
            json.dumps(
                {
                    'time': now(),
                    'request_id': self.request_id,
                    'trace_id': trace_id,
                    'span_id': span_id,
                    'remote': self.client_address[0],
                    'message': 'http_request',
                    'route': telemetry.safe_route(getattr(self, 'path', '')),
                    'status': getattr(self, '_response_status', None),
                },
                separators=(',', ':'),
            )
        )

    def send_response(self, code, message=None):
        self._response_status = code
        span = getattr(self, '_otel_span', None)
        if span is not None:
            from opentelemetry.trace import StatusCode
            span.set_attribute('http.response.status_code', code)
            if code >= 500:
                span.set_status(StatusCode.ERROR)
        return super().send_response(code, message)

    def common(self, code, ctype, extra_headers=None):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
        self.send_header(
            'Content-Security-Policy',
            "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
        )
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Request-ID', self.request_id)
        self.send_header('traceparent', self.trace_context()[2])
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        if PUBLIC_BASE_URL.startswith('https://'):
            self.send_header('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
        self.end_headers()
        self._xray_headers_sent = True

    def out(self, obj, code=200, extra_headers=None):
        if code >= 400:
            _metric_inc('errors')
        body = json.dumps(obj, ensure_ascii=False, separators=(',', ':'))
        if 200 <= code < 300 and self.idem:
            c = self.tx
            if c:
                # Fenced completion: only the worker whose reservation
                # (created_at acts as the lease token) is still live may
                # record the outcome.  If the lease was reclaimed and
                # re-issued to a retry, abort *before* commit so the retry
                # remains the single writer (exactly-once, no duplicate row).
                updated = c.execute(
                    "UPDATE idempotency_keys SET state='completed',response_code=?,response_body=?,completed_at=? WHERE principal=? AND key=? AND state='processing' AND created_at=?",
                    (code, body, now(), self.idem[0], self.idem[1], self.idem[2]),
                ).rowcount
                if updated != 1:
                    _metric_inc('idempotency_lease_lost')
                    raise IdempotencyLeaseLost(self.idem[1])
        if self.tx is not None:
            # Never acknowledge a write until the surrounding transaction commits.
            self._pending_response = (body, code, extra_headers)
            return
        self.common(code, 'application/json; charset=utf-8', extra_headers=extra_headers)
        self.wfile.write(body.encode())

    def capability_guard(self, method, path):
        policy = CapabilityPolicy.from_mapping(os.environ)
        reason = denial_reason(policy, method, path, self.headers)
        if reason is None:
            return policy
        print(
            json.dumps(
                {
                    'time': now(),
                    'level': 'warning',
                    'event': 'capability_denied',
                    'request_id': self.request_id,
                    'method': method,
                    'path': telemetry.safe_route(path),
                    'reason': reason,
                },
                separators=(',', ':'),
            )
        )
        self.out(
            {
                'error': 'capability temporarily unavailable',
                'code': 'capability_temporarily_unavailable',
                'request_id': self.request_id,
            },
            503,
            extra_headers={'Retry-After': '60'},
        )
        return None

    def text(self, value, code=200, ctype='text/plain; charset=utf-8', extra_headers=None):
        self.common(code, ctype, extra_headers)
        self.wfile.write(value.encode())

    def body(self):
        # Reject ambiguous framing rather than disagreeing with an upstream proxy.
        if self.headers.get('Transfer-Encoding') is not None:
            raise ValueError('Transfer-Encoding is not supported')
        lengths = self.headers.get_all('Content-Length', [])
        if len(lengths) != 1 or not re.fullmatch(r'[0-9]+', lengths[0]):
            raise ValueError('one valid Content-Length required')
        try:
            n = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            raise ValueError('invalid Content-Length')
        if n <= 0:
            raise ValueError('JSON body required')
        if n > MAX:
            raise OverflowError('request too large')
        ctype = self.headers.get('Content-Type', '').split(';')[0].strip()
        if ctype != 'application/json':
            raise TypeError('Content-Type must be application/json')
        try:
            raw = self.rfile.read(n)
            if len(raw) != n:
                raise ValueError('incomplete request body')
            return strict_json(raw)
        except json.JSONDecodeError:
            raise ValueError('invalid JSON')

    def principal(self, roles):
        role, actor = auth(self.headers)
        if role not in roles:
            _metric_inc('auth_failures')
            self.out({'error': 'unauthorized'}, 401)
            return None
        return role, actor

    def client_identity(self):
        remote = self.client_address[0]
        if not TRUST_PROXY_HEADERS:
            return remote
        forwarded = self.headers.get('X-Forwarded-For', '').split(',')[0].strip()
        candidate = forwarded or self.headers.get('X-Real-IP', '').strip() or remote
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            return remote

    def _headers_sent(self):
        return bool(getattr(self, '_headers_buffer', None) is not None and getattr(self, 'wfile', None) and getattr(self, '_xray_headers_sent', False))

    def _fail_safe(self, exc, method='GET'):
        """Return a generic 500 without leaking exception details to clients."""
        self._pending_response = None
        # Any exception that reaches here means the write transaction did not
        # commit (the db() context rolled back and closed it).  Clear the
        # handle so the error reply is actually written to the client instead
        # of being buffered for a commit that will never happen.
        self.tx = None
        if isinstance(exc, IdempotencyLeaseLost):
            return self.out({'error': 'idempotency reservation was reclaimed by a concurrent retry; replay with the same key', 'request_id': self.request_id or uid('req')}, 409,
                            extra_headers={'Retry-After': '1'})
        if isinstance(exc, DatabaseBusy):
            return self.out({'error': 'database capacity temporarily unavailable'}, 503,
                            extra_headers={'Retry-After': '1'})
        import traceback
        path = ''
        try:
            path = urlparse(self.path).path
        except Exception:
            path = getattr(self, 'path', '')
        rid = getattr(self, 'request_id', None) or uid('req')
        self.request_id = rid
        # Structured server log — never include auth headers or secrets
        print(
            json.dumps(
                {
                    'time': now(),
                    'level': 'error',
                    'request_id': rid,
                    'method': method,
                    'path': telemetry.safe_route(path),
                    'exception_class': type(exc).__name__,
                    'stack': [{'file': Path(frame.filename).name, 'line': frame.lineno, 'function': frame.name}
                              for frame in traceback.extract_tb(exc.__traceback__)],
                },
                separators=(',', ':'),
            )
        )
        _metric_inc('errors')
        if getattr(self, '_xray_headers_sent', False):
            # Partially written response — do not attempt a second write
            return
        try:
            self._xray_headers_sent = True
            body = json.dumps(
                {'error': 'internal server error', 'request_id': rid},
                separators=(',', ':'),
            )
            self.send_response(500)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
            self.send_header(
                'Content-Security-Policy',
                "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
            )
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Request-ID', rid)
            if PUBLIC_BASE_URL.startswith('https://'):
                self.send_header('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
            self.end_headers()
            self.wfile.write(body.encode())
        except Exception:
            pass

    def do_OPTIONS(self):
        # Explicitly reject cross-origin preflight — same-origin only API
        self.request_id = self.safe_request_id()
        self.send_response(405)
        self.send_header('Allow', 'GET, POST')
        self.send_header('X-Request-ID', self.request_id)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()

    def rate_bucket(self, method, path):

        if path in PROBE_PATHS or path in {'/', '/index.html', '/app.js', '/styles.css'}:
            return None
        if method == 'GET':
            if self.headers.get('Authorization'):
                return 'auth_read', AUTH_READ_RATE_LIMIT
            return 'public_read', PUBLIC_READ_RATE_LIMIT
        if method == 'POST':
            expensive = (
                path.startswith('/api/auth/tokens')
                or path.endswith('/publish')
                or path.endswith('/scan')
                or path == '/api/operations/test-alert'
            )
            return ('expensive_write', EXPENSIVE_WRITE_RATE_LIMIT) if expensive else ('write', WRITE_RATE_LIMIT)
        return None

    def limited(self, method, path):
        bucket = self.rate_bucket(method, path)
        if not bucket:
            return False
        category, limit = bucket
        minute = int(time.time() // 60)
        key = (category, self.client_identity(), minute)
        with _RATE_LOCK:
            slot = RATE.get(key, 0)
            RATE[key] = slot + 1
            # Bounded cleanup of stale rate-limit entries (no I/O under lock)
            stale = [k for k in RATE if k[2] < minute - 1]
            for old in stale:
                RATE.pop(old, None)
            limited_now = slot >= limit
        if limited_now:
            _metric_inc('rate_limited')
            record_abuse(category, self.client_identity())
            return True
        return False

    def reserve_idempotency(self, actor, data):
        key = self.headers.get('Idempotency-Key', '').strip()
        if ENV == 'production' and not key:
            self.out({'error': 'Idempotency-Key required'}, 400)
            return False
        if not key:
            return True
        if len(key) > 128:
            self.out({'error': 'invalid Idempotency-Key'}, 400)
            return False
        request_hash = hashlib.sha256(
            (self.command + '|' + urlparse(self.path).path + '|' +
             json.dumps(data, sort_keys=True, separators=(',', ':'))).encode()
        ).hexdigest()

        def answer_existing(old):
            _metric_inc('idempotency_conflicts')
            if old['request_hash'] != request_hash:
                self.out({'error': 'idempotency key reused with different request'}, 409)
                return False
            if old['state'] == 'completed':
                _metric_inc('idempotency_replays')
                self.common(old['response_code'], 'application/json; charset=utf-8')
                self.wfile.write(old['response_body'].encode())
                return False
            self.out({'error': 'request with this idempotency key is processing'}, 409,
                     extra_headers={'Retry-After': '1'})
            return False

        reserved_at = now()
        try:
            with db(True) as c:
                old = c.execute(
                    'SELECT * FROM idempotency_keys WHERE principal=? AND key=?',
                    (actor, key),
                ).fetchone()
                if old:
                    age = idempotency_age_seconds(old['created_at'])
                    stuck = (old['state'] == 'processing' and age is not None and
                             age > IDEMPOTENCY_STUCK_SECONDS)
                    if stuck:
                        # Fence reclaim to the lease we observed. Without this
                        # predicate a losing reclaimer can delete a fresh lease.
                        deleted = c.execute(
                            "DELETE FROM idempotency_keys WHERE principal=? AND key=? "
                            "AND state='processing' AND created_at=?",
                            (actor, key, old['created_at']),
                        )
                        if deleted.rowcount == 1:
                            _metric_inc('idempotency_stuck_reclaims')
                            audit(c, actor, 'reclaim', 'idempotency_key', key,
                                  f'age_seconds={int(age)}')
                        else:
                            # Serialisation was relaxed and another worker won.
                            # Let INSERT contention below resolve truthfully.
                            pass
                    else:
                        return answer_existing(old)
                c.execute(
                    'INSERT INTO idempotency_keys(principal,key,request_hash,state,created_at) '
                    'VALUES(?,?,?,?,?)',
                    (actor, key, request_hash, 'processing', reserved_at),
                )
        except IntegrityError:
            # Read-then-insert can race if backend serialisation is ever relaxed.
            # The failed transaction is gone; inspect the winner afresh rather
            # than surfacing an opaque 500.
            with db() as c:
                winner = c.execute(
                    'SELECT * FROM idempotency_keys WHERE principal=? AND key=?',
                    (actor, key),
                ).fetchone()
            if winner:
                return answer_existing(winner)
            raise
        self.idem = (actor, key, reserved_at)
        return True

    def release_idempotency(self):
        """Free a reservation when nothing was acknowledged to the client."""
        actor, key, reserved_at = self.idem
        try:
            with db(True) as c:
                # Fenced release: never delete a reservation re-issued to a
                # later retry after this one was reclaimed as stuck.
                c.execute(
                    "DELETE FROM idempotency_keys WHERE principal=? AND key=? AND state='processing' AND created_at=?",
                    (actor, key, reserved_at),
                )
        except Exception:
            pass

    def do_GET(self):
        self.request_id = self.safe_request_id()
        try:
            self._handle_get()
        except Exception as exc:
            self._fail_safe(exc, method='GET')

    def _handle_get(self):
        _metric_inc('requests')
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        capability_policy = self.capability_guard('GET', path)
        if capability_policy is None:
            return

        if self.limited('GET', path):
            return self.out({'error': 'rate limit exceeded'}, 429,
                            extra_headers={'Retry-After': str(max(1, 60 - int(time.time()) % 60))})

        if path in LIVENESS_PATHS:
            return self.out({'status': 'ok', 'time': now(), 'version': '0.4.8'})
        if path in READINESS_PATHS:
            if not capability_policy.valid or capability_policy.maintenance:
                return self.out(
                    {
                        'status': 'not_ready',
                        'ready': False,
                        'capabilities': capability_policy.as_public_dict(),
                    },
                    503,
                )
            try:
                with db() as c:
                    c.execute('SELECT 1')
                    readiness_verify_audit(c)
                return self.out(
                    {
                        'status': 'degraded' if capability_policy.degraded else 'ready',
                        'ready': True,
                        'time': now(),
                        'capabilities': capability_policy.as_public_dict(),
                    }
                )
            except AuditVerificationPending as pending:
                # Honest degraded answer: integrity is unproven within the
                # probe budget, so the instance stays out of the load balancer
                # while the coalesced background scan resumes from its cursor.
                return self.out(
                    {
                        'status': 'not_ready',
                        'ready': False,
                        'reason': 'audit_verification_in_progress',
                        'audit_verification': pending.progress,
                        'capabilities': capability_policy.as_public_dict(),
                    },
                    503,
                )
            except Exception:
                return self.out(
                    {
                        'status': 'not_ready',
                        'ready': False,
                        'capabilities': capability_policy.as_public_dict(),
                    },
                    503,
                )
        if path == '/metrics':
            if not self.principal(('admin',)):
                return
            lines = '\n'.join(f'project_xray_{k}_total {v}' for k, v in metrics_snapshot().items()) + '\n'
            return self.text(lines, ctype='text/plain; version=0.0.4')
        if path == '/api/admin/abuse':
            if not self.principal(('admin',)):
                return
            return self.out(abuse_snapshot())
        if path == '/api/auth/tokens':
            if not self.principal(('admin',)):
                return
            with db() as c:
                return self.out(
                    {
                        'tokens': rows(
                            c.execute(
                                'SELECT id,principal,role,expires_at,revoked_at,created_at,rotated_from FROM auth_tokens ORDER BY created_at'
                            )
                        )
                    }
                )
        if path == '/api/projects':
            private = query.get('include_private') == ['1'] and auth(self.headers)[0] in ('admin', 'reviewer')
            with db() as c:
                if private:
                    projects = rows(
                        c.execute(
                            'SELECT id,title,authority,location,summary,status,synthetic,created_at,updated_at FROM projects ORDER BY updated_at DESC'
                        )
                    )
                else:
                    projects = [
                        project_public_view(row)
                        for row in c.execute(
                            "SELECT * FROM projects WHERE status='published' ORDER BY updated_at DESC"
                        )
                    ]
            return self.out({'projects': projects})

        segments = [x for x in path.split('/') if x]
        if len(segments) >= 3 and segments[:2] == ['api', 'projects'] and valid_id(segments[2], 'prj'):
            private = query.get('include_private') == ['1'] and auth(self.headers)[0] in ('admin', 'reviewer')
            dossier = bundle(segments[2], private)
            if not dossier:
                return self.out({'error': 'not found'}, 404)
            if len(segments) == 3:
                return self.out(dossier)
            if len(segments) == 4 and segments[3] == 'claims.csv':
                return self.text(
                    dossier_csv(dossier),
                    ctype='text/csv; charset=utf-8',
                    extra_headers={'Content-Disposition': f'attachment; filename="{segments[2]}-claims.csv"'},
                )
            if len(segments) == 4 and segments[3] == 'report':
                project = dossier['project']
                lines = [
                    f"# Evidence report: {project['title']}",
                    '',
                    f'Generated: {now()}',
                    f"Authority: {project['authority']}",
                    '',
                    '## Claims',
                ]
                for claim in dossier['claims']:
                    lines += [
                        f"- [{claim['claim_type']} / {claim['publication_state']}] {claim['text']}",
                        f"  Source: {claim['source_url']} (retrieved {claim['retrieved_at']}; SHA-256 {claim['source_sha256']})",
                        f"  Anchor: {claim['page_ref'] or claim['passage']}",
                    ]
                lines += ['', '## Records not located'] + [
                    f"- {gap['document_name']} — searched: {gap['search_scope']} ({gap['searched_at']})"
                    for gap in dossier['gaps']
                ]
                return self.text('\n'.join(lines), ctype='text/markdown; charset=utf-8')
            if len(segments) == 4 and segments[3] == 'rti':
                project = dossier['project']
                items = '\n'.join(
                    f"{i + 1}. Certified electronic copy of {gap['document_name']}."
                    for i, gap in enumerate(dossier['gaps'])
                ) or '1. No document gaps have been selected.'
                return self.text(
                    f"Draft RTI request — not legal advice\n"
                    f"{'SYNTHETIC EVALUATION — do not file' if project['synthetic'] else 'Human review required before filing'}\n\n"
                    f"To: Public Information Officer, {project['authority']}\n"
                    "PIO postal address: [verify independently]\n"
                    f"Subject: Request under Section 6(1), Right to Information Act, 2005 — {project['title']}\n\n"
                    f"Please provide the following records, where held by your authority:\n{items}\n\n"
                    "Applicant name and contact address: [complete privately; do not publish]\n"
                    "Date and signature: [complete]\n"
                    "Before filing: verify the competent authority, applicable central/state rules, "
                    "fee or exemption, submission method, and requested record descriptions. "
                    "A record not located in the stated search scope is not proof it does not exist.\n"
                )
            if len(segments) == 4 and segments[3] == 'capsule':
                return self.out(dossier_capsule(dossier))
            if len(segments) == 4 and segments[3] == 'audit':
                if not self.principal(('admin', 'reviewer')):
                    return
                with db() as c:
                    return self.out(
                        {
                            'events': rows(
                                c.execute(
                                    'SELECT * FROM audit_events WHERE object_id=? OR detail LIKE ? ORDER BY id',
                                    (segments[2], '%project=' + segments[2] + '%'),
                                )
                            ),
                            'verification': verify_audit(c, AUDIT_KEY),
                        }
                    )
        return self.static(path)

    def static(self, path):
        mapping = {
            '/': 'static/index.html',
            '/index.html': 'static/index.html',
            '/app.js': 'static/app.js',
            '/styles.css': 'static/styles.css',
        }
        rel = mapping.get(path)
        if not rel:
            return self.out({'error': 'not found'}, 404)
        p = ROOT / rel
        types = {
            '.html': 'text/html; charset=utf-8',
            '.js': 'application/javascript; charset=utf-8',
            '.css': 'text/css; charset=utf-8',
        }
        content = p.read_bytes()
        self.common(200, types[p.suffix])
        self.wfile.write(content)

    def do_POST(self):
        self.request_id = self.safe_request_id()
        self._pending_response = None
        try:
            self._handle_post()
            if self._pending_response is not None:
                body, code, extra_headers = self._pending_response
                self.common(code, 'application/json; charset=utf-8', extra_headers=extra_headers)
                self.wfile.write(body.encode())
        except Exception as exc:
            self._fail_safe(exc, method='POST')
        finally:
            self._pending_response = None

    def _handle_post(self):
        _metric_inc('requests')
        path = urlparse(self.path).path
        segments = [x for x in path.split('/') if x]

        if self.capability_guard('POST', path) is None:
            return
        _metric_inc('writes')

        if self.limited('POST', path):
            return self.out({'error': 'rate limit exceeded'}, 429,
                            extra_headers={'Retry-After': str(max(1, 60 - int(time.time()) % 60))})

        principal = self.principal(('admin', 'reviewer', 'scanner'))
        if not principal:
            return
        role, actor = principal

        try:
            data = self.body()
        except OverflowError:
            return self.out({'error': 'request too large'}, 413)
        except TimeoutError:
            return self.out({'error': 'request body timeout'}, 408)
        except (ValueError, TypeError) as e:
            # Validation errors may expose field names only; never stack traces
            msg = str(e)
            if any(s in msg.lower() for s in ('password', 'secret', 'token', 'authorization', 'bearer')):
                msg = 'invalid request'
            return self.out({'error': msg}, 400)

        if not self.reserve_idempotency(actor, data):
            return

        try:
            if path == '/api/operations/test-alert':
                if role != 'admin':
                    return self.out({'error': 'admin required'}, 403)
                receipt = send_alert(
                    {
                        'severity': 'test',
                        'summary': 'Project X-Ray monitoring path test',
                        'request_id': self.request_id,
                    }
                )
                with db(True) as c:
                    self.tx = c
                    audit(c, actor, 'test', 'monitoring_alert', receipt['event_id'])
                    return self.out({'delivered': True, 'event_id': receipt['event_id']})

            with db(True) as c:
                self.tx = c
                if path == '/api/auth/tokens':
                    if role != 'admin':
                        return self.out({'error': 'admin required'}, 403)
                    principal_name = clean(data.get('principal', ''), 120, True)
                    new_role = data.get('role')
                    ttl = int(data.get('ttl_seconds', 3600))
                    if new_role not in ('admin', 'reviewer', 'scanner') or not 60 <= ttl <= 2592000:
                        return self.out({'error': 'invalid role or ttl'}, 400)
                    secret = secrets.token_urlsafe(32)
                    token_id = uid('tok')
                    expires = (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat()
                    rotated = data.get('rotated_from') or None
                    if rotated and not c.execute(
                        "SELECT 1 FROM auth_tokens WHERE id=? AND revoked_at=''", (rotated,)
                    ).fetchone():
                        return self.out({'error': 'rotation source not active'}, 409)
                    c.execute(
                        'INSERT INTO auth_tokens(id,principal,role,token_hash,expires_at,created_at,rotated_from) VALUES(?,?,?,?,?,?,?)',
                        (token_id, principal_name, new_role, token_hash(secret, TOKEN_PEPPER), expires, now(), rotated),
                    )
                    if rotated:
                        c.execute('UPDATE auth_tokens SET revoked_at=? WHERE id=?', (now(), rotated))
                    audit(c, actor, 'rotate' if rotated else 'create', 'auth_token', token_id)
                    return self.out({'id': token_id, 'token': secret, 'expires_at': expires}, 201)

                if len(segments) == 5 and segments[:3] == ['api', 'auth', 'tokens'] and segments[4] == 'revoke':
                    if role != 'admin':
                        return self.out({'error': 'admin required'}, 403)
                    changed = c.execute(
                        "UPDATE auth_tokens SET revoked_at=? WHERE id=? AND revoked_at=''",
                        (now(), segments[3]),
                    ).rowcount
                    if not changed:
                        return self.out({'error': 'active token not found'}, 404)
                    audit(c, actor, 'revoke', 'auth_token', segments[3])
                    return self.out({'id': segments[3], 'revoked': True})

                if path == '/api/projects':
                    if role != 'admin':
                        return self.out({'error': 'admin required'}, 403)
                    if data.get('status', 'research') not in ('research', 'review'):
                        return self.out({'error': 'new projects must be unpublished; use publication workflow'}, 400)
                    project_id = uid('prj')
                    timestamp = now()
                    c.execute(
                        'INSERT INTO projects VALUES(?,?,?,?,?,?,?,?,?)',
                        (
                            project_id,
                            clean(data.get('title', ''), 200, True),
                            clean(data.get('authority', ''), 200),
                            clean(data.get('location', ''), 200),
                            clean(data.get('summary', ''), 4000),
                            data.get('status', 'research'),
                            int(bool(data.get('synthetic', False))),
                            timestamp,
                            timestamp,
                        ),
                    )
                    audit(c, actor, 'create', 'project', project_id)
                    return self.out({'id': project_id}, 201)

                if len(segments) < 4 or segments[:2] != ['api', 'projects'] or not valid_id(segments[2], 'prj'):
                    return self.out({'error': 'not found'}, 404)

                project_id = segments[2]
                kind = segments[3]
                if not c.execute('SELECT 1 FROM projects WHERE id=?', (project_id,)).fetchone():
                    return self.out({'error': 'project not found'}, 404)

                if kind == 'sources' and len(segments) == 4:
                    if role != 'admin':
                        return self.out({'error': 'admin required'}, 403)
                    url = clean(data.get('url', ''), 2000, True)
                    sha256 = clean(data.get('sha256', ''), 64, True).lower()
                    if not url.startswith(('https://', 'http://')) or not re.fullmatch(r'[a-f0-9]{64}', sha256):
                        return self.out({'error': 'valid source URL and SHA-256 required'}, 400)
                    source_id = uid('src')
                    c.execute(
                        'INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?,?)',
                        (
                            source_id,
                            project_id,
                            clean(data.get('publisher', ''), 200, True),
                            url,
                            clean(data.get('source_class', 'official'), 50, True),
                            clean(data.get('retrieved_at', now()), 64, True),
                            sha256,
                            clean(data.get('passage', ''), 4000),
                            clean(data.get('page_ref', ''), 100),
                            now(),
                        ),
                    )
                    audit(c, actor, 'create', 'source', source_id, 'project=' + project_id)
                    return self.out({'id': source_id}, 201)

                if kind == 'documents' and len(segments) == 4:
                    if role != 'admin':
                        return self.out({'error': 'admin required'}, 403)
                    sha256 = clean(data.get('sha256', ''), 64, True).lower()
                    media = clean(data.get('media_type', ''), 100, True)
                    size = int(data.get('size_bytes', -1))
                    source_id = data.get('source_id') or None
                    storage_uri = clean(data.get('storage_uri', ''), 1000)
                    if (
                        not re.fullmatch(r'[a-f0-9]{64}', sha256)
                        or media not in {'application/pdf', 'text/plain', 'text/csv', 'application/json', 'image/png', 'image/jpeg'}
                        or not 0 <= size <= MAX
                    ):
                        return self.out({'error': 'invalid document metadata'}, 400)
                    if source_id and not source(c, source_id, project_id):
                        return self.out({'error': 'source not found'}, 400)
                    if ENV == 'production':
                        bucket = os.getenv('STORAGE_BUCKET', '')
                        if not storage_uri.startswith('s3://' + bucket + '/'):
                            return self.out({'error': 'managed storage URI required'}, 400)
                        try:
                            verify_managed_object(
                                storage_uri,
                                sha256,
                                size,
                                require_version=True,
                            )
                        except Exception:
                            # Provider exceptions can contain access-key IDs,
                            # endpoints or request identifiers.  Keep the
                            # public failure stable and redacted.
                            return self.out({'error': 'managed object verification failed'}, 409)
                    document_id = uid('doc')
                    c.execute(
                        'INSERT INTO documents(id,project_id,source_id,filename,media_type,size_bytes,sha256,storage_state,scan_result,storage_uri,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                        (
                            document_id,
                            project_id,
                            source_id,
                            clean(data.get('filename', ''), 255, True),
                            media,
                            size,
                            sha256,
                            'quarantined',
                            'pending',
                            storage_uri,
                            now(),
                        ),
                    )
                    audit(c, actor, 'create', 'document', document_id, 'project=' + project_id)
                    return self.out({'id': document_id, 'storage_state': 'quarantined'}, 201)

                if kind == 'documents' and len(segments) == 6 and valid_id(segments[4], 'doc') and segments[5] == 'scan':
                    if role != 'scanner':
                        return self.out({'error': 'scanner role required'}, 403)
                    result = data.get('result')
                    state = 'clean' if result == 'clean' else 'rejected' if result == 'malicious' else None
                    if not state:
                        return self.out({'error': 'scan result must be clean or malicious'}, 400)
                    changed = c.execute(
                        "UPDATE documents SET storage_state=?,scan_result=?,scanned_at=?,scanned_by=? WHERE id=? AND project_id=? AND storage_state='quarantined'",
                        (state, result, now(), actor, segments[4], project_id),
                    ).rowcount
                    if not changed:
                        return self.out({'error': 'quarantined document not found'}, 409)
                    audit(c, actor, 'scan', 'document', segments[4], result)
                    return self.out({'id': segments[4], 'storage_state': state})

                if kind == 'claims' and len(segments) == 4:
                    if role != 'admin':
                        return self.out({'error': 'admin required'}, 403)
                    claim_type = data.get('claim_type')
                    source_id = data.get('source_id', '')
                    text = clean(data.get('text', ''), 8000, True)
                    passage = clean(data.get('passage', ''), 4000)
                    page_ref = clean(data.get('page_ref', ''), 100)
                    if (
                        data.get('publication_state', 'candidate') != 'candidate'
                        or claim_type not in CLAIM_TYPES
                        or not source(c, source_id, project_id)
                        or not (passage or page_ref)
                    ):
                        return self.out({'error': 'candidate with valid type, source and anchor required'}, 400)
                    claim_id = uid('clm')
                    timestamp = now()
                    c.execute(
                        'INSERT INTO claims(id,project_id,source_id,claim_type,publication_state,text,passage,page_ref,created_by,version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                        (
                            claim_id,
                            project_id,
                            source_id,
                            claim_type,
                            'candidate',
                            text,
                            passage,
                            page_ref,
                            actor,
                            1,
                            timestamp,
                            timestamp,
                        ),
                    )
                    audit(c, actor, 'create', 'claim', claim_id, 'project=' + project_id)
                    return self.out({'id': claim_id, 'version': 1, 'publication_state': 'candidate'}, 201)

                if kind == 'claims' and len(segments) == 6 and valid_id(segments[4], 'clm') and segments[5] == 'reviews':
                    if role != 'reviewer':
                        return self.out({'error': 'reviewer role required'}, 403)
                    claim = c.execute(
                        'SELECT * FROM claims WHERE id=? AND project_id=?',
                        (segments[4], project_id),
                    ).fetchone()
                    decision = data.get('decision')
                    if not claim:
                        return self.out({'error': 'claim not found'}, 404)
                    if claim['publication_state'] not in ('candidate', 'reviewed'):
                        return self.out({'error': 'public claims require an explicit correction before new review'}, 409)
                    if claim['created_by'] == actor:
                        return self.out({'error': 'creator cannot review own claim'}, 409)
                    if decision not in ('approve', 'reject'):
                        return self.out({'error': 'invalid decision'}, 400)
                    review_id = uid('rev')
                    c.execute(
                        'INSERT INTO claim_reviews VALUES(?,?,?,?,?,?,?)',
                        (
                            review_id,
                            segments[4],
                            claim['version'],
                            actor,
                            decision,
                            clean(data.get('note', ''), 1000),
                            now(),
                        ),
                    )
                    approvals = c.execute(
                        "SELECT COUNT(*) n FROM claim_reviews WHERE claim_id=? AND claim_version=? AND decision='approve'",
                        (segments[4], claim['version']),
                    ).fetchone()['n']
                    rejected = c.execute(
                        "SELECT COUNT(*) n FROM claim_reviews WHERE claim_id=? AND claim_version=? AND decision='reject'",
                        (segments[4], claim['version']),
                    ).fetchone()['n']
                    state = 'reviewed' if approvals >= 2 and not rejected else 'candidate'
                    c.execute('UPDATE claims SET publication_state=?,updated_at=? WHERE id=?', (state, now(), segments[4]))
                    audit(c, actor, 'review', 'claim', segments[4], f"version={claim['version']};{decision}")
                    return self.out({'id': review_id, 'version': claim['version'], 'approvals': approvals, 'publication_state': state}, 201)

                if kind == 'claims' and len(segments) == 6 and valid_id(segments[4], 'clm') and segments[5] == 'publish':
                    if role != 'admin':
                        return self.out({'error': 'admin required'}, 403)
                    verify_audit(c, AUDIT_KEY)
                    claim = c.execute(
                        'SELECT * FROM claims WHERE id=? AND project_id=?',
                        (segments[4], project_id),
                    ).fetchone()
                    if not claim:
                        return self.out({'error': 'claim not found'}, 404)
                    approvals = c.execute(
                        "SELECT COUNT(DISTINCT reviewer) n FROM claim_reviews WHERE claim_id=? AND claim_version=? AND decision='approve'",
                        (segments[4], claim['version']),
                    ).fetchone()['n']
                    rejected = c.execute(
                        "SELECT COUNT(*) n FROM claim_reviews WHERE claim_id=? AND claim_version=? AND decision='reject'",
                        (segments[4], claim['version']),
                    ).fetchone()['n']
                    if approvals < 2 or rejected:
                        return self.out({'error': 'two current-version approvals and no rejection required'}, 409)
                    if not source_publishable(c, claim['source_id']):
                        _metric_inc('quarantine_blocks')
                        return self.out({'error': 'source document remains quarantined or rejected'}, 409)
                    if claim['publication_state'] in PUBLIC_STATES:
                        return self.out({'id': segments[4], 'version': claim['version'], 'publication_state': claim['publication_state']})
                    state = 'corrected' if claim['version'] > 1 else 'published'
                    c.execute('UPDATE claims SET publication_state=?,updated_at=? WHERE id=?', (state, now(), segments[4]))
                    audit(c, actor, 'publish', 'claim', segments[4], f'project={project_id};version={claim["version"]}')
                    _metric_inc('publications')
                    return self.out({'id': segments[4], 'version': claim['version'], 'publication_state': state})

                if kind == 'claims' and len(segments) == 6 and valid_id(segments[4], 'clm') and segments[5] == 'correct':
                    if role != 'admin':
                        return self.out({'error': 'admin required'}, 403)
                    claim = c.execute(
                        'SELECT * FROM claims WHERE id=? AND project_id=?',
                        (segments[4], project_id),
                    ).fetchone()
                    new_text = clean(data.get('text', ''), 8000, True)
                    reason = clean(data.get('reason', ''), 1000, True)
                    if not claim or claim['publication_state'] not in PUBLIC_STATES:
                        return self.out({'error': 'only public claims can be corrected'}, 409)
                    if new_text == claim['text']:
                        return self.out({'error': 'correction must change text'}, 400)
                    version = claim['version'] + 1
                    revision_id = uid('crv')
                    c.execute(
                        'INSERT INTO claim_revisions VALUES(?,?,?,?,?,?,?,?,?)',
                        (
                            revision_id,
                            segments[4],
                            claim['version'],
                            version,
                            claim['text'],
                            new_text,
                            reason,
                            actor,
                            now(),
                        ),
                    )
                    c.execute(
                        "UPDATE claims SET text=?,version=?,publication_state='candidate',updated_at=? WHERE id=?",
                        (new_text, version, now(), segments[4]),
                    )
                    audit(c, actor, 'correct', 'claim', segments[4], f'version={version};{reason}')
                    return self.out({'id': segments[4], 'revision_id': revision_id, 'version': version, 'publication_state': 'candidate'})

                if kind == 'gaps' and len(segments) == 4:
                    if role != 'admin':
                        return self.out({'error': 'admin required'}, 403)
                    gap_id = uid('gap')
                    c.execute(
                        'INSERT INTO gaps VALUES(?,?,?,?,?,?,?)',
                        (
                            gap_id,
                            project_id,
                            clean(data.get('document_name', ''), 300, True),
                            clean(data.get('search_scope', ''), 2000, True),
                            clean(data.get('searched_at', now()), 64, True),
                            data.get('status', 'not_located'),
                            now(),
                        ),
                    )
                    audit(c, actor, 'create', 'gap', gap_id, 'project=' + project_id)
                    return self.out({'id': gap_id}, 201)

                if kind == 'responses' and len(segments) == 4:
                    if role != 'admin':
                        return self.out({'error': 'admin required'}, 403)
                    source_id = data.get('source_id') or None
                    if source_id and not source(c, source_id, project_id):
                        return self.out({'error': 'source not found'}, 400)
                    response_id = uid('rsp')
                    c.execute(
                        'INSERT INTO responses VALUES(?,?,?,?,?,?)',
                        (
                            response_id,
                            project_id,
                            clean(data.get('responder', ''), 200, True),
                            clean(data.get('text', ''), 8000, True),
                            source_id,
                            now(),
                        ),
                    )
                    audit(c, actor, 'create', 'response', response_id, 'project=' + project_id)
                    return self.out({'id': response_id}, 201)

                if kind == 'publish' and len(segments) == 4:
                    if role != 'admin':
                        return self.out({'error': 'admin required'}, 403)
                    verify_audit(c, AUDIT_KEY)
                    pending = c.execute(
                        "SELECT COUNT(*) n FROM claims WHERE project_id=? AND publication_state NOT IN ('published','disputed','corrected','withdrawn')",
                        (project_id,),
                    ).fetchone()['n']
                    published = c.execute(
                        "SELECT COUNT(*) n FROM claims WHERE project_id=? AND publication_state IN ('published','corrected')",
                        (project_id,),
                    ).fetchone()['n']
                    bad = c.execute(
                        "SELECT COUNT(*) n FROM claims c JOIN documents d ON d.source_id=c.source_id WHERE c.project_id=? AND d.storage_state!='clean'",
                        (project_id,),
                    ).fetchone()['n']
                    if pending or not published or bad:
                        if bad:
                            _metric_inc('quarantine_blocks')
                        return self.out({'error': 'project has pending claims or non-clean evidence'}, 409)
                    c.execute("UPDATE projects SET status='published',updated_at=? WHERE id=?", (now(), project_id))
                    audit(c, actor, 'publish', 'project', project_id)
                    return self.out({'id': project_id, 'status': 'published'})

                return self.out({'error': 'not found'}, 404)
        except IntegrityError:
            return self.out({'error': 'conflict'}, 409)
        except (ValueError, TypeError) as e:
            msg = str(e)
            if any(s in msg.lower() for s in ('password', 'secret', 'token', 'authorization', 'bearer')):
                msg = 'invalid request'
            return self.out({'error': msg}, 400)
        finally:
            pending = self._pending_response
            if self.idem is not None and (pending is None or pending[1] >= 400):
                # Nothing was acknowledged to the client (crash, commit
                # failure or a rejected write): free the reservation so the
                # client can retry immediately instead of waiting out the
                # stuck-reservation TTL.
                self.release_idempotency()
            self.tx = None
            self.idem = None


def _bound_execution(method):
    """Run a ``do_*`` handler inside the server's execution gate.

    The gate is taken only after the request line and headers are parsed, so
    slow or idle clients hold a connection slot (``MAX_HTTP_WORKERS``) but never
    an execution slot; the execution bound therefore cannot be starved by a
    slowloris-style client.
    """
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        from contextlib import nullcontext
        server = getattr(self, 'server', None)
        gate = getattr(server, '_exec', None)
        observer = getattr(server, 'telemetry', None)
        # Include execution admission wait, but never hold execution for slow headers.
        started = getattr(server, 'request_started', None)
        if started is not None:
            started()
        try:
            with observer.request(self) if observer else nullcontext():
                with gate if gate is not None else nullcontext():
                    if getattr(server, 'draining', False):
                        # Shed cleanly during drain: tell the client to stop
                        # reusing this connection so the pool rotates to a
                        # healthy replica instead of racing the close.
                        self.close_connection = True
                    return method(self, *args, **kwargs)
        finally:
            finished = getattr(server, 'request_finished', None)
            if finished is not None:
                finished()
    return wrapper


for _name in [n for n in vars(H) if n.startswith('do_')]:
    setattr(H, _name, _bound_execution(getattr(H, _name)))


DEFAULT_EXEC_PARALLELISM = 4


class BoundedHTTPServer(ThreadingHTTPServer):
    """Bound active handlers; backpressure admission before creating threads.

    TLS, per-client admission and slow-header protection still belong at the
    ingress proxy. This process-level limit is a final resource safety boundary.
    """
    request_queue_size = 128
    daemon_threads = True

    def __init__(self, address, handler, max_workers=None, exec_parallelism=None):
        workers = int(os.getenv('MAX_HTTP_WORKERS', '64')) if max_workers is None else max_workers
        if workers < 1:
            raise ValueError('MAX_HTTP_WORKERS must be positive')
        if exec_parallelism is None:
            exec_parallelism = int(os.getenv('HTTP_EXEC_PARALLELISM', str(DEFAULT_EXEC_PARALLELISM)))
        if exec_parallelism < 1:
            raise ValueError('HTTP_EXEC_PARALLELISM must be positive')
        # Two independent bounds. ``_slots`` caps *held* connections (memory /
        # file descriptors); ``_exec`` caps how many admitted handlers are
        # *runnable* at once. With 64 runnable CPython threads contending for
        # the GIL on SQLite-bound handlers, throughput collapsed ~8x under 100
        # clients (GIL convoy). Handlers wait here instead of thrashing.
        self.max_workers = workers
        self.exec_parallelism = min(exec_parallelism, workers)
        self._slots = threading.BoundedSemaphore(workers)
        self._exec = threading.BoundedSemaphore(self.exec_parallelism)
        self._inflight = 0
        self._inflight_lock = threading.Lock()
        self._idle = threading.Event()
        self._idle.set()
        self.draining = False
        super().__init__(address, handler)
        try:
            self.telemetry = telemetry.configured()
        except Exception:
            super().server_close()
            raise

    def server_close(self):
        super().server_close()
        observer = getattr(self, "telemetry", None)
        if observer is not None:
            observer.shutdown()

    def process_request(self, request, client_address):
        # Backpressure the accept loop instead of closing sockets with unread
        # POST bodies (which causes TCP resets rather than usable 503 replies).
        self._slots.acquire()
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def request_started(self):
        """Mark a handler as executing a request (not merely holding a socket).

        Drain must wait for *requests*, never for idle keep-alive connections:
        counting connection threads made an idle keep-alive client stall the
        drain for the whole XRAY_DRAIN_SECONDS bound (caught by regression test).
        """
        with self._inflight_lock:
            self._inflight += 1
            self._idle.clear()

    def request_finished(self):
        with self._inflight_lock:
            self._inflight -= 1
            if self._inflight <= 0:
                self._idle.set()

    def inflight(self):
        """Number of handler threads currently executing a request."""
        with self._inflight_lock:
            return self._inflight

    def drain(self, timeout=None):
        """Bounded graceful shutdown: stop accepting, let in-flight finish, close.

        Root cause this closes: ``serve_forever()`` under the default SIGTERM
        disposition died instantly (rc=-15), aborting in-flight SQLite
        transactions mid-commit and dropping queued OTLP spans. Order matters:
        (1) stop the accept loop so no new work is admitted, (2) wait up to
        ``XRAY_DRAIN_SECONDS`` for admitted handlers to complete, (3) close the
        listener and flush telemetry. Returns True if the drain completed
        before the deadline, False if it timed out (the bound is the point:
        a restart must never hang forever).
        """
        if timeout is None:
            timeout = float(os.getenv('XRAY_DRAIN_SECONDS', '15'))
        timeout = max(0.0, timeout)
        self.draining = True
        try:
            self.shutdown()  # idempotent; stops the accept loop only
        except Exception:
            pass
        deadline = time.monotonic() + timeout
        while self.inflight() > 0 and time.monotonic() < deadline:
            self._idle.wait(0.02)
        completed = self.inflight() == 0
        try:
            self.server_close()  # also flushes/stops the bounded OTLP exporter
        except Exception:
            pass
        print(json.dumps({'event': 'shutdown', 'service': 'project-xray',
                          'drained': completed, 'timeout_seconds': timeout}), flush=True)
        return completed


def install_drain_handlers(server, signals=(signal.SIGTERM, signal.SIGINT)):
    """Install bounded-drain signal handlers *before* announcing startup.

    The stopper runs on a non-daemon thread: the signal arrives on the main
    thread while it is parked in ``serve_forever()``, and ``shutdown()`` may not
    be called from that same thread (it would deadlock). Non-daemon means the
    interpreter waits for the drain to finish instead of exiting underneath it.
    """
    state = {'stopper': None}

    def _on_signal(signum, _frame):
        if state['stopper'] is not None:
            return  # second signal: already draining, stay idempotent
        stopper = threading.Thread(target=server.drain, name='xray-drain', daemon=False)
        state['stopper'] = stopper
        stopper.start()

    for sig in signals:
        try:
            signal.signal(sig, _on_signal)
        except (ValueError, OSError):  # non-main thread / unsupported platform
            pass
    return state


def main():
    init()
    server = BoundedHTTPServer((os.getenv('BIND_HOST', '127.0.0.1'), PORT), H)
    state = install_drain_handlers(server)
    print(json.dumps({'event': 'startup', 'service': 'project-xray', 'version': '0.4.8',
                      'port': PORT, 'environment': ENV,
                      'drain_seconds': float(os.getenv('XRAY_DRAIN_SECONDS', '15'))}), flush=True)
    try:
        server.serve_forever()
    finally:
        stopper = state['stopper']
        if stopper is not None:
            stopper.join()
        else:
            server.drain()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
