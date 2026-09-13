# Idempotency-Key client contract (v0.4.6)

In `APP_ENV=production` every `POST` to `/api/*` **must** carry an `Idempotency-Key`
header (1-128 chars, unique per logical operation, per principal). The server
guarantees **at-most-one committed write per (principal, key)** and replays the
stored response for later identical requests.

## How to retry (do this exactly)

| Response | Meaning | Client action |
|---|---|---|
| `2xx` | Committed. Body is authoritative and will be replayed verbatim for the same key. | Done. |
| `409` `...is processing` | Another request with this key is in flight (fresh lease). | **Retry with the SAME key** after `Retry-After`/1-2 s with backoff. Never mint a new key. |
| `409` `...reclaimed by a concurrent retry` | This worker lost its lease to your own retry; nothing was written by it. | **Retry with the SAME key** once; you will receive the retry's stored response. |
| `409` `...reused with different request` | Same key, different payload. | Bug on the client side: fix the payload or use a new key. |
| `400`/`413`/`422` | Rejected before any write; the reservation is released immediately. | Fix the request; the same key may be reused for the corrected payload. |
| `500`/`503` (JSON body with `request_id`) | Not committed; reservation released. | **Retry with the SAME key** with exponential backoff (1s, 2s, 4s...). Quote `request_id` in support requests. |

## Server-side guarantees behind this contract

* Reservation rows are `processing` -> `completed`; any path that does not acknowledge
  a 2xx to the client deletes the reservation in `finally` (crash, commit failure, rejection).
* A `processing` reservation older than `IDEMPOTENCY_STUCK_SECONDS` (default 300 s,
  clamped to a 30 s floor) is reclaimed; the reclaim is recorded in the hash-chained audit
  ledger (`action=reclaim`) and counted in `project_xray_idempotency_stuck_reclaims_total`.
* **Fencing:** completion and release are conditional on the reservation's own lease token
  (`created_at`). A stale worker whose lease was reclaimed aborts *before* commit with the
  `reclaimed` 409 and increments `project_xray_idempotency_lease_lost_total`; it can never
  create a duplicate resource or clobber the retry's reservation.

## Alerting

* `idempotency_stuck_reclaims_total > 0` over 5 min: workers are dying mid-request (crash loop / OOM).
* `idempotency_lease_lost_total > 0`: a request ran longer than the stuck TTL; raise
  `IDEMPOTENCY_STUCK_SECONDS` or fix the slow path.
