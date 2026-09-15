#!/usr/bin/env python3
"""Produce a signed recovery-drill evidence document.

Automatic collection is SQLite-only. PostgreSQL operators must explicitly
restore to an isolated target with recovery.py; a drill must never overwrite
the source DATABASE_URL. A fresh roundtrip does not measure disaster RPO.
"""
import argparse
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.recovery import backup, restore
from app.manifest import sign
from app.database import IS_POSTGRES


def collect(database, output, rpo_target_seconds=3600, rto_target_seconds=900, backup_key=None, audit_key=None):
    if IS_POSTGRES:
        raise RuntimeError(
            'Automatic PostgreSQL drill disabled: restore would overwrite DATABASE_URL. '
            'Back up the source, then run recovery.py restore with DATABASE_URL set '
            'to an isolated disposable target and explicit --force; retain both receipts.'
        )
    if rpo_target_seconds < 0 or rto_target_seconds < 0:
        raise ValueError('recovery targets must be non-negative')
    database = Path(database)
    output = Path(output)
    backup_key = backup_key or os.getenv("BACKUP_HMAC_KEY", "development-backup-key-not-for-production")
    audit_key = audit_key or os.getenv("AUDIT_HMAC_KEY", "development-audit-key-not-for-production")
    with tempfile.TemporaryDirectory() as d:
        archive = Path(d) / ("evidence-backup.dump" if IS_POSTGRES else "evidence-backup.db")
        restored = Path(d) / "clean" / ("restored.db" if not IS_POSTGRES else "restored.marker")
        start = time.monotonic()
        b = backup(database if not IS_POSTGRES else "DATABASE_URL", archive, backup_key, audit_key)
        backup_seconds = time.monotonic() - start
        start = time.monotonic()
        r = restore(archive, restored, force=True, key=backup_key, audit_key=audit_key)
        restore_seconds = time.monotonic() - start
        # A fresh local roundtrip does not measure loss at a disaster boundary.
        rpo_seconds = None
        payload = {
            "kind": "recovery_drill",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "backend": "postgres" if IS_POSTGRES else "sqlite",
            "database": database.name if not IS_POSTGRES else "DATABASE_URL",
            "backup_sha256": b["sha256"],
            "restored_sha256": r.get("sha256"),
            "audit_events": r["audit_events"],
            "audit_head": r["audit_head"],
            "backup_seconds": round(backup_seconds, 6),
            "restore_seconds": round(restore_seconds, 6),
            "rpo_seconds": rpo_seconds,
            "rpo_target_seconds": rpo_target_seconds,
            "rto_target_seconds": rto_target_seconds,
            "rpo_pass": False,
            "rpo_status": "not_measured",
            "scope": "local_backup_restore_roundtrip_not_disaster_recovery",
            "rto_pass": restore_seconds <= rto_target_seconds,
        }
        doc = {"payload": payload, "signature": sign(payload, backup_key)}
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
        os.chmod(output, 0o600)
        return doc


def main():
    p = argparse.ArgumentParser()
    p.add_argument("database")
    p.add_argument("output")
    p.add_argument("--rpo-target-seconds", type=int, default=3600)
    p.add_argument("--rto-target-seconds", type=int, default=900)
    a = p.parse_args()
    try:
        doc = collect(a.database, a.output, a.rpo_target_seconds, a.rto_target_seconds)
        print(json.dumps({"status": "ok", **doc["payload"]}, sort_keys=True))
        return 0
    except Exception as e:
        print(json.dumps({"status": "error", "error": str(e)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
