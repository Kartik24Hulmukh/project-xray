"""Opt-in, bounded OTLP/HTTP server tracing; no payload/header/query capture."""
import os
import re
from contextlib import contextmanager


def safe_route(path):
    # Keep cardinality bounded. Do not export identifiers or unrecognised paths.
    path = path.split('?', 1)[0]
    if path in {'/healthz', '/health', '/livez', '/readyz', '/ready', '/',
                '/api/projects', '/api/metrics', '/app.js', '/styles.css'}:
        return path
    if re.fullmatch(r'/api/projects/[^/]+(?:/(?:claims|sources|documents|gaps|responses|publish|report|rti|capsule))?', path):
        parts = path.split('/')
        return '/api/projects/{id}' + ('/' + parts[4] if len(parts) == 5 else '')
    return '/unmatched'


class Telemetry:
    def __init__(self, exporter=None):
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
        ratio = float(os.getenv('XRAY_OTEL_SAMPLE_RATIO', '0.1'))
        if not 0 <= ratio <= 1:
            raise ValueError('XRAY_OTEL_SAMPLE_RATIO must be between 0 and 1')
        if exporter is None:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            exporter = OTLPSpanExporter(timeout=2)
        self.provider = TracerProvider(resource=Resource.create({'service.name': 'project-xray'}),
                                       sampler=ParentBased(TraceIdRatioBased(ratio)))
        # A failed collector drops spans rather than consuming unbounded memory.
        self.provider.add_span_processor(BatchSpanProcessor(exporter, max_queue_size=2048,
            max_export_batch_size=256, schedule_delay_millis=1000, export_timeout_millis=2000))
        self.tracer = self.provider.get_tracer('project-xray.http', '1')

    @contextmanager
    def request(self, handler):
        from opentelemetry.trace import SpanKind, StatusCode
        from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
        header = handler.headers.get('traceparent', '')
        # Drop baggage/tracestate: neither is needed and both may contain secrets.
        context = TraceContextTextMapPropagator().extract({'traceparent': header})
        method = handler.command if handler.command in {'GET', 'POST', 'OPTIONS'} else 'OTHER'
        route = safe_route(handler.path)
        with self.tracer.start_as_current_span(method + ' ' + route, context=context,
                kind=SpanKind.SERVER, record_exception=False, set_status_on_exception=False,
                attributes={'http.request.method': method, 'http.route': route}) as span:
            handler._otel_span = span
            handler._trace = None
            try:
                yield
            except BaseException:
                span.set_status(StatusCode.ERROR)
                raise
            finally:
                handler._otel_span = None

    def shutdown(self):
        self.provider.shutdown()


def configured():
    # Stdlib-only local mode remains supported; enabling requires installed SDK.
    return Telemetry() if os.getenv('XRAY_OTEL_ENABLED', '0') == '1' else None
