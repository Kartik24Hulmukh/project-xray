#!/usr/bin/env python3
"""One bounded maintenance transaction; dry-run unless --apply is supplied.

Use the same DB configuration and IDEMPOTENCY_STUCK_SECONDS as the server.
Explicit receipt expiration ends exactly-once replay protection after retention.
"""
import argparse
import json
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import database
from app.idempotency_reconcile import reconcile


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--batch', type=int, default=500)
    ap.add_argument('--stuck-seconds', type=int, default=int(os.getenv('IDEMPOTENCY_STUCK_SECONDS', '300')))
    ap.add_argument('--retention-seconds', type=int, default=None)
    ap.add_argument('--after', help='private JSON [principal,key] cursor from previous receipt')
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    try:
        server_threshold = max(30, int(os.getenv('IDEMPOTENCY_STUCK_SECONDS', '300')))
        if args.stuck_seconds < server_threshold:
            raise ValueError('stuck-seconds must not be below server lease timeout')
        with database.db(write=True) as connection:
            result = reconcile(connection, batch=args.batch, stuck_seconds=args.stuck_seconds,
                               retention_seconds=args.retention_seconds,
                               after=json.loads(args.after) if args.after else None, dry_run=not args.apply)
    except (ValueError, TypeError) as error:
        ap.error(str(error))
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
