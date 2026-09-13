"""Authentication helpers for Project X-Ray India.

OIDC proxy assertion policy (fail-closed):
- MFA must be true
- Signature must validate over the exact header values
- Timestamp must be within max_age seconds
- Subject must match a conservative format/length bound
- Exactly one recognized application role is required after normalization
- Zero recognized roles: deny
- More than one distinct recognized role: deny as ambiguous
- Duplicate of the same normalized role collapses to one
- Unknown roles never grant access
- Replay protection blocks reuse of the same signed assertion
- Gateways SHOULD send a unique X-Auth-Nonce per request so multi-call
  sessions are not treated as replays when timestamps share a second
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone

try:
    from app.gateway import assertion_signature, stable_principal
except ModuleNotFoundError:
    from gateway import assertion_signature, stable_principal

RECOGNIZED_ROLES = frozenset({'admin', 'reviewer', 'scanner'})
_SUBJECT_RE = re.compile(r'^[A-Za-z0-9._:@+/-]{1,200}$')
_NONCE_RE = re.compile(r'^[A-Za-z0-9._:-]{1,128}$')

# Process-local replay cache: signature -> expiry epoch
_replay_lock = threading.Lock()
_replay_seen = OrderedDict()
_REPLAY_MAX = 4096


def token_hash(token, pepper):
    return hmac.new(pepper.encode(), token.encode(), hashlib.sha256).hexdigest()


def iso_now():
    return datetime.now(timezone.utc).isoformat()


def proxy_signature(subject, roles, mfa, timestamp, secret, nonce=''):
    """HMAC over subject|roles|mfa|timestamp[|nonce]."""
    parts = [subject, roles, mfa, timestamp]
    if nonce:
        parts.append(nonce)
    message = '|'.join(parts)
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def normalize_roles(roles_header):
    """Normalize a comma-separated roles header into ordered unique roles.

    Returns (recognized_unique_roles, had_unknown).
    Whitespace is stripped; case is lowercased for matching.
    """
    if roles_header is None:
        return [], False
    raw_parts = [p.strip().lower() for p in str(roles_header).split(',')]
    raw_parts = [p for p in raw_parts if p]
    recognized = []
    seen = set()
    had_unknown = False
    for part in raw_parts:
        if part in RECOGNIZED_ROLES:
            if part not in seen:
                recognized.append(part)
                seen.add(part)
        else:
            had_unknown = True
    return recognized, had_unknown


def resolve_single_role(roles_header):
    """Fail-closed role resolution.

    Accepts exactly one distinct recognized role. Duplicates of the same
    role are collapsed. Unknown-only or multi-role assertions are denied.
    Presence of unknown roles alongside a recognized role is denied.
    """
    recognized, had_unknown = normalize_roles(roles_header)
    if had_unknown:
        return None
    if len(recognized) != 1:
        return None
    return recognized[0]


def _remember_assertion(signature, expires_at):
    """Consume once without evicting unexpired replay evidence.

    Fail closed at capacity. Expiries need not follow insertion order (gateway
    and legacy assertions have different lifetimes and clock skew).
    This cache is process-local; it is not cross-replica replay protection.
    """
    now = time.time()
    with _replay_lock:
        expired = [key for key, expiry in _replay_seen.items() if expiry < now]
        for key in expired:
            del _replay_seen[key]
        if signature in _replay_seen or len(_replay_seen) >= _REPLAY_MAX:
            return False
        _replay_seen[signature] = expires_at
    return True


def verify_proxy(headers, secret, max_age=None, allow_replay=False):
    """Verify OIDC proxy headers.

    Returns (role, subject) on success, else None.
    """
    if not secret:
        return None
    if max_age is None:
        max_age = int(os.getenv('OIDC_MAX_AGE_SECONDS', '90'))

    subject = headers.get('X-Auth-Subject', '').strip()
    roles = headers.get('X-Auth-Roles', '').strip()
    mfa = headers.get('X-Auth-MFA', '').strip().lower()
    stamp = headers.get('X-Auth-Timestamp', '').strip()
    signature = headers.get('X-Auth-Signature', '').strip()
    nonce = headers.get('X-Auth-Nonce', '').strip()

    if not all((subject, roles, stamp, signature)) or mfa != 'true':
        return None
    if not _SUBJECT_RE.match(subject):
        return None
    if nonce and not _NONCE_RE.match(nonce):
        return None
    if len(signature) > 128 or not re.fullmatch(r'[0-9a-fA-F]+', signature):
        return None

    try:
        ts = int(stamp)
    except ValueError:
        return None
    now = time.time()
    if abs(now - ts) > max_age:
        return None

    expected = proxy_signature(subject, roles, mfa, stamp, secret, nonce=nonce)
    if not hmac.compare_digest(signature, expected):
        return None

    role = resolve_single_role(roles)
    if role is None:
        return None

    if not allow_replay:
        # Replay protection: same signed assertion cannot be reused.
        if not _remember_assertion(signature.lower(), ts + max_age + 1):
            return None

    return (role, subject)


def _header(headers, name):
    """Case-insensitive single-value lookup for dict-like header objects."""
    matches = [
        str(value)
        for key, value in headers.items()
        if str(key).lower() == name.lower()
    ]
    return matches[0].strip() if len(matches) == 1 else ''


def verify_gateway_assertion(
    headers,
    keys,
    expected_audience,
    expected_issuers,
    now_epoch=None,
    consume_replay=True,
):
    """Verify the frozen v1 trusted-gateway assertion.

    Returns ``(role, stable_principal)`` or ``None``.  The upstream ALB JWT
    must already have been verified by the gateway; this verifies only the
    gateway-to-application assertion.
    """
    fields = {
        'version': _header(headers, 'X-Auth-Version'),
        'issuer': _header(headers, 'X-Auth-Issuer'),
        'subject': _header(headers, 'X-Auth-Subject'),
        'principal': _header(headers, 'X-Auth-Principal'),
        'role': _header(headers, 'X-Auth-Role'),
        'mfa': _header(headers, 'X-Auth-MFA').lower(),
        'issued_at': _header(headers, 'X-Auth-Issued-At'),
        'expires_at': _header(headers, 'X-Auth-Expires-At'),
        'audience': _header(headers, 'X-Auth-Audience'),
        'nonce': _header(headers, 'X-Auth-Nonce'),
        'key_id': _header(headers, 'X-Auth-Key-Id'),
    }
    signature = _header(headers, 'X-Auth-Signature').lower()
    if not all(fields.values()) or not signature:
        return None
    if fields['version'] != '1' or fields['mfa'] != 'true':
        return None
    if fields['issuer'] not in set(expected_issuers):
        return None
    if fields['audience'] != expected_audience:
        return None
    if fields['role'] not in RECOGNIZED_ROLES:
        return None
    if not _SUBJECT_RE.fullmatch(fields['subject']):
        return None
    if not _NONCE_RE.fullmatch(fields['nonce']) or len(fields['nonce']) < 16:
        return None
    secret = keys.get(fields['key_id']) if isinstance(keys, dict) else None
    if not secret or len(secret) < 32:
        return None
    try:
        issued_at = int(fields['issued_at'])
        expires_at = int(fields['expires_at'])
    except ValueError:
        return None
    current = int(time.time()) if now_epoch is None else int(now_epoch)
    if issued_at > current + 5 or expires_at <= current:
        return None
    if expires_at <= issued_at or expires_at - issued_at > 30:
        return None
    expected_principal = stable_principal(fields['issuer'], fields['subject'])
    if not hmac.compare_digest(fields['principal'], expected_principal):
        return None
    payload = {
        'audience': fields['audience'],
        'expires_at': expires_at,
        'issued_at': issued_at,
        'issuer': fields['issuer'],
        'key_id': fields['key_id'],
        'mfa': True,
        'nonce': fields['nonce'],
        'principal': fields['principal'],
        'role': fields['role'],
        'subject': fields['subject'],
        'version': 1,
    }
    try:
        expected_signature = assertion_signature(payload, secret)
    except (TypeError, ValueError):
        return None
    if not re.fullmatch(r'[0-9a-f]{64}', signature):
        return None
    if not hmac.compare_digest(signature, expected_signature):
        return None
    if consume_replay and not _remember_assertion(signature, expires_at + 1):
        return None
    return fields['role'], fields['principal']


def clear_replay_cache():
    """Test helper to reset replay state."""
    with _replay_lock:
        _replay_seen.clear()
