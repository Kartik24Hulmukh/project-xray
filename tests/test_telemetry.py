"""Real OTLP/HTTP sink, trace correlation, privacy and overload contracts."""
import contextlib
import io
import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from app import server, telemetry
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

VALID = '00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01'
SECRET = 'private-sentinel-credential'


class TelemetryTests(unittest.TestCase):
    def test_bounded_routes(self):
        self.assertEqual(telemetry.safe_route('/api/projects/private-id/claims?token='+SECRET),
                         '/api/projects/{id}/claims')
        self.assertEqual(telemetry.safe_route('/'+SECRET), '/unmatched')

    def test_disabled_by_default(self):
        with patch.dict(os.environ, {'XRAY_OTEL_ENABLED': '0'}):
            self.assertIsNone(telemetry.configured())

    def test_invalid_sampling_fails_closed(self):
        with patch.dict(os.environ, {'XRAY_OTEL_SAMPLE_RATIO': 'nan'}):
            with self.assertRaises(ValueError):
                telemetry.Telemetry(exporter=object())

    def test_failed_collector_does_not_block_requests(self):
        from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
        class Failed(SpanExporter):
            def export(self, spans):
                return SpanExportResult.FAILURE
        with patch.dict(os.environ, {'XRAY_OTEL_SAMPLE_RATIO': '1'}):
            observer = telemetry.Telemetry(exporter=Failed())
        class H:
            headers = {}
            command = 'GET'
            path = '/healthz'
        try:
            for _ in range(3000):
                with observer.request(H()):
                    pass
            self.assertTrue(observer.provider.force_flush(timeout_millis=3000))
        finally:
            observer.shutdown()

    def test_real_otlp_sink_correlation_privacy_429_503(self):
        received = []
        class Sink(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append(self.rfile.read(int(self.headers['Content-Length'])))
                self.send_response(200); self.end_headers()
            def log_message(self, *args):
                pass
        sink = ThreadingHTTPServer(('127.0.0.1', 0), Sink)
        threading.Thread(target=sink.serve_forever, daemon=True).start()
        exporter = OTLPSpanExporter(endpoint=f'http://127.0.0.1:{sink.server_port}/v1/traces', timeout=1)
        with patch.dict(os.environ, {'XRAY_OTEL_SAMPLE_RATIO': '1'}):
            observer = telemetry.Telemetry(exporter=exporter)
        class Handler(server.H):
            def _handle_get(self):
                if self.path.startswith('/busy'):
                    raise server.DatabaseBusy(SECRET)
                if self.path.startswith('/limited'):
                    return self.out({'error': 'rate limit exceeded'}, 429)
                return self.out({'status': 'ok'})
        with patch.dict(os.environ, {'XRAY_OTEL_ENABLED': '0'}):
            httpd = server.BoundedHTTPServer(('127.0.0.1', 0), Handler)
        httpd.telemetry = observer
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        output = io.StringIO()
        parents = []
        try:
            with contextlib.redirect_stdout(output):
                for path, expected in [('/healthz?token='+SECRET, 200), ('/limited', 429), ('/busy', 503)]:
                    request = urllib.request.Request(f'http://127.0.0.1:{httpd.server_port}'+path,
                        headers={'traceparent': VALID, 'Authorization': 'Bearer '+SECRET,
                                 'baggage': 'secret='+SECRET})
                    try:
                        response = urllib.request.urlopen(request, timeout=3)
                    except urllib.error.HTTPError as exc:
                        response = exc
                    with response:
                        self.assertEqual(response.status, expected)
                        parents.append(response.headers['traceparent'])
                        if expected == 503:
                            self.assertEqual(response.headers['Retry-After'], '1')
                        self.assertNotIn(SECRET, response.read().decode())
                self.assertTrue(observer.provider.force_flush(timeout_millis=3000))
        finally:
            httpd.shutdown(); httpd.server_close()
            sink.shutdown(); sink.server_close()
        spans = []
        for payload in received:
            self.assertNotIn(SECRET.encode(), payload)
            req = ExportTraceServiceRequest.FromString(payload)
            for resource in req.resource_spans:
                for scope in resource.scope_spans:
                    spans.extend(scope.spans)
        self.assertEqual(len(spans), 3)
        self.assertEqual({span.trace_id.hex() for span in spans}, {VALID.split('-')[1]})
        self.assertEqual({span.parent_span_id.hex() for span in spans}, {VALID.split('-')[2]})
        self.assertEqual({span.span_id.hex() for span in spans}, {p.split('-')[2] for p in parents})
        self.assertNotIn(SECRET, output.getvalue())
        logs = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual({line['span_id'] for line in logs}, {span.span_id.hex() for span in spans})
        self.assertEqual({line['status'] for line in logs}, {200, 429, 503})
        statuses = {int(attr.value.int_value): span for span in spans for attr in span.attributes
                    if attr.key == 'http.response.status_code'}
        self.assertEqual(statuses[503].status.code, 2)


if __name__ == '__main__':
    unittest.main()
