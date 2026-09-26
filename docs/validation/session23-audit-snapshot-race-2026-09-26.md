# Session 23 — audit single-snapshot reads & positional event_count (2026-09-26)

Branch: `fix/session23-audit-atomic-event-count` -> `harden/project-xray-v1-launch`.
Resolves the session-22 escalated residual: 2-3 `dependency_check_failed` /readyz 503s
in 2 of 4 five-wave soak runs.

## Root cause (reproduced, not guessed)

Instrumented soak runs (server log persisted) captured the failing probe:
`{"message":"readiness_dependency_check_failed","error_class":"RuntimeError"}` on
2 of 6 pre-fix runs (seed 20260926, 100 workers, 5 waves).

Three statement-snapshot races in `app/audit.py`:
1. `verify_head(strict_count=True)` read the head row, then the checkpoint row,
   then `COUNT(*)` - three separate snapshots on an autocommit read handle.
   A writer committing an (event, checkpoint) pair between them made
   `cp.event_count != COUNT(*)` -> healthy chain raised -> `/readyz` 503.
2. `verify()` materialised all checkpoints in one statement and walked all
   events in a second: an append in between produced an event with no
   checkpoint in the stale dict -> false tamper.
3. `append()` minted `event_count` from a bare whole-table `COUNT(*)` in its
   own snapshot after the insert; two interleaved writers could mint the same
   count for two events, permanently corrupting position checks.

## Fix (structural, no locks added, no sleeps)

- `verify_head`: ONE statement - head row LEFT JOIN checkpoint with the
  cardinality as a correlated subquery in the same snapshot.
- `verify()`: the same bounded single-snapshot event-LEFT-JOIN-checkpoint
  stream `verify_segment()` already used, then the exact anti-join orphan guard.
- `append()`: `event_count` = position = `COUNT(*) WHERE id <= own id`, fixed
  once the row exists (ids only grow; deletes blocked by triggers).

## Verification

| Check | Pre-fix | Post-fix |
|---|---|---|
| `tests/test_audit_snapshot_race.py` (4 tests, interleaved-writer injection at the exact pre-fix statements) | 3 errors | 4 OK |
| Full suite | 442 OK / 19 skipped | 446 OK / 19 skipped |
| 5-wave soak determinism gate, seed 20260926 x100c | 0/8 clean (2 of 6 instrumented runs had dependency_check_failed) | 8/8 + 3/3 receipted runs clean |
| 1-wave soak determinism gate | holds | 3/3 holds |
| Zero panics / tracebacks | 0 | 0 |

Receipts: `docs/validation/session23-soak-w1-run{0,1,2}-2026-09-26.json`,
`docs/validation/session23-soak-w5-run{0,1,2}-2026-09-26.json` (pass=true,
readyz_determinism_invariant=true in all six; 5-wave RSS wave growth 3.8-4.0 MiB
<= 8 MiB gate; health/ready recovery < 3 ms <= 200 ms bar).
