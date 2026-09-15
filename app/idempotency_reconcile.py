"""Bounded offline maintenance; caller MUST hold database.db(write=True).

Keyset pagination bounds inspected rows, including malformed/live entries.
A full scan consists of bounded transactions, passing next_cursor until null.
Completed receipts are never expired unless retention is explicitly supplied.
"""
from datetime import datetime, timezone


def _integer(value, minimum, maximum, name):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f'{name} must be an integer in [{minimum}, {maximum}]')
    return value


def _timestamp(value):
    try:
        result = datetime.fromisoformat(value)
        if result.tzinfo is None:
            return None
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def reconcile(connection, *, reference=None, stuck_seconds=300,
              retention_seconds=None, batch=500, after=None, dry_run=True):
    """Inspect at most batch rows. Never commits; errors roll back in caller.

    Stuck threshold must match or exceed the server's configured lease timeout.
    Lowering it can reclaim a still-active operation; do not do this in production.
    Cursor contains principal/key metadata: retain it privately, not public logs.
    """
    _integer(batch, 1, 5000, 'batch')
    _integer(stuck_seconds, 30, 31536000, 'stuck_seconds')
    if retention_seconds is not None:
        _integer(retention_seconds, 3600, 315360000, 'retention_seconds')
    if not isinstance(dry_run, bool):
        raise ValueError('dry_run must be boolean')
    if after is not None and (not isinstance(after, (tuple, list)) or len(after) != 2
                              or not all(isinstance(v, str) for v in after)):
        raise ValueError('after must contain principal and key strings')
    reference = reference if reference is not None else datetime.now(timezone.utc)
    if not isinstance(reference, datetime) or reference.tzinfo is None:
        raise ValueError('reference must be timezone-aware datetime')
    reference = reference.astimezone(timezone.utc)
    query = 'SELECT principal,key,state,created_at,completed_at,request_hash FROM idempotency_keys'
    params = []
    if after is not None:
        query += ' WHERE (principal,key) > (?,?)'
        params.extend(after)
    query += ' ORDER BY principal,key LIMIT ?'
    params.append(batch)
    rows = connection.execute(query, params).fetchall()
    out = dict(inspected=len(rows), eligible=0, reservations_reconciled=0,
               receipts_expired=0, skipped_unparsable=0, fence_lost=0,
               dry_run=dry_run, next_cursor=None, reference=reference.isoformat())
    if len(rows) == batch:
        out['next_cursor'] = [rows[-1]['principal'], rows[-1]['key']]
    for row in rows:
        state = row['state']
        if state == 'completed' and retention_seconds is None:
            continue
        if state not in ('processing', 'completed'):
            continue
        stamp = _timestamp(row['created_at'] if state == 'processing' else row['completed_at'])
        if stamp is None:
            out['skipped_unparsable'] += 1
            continue
        threshold = stuck_seconds if state == 'processing' else retention_seconds
        if (reference - stamp).total_seconds() <= threshold:
            continue
        out['eligible'] += 1
        if dry_run:
            continue
        result = connection.execute(
            'DELETE FROM idempotency_keys WHERE principal=? AND key=? AND state=? '
            'AND created_at=? AND completed_at=? AND request_hash=?',
            tuple(row[k] for k in ('principal', 'key', 'state', 'created_at', 'completed_at', 'request_hash')))
        if result.rowcount == 1:
            out['reservations_reconciled' if state == 'processing' else 'receipts_expired'] += 1
        else:
            out['fence_lost'] += 1
    return out
