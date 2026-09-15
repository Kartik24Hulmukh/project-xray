# Bounded idempotency maintenance

Run `python3 scripts/reconcile_idempotency.py` with the **same database configuration and lease timeout as the server**. Default is dry-run; `--apply` performs one serialized write transaction. Schedule outside peak load. No background service or new public administrative endpoint is added.

Inspect JSON counters and pass the private `next_cursor` JSON to `--after` on the next transaction until null. The scan is bounded to 500 rows (maximum 5000) using the existing composite primary key. A null cursor ends the scan; a full final batch may require one empty follow-up. Restart from the beginning on the next scheduled sweep, so rows inserted behind the cursor are revisited. Do not log principal/key cursors publicly.

Processing leases older than the configured threshold may be reclaimed, consistent with existing request-path semantics; configure this longer than the maximum operation lifetime. Every delete matches the observed state, timestamps and request hash. Malformed or naive timestamps are never deleted. Live and future leases survive. Failures roll back the whole batch using the existing database abstraction.

**Completed receipts are retained indefinitely by default.** `--retention-seconds` (minimum 3600) explicitly opts into expiration measured from completed_at, ending exactly-once protection for retries older than that horizon. Select retention only after approving the client retry contract; it is not a safe generic disk-cleanup default.

Strict invalid values cause an error, not silent coercion. The CLI rejects a lease threshold below IDEMPOTENCY_STUCK_SECONDS. Reconciliation metrics are returned as structured JSON counters, not process-local HTTP metrics from an unrelated CronJob.

SQLite regressions cover snapshot fencing, rollback, cursors, malformed/live timestamps, fixed clocks and retention. Live PostgreSQL execution is a separate release gate, not implied by SQLite passes.
