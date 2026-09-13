# Canonical probes and OpenTelemetry runbook

Canonical process liveness is `GET /healthz`; dependency/audit readiness is `GET /readyz`.
Legacy `/health`, `/livez` and `/ready` remain compatible. Probes require no token
and are exempt from rate limits. Docker/CI use the canonical names.

## Opt-in OTLP/HTTP

Install `requirements.txt` (SDK and HTTP exporter pinned to 1.37.0). Set:

```sh
XRAY_OTEL_ENABLED=1
XRAY_OTEL_SAMPLE_RATIO=0.1
OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=https://collector.internal/v1/traces
```

Use a trusted internal collector, configure TLS verification and exporter credentials
via secret manager (`OTEL_EXPORTER_OTLP_HEADERS`), never in source control. Default
is disabled so stdlib-only local runs still work. Enabling without SDK or with an
invalid sampling ratio fails startup. ParentBased sampling respects upstream sampled
flags; ratio applies to root spans only. Filter untrusted trace headers at ingress if
clients must not control sampling. This is tracing, not OTel metrics or profiling.

Queue: 2048 spans, batch: 256, scheduled export every 1s, exporter timeout 2s.
Export is off the handler thread; collector failures may drop spans, not HTTP work.
Span lifetime includes execution-gate wait after HTTP headers have been parsed.
Server span IDs match response `traceparent` and JSON access logs. No body, query,
authorization, baggage, arbitrary paths or entity IDs are exported. Routes are
allowlisted/template-normalized. Access logs no longer print raw request lines;
error logs normalize paths and omit exception messages. Client IP remains in access
logs under the existing privacy/retention policy.

## Acceptance and rollback

```sh
python3 -m unittest discover -s tests -p 'test_telemetry.py' -v
python3 -m unittest discover -s tests -p 'test_probes.py' -v
```

The test decodes actual protobuf received by a loopback OTLP/HTTP collector and
checks parent/child IDs, log/header correlation, status 429/503 and secret absence.
A failing exporter is exercised with 3000 spans. This is not evidence that a
production collector has received spans. Before enabling in production, send a
synthetic request to staging, locate the trace at your configured sink, verify
redaction/retention and alerting, then measure the 100-client harness with tracing
on. Budget up to queue size of lost spans on abrupt process termination; the
existing SIGTERM path is not claimed to drain active requests or flush telemetry.

Disable `XRAY_OTEL_ENABLED` and restart to roll back observability without schema
changes. Roll back an immutable application image after taking a database backup;
never roll back the database by deleting audited evidence. Re-run canonical probes,
smoke, persistence, backup/restore and editorial gates before reopening traffic.
