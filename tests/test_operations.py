import hashlib,hmac,json,sqlite3,tempfile,threading,unittest
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from pathlib import Path
from app.storage import verify_managed_object
from app.operations import send_alert
from scripts.recovery_evidence import collect
ROOT=Path(__file__).resolve().parents[1]
class Fake(BaseHTTPRequestHandler):
 received=None
 def log_message(self,*args):pass
 def do_HEAD(self):
  Fake.received=dict(self.headers);self.send_response(200);self.send_header('Content-Length','128');self.send_header('x-amz-meta-sha256','a'*64);self.end_headers()
 def do_POST(self):
  body=self.rfile.read(int(self.headers['Content-Length']));Fake.received=(dict(self.headers),body);self.send_response(204);self.end_headers()
class TestOperations(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.http=ThreadingHTTPServer(('127.0.0.1',0),Fake);threading.Thread(target=cls.http.serve_forever,daemon=True).start();cls.url=f'http://127.0.0.1:{cls.http.server_address[1]}'
 @classmethod
 def tearDownClass(cls):cls.http.shutdown();cls.http.server_close()
 def test_managed_storage_head_is_signed_and_metadata_bound(self):
  result=verify_managed_object('s3://evidence/case/a.pdf','a'*64,128,self.url,'evidence','access','secret','ap-south-1');self.assertEqual(result['size_bytes'],128);self.assertTrue(Fake.received['Authorization'].startswith('AWS4-HMAC-SHA256 '))
  with self.assertRaises(RuntimeError):verify_managed_object('s3://evidence/case/a.pdf','b'*64,128,self.url,'evidence','access','secret','ap-south-1')
 def test_monitoring_webhook_is_authenticated(self):
  secret='monitoring-secret-12345678901234567890';payload=send_alert({'severity':'test'},self.url,secret);headers,body=Fake.received;expected='sha256='+hmac.new(secret.encode(),body,hashlib.sha256).hexdigest();self.assertEqual(next(v for k,v in headers.items() if k.lower()=='x-project-xray-signature'),expected);self.assertEqual(json.loads(body)['event_id'],payload['event_id'])
 def test_recovery_evidence_is_signed_and_measured(self):
  with tempfile.TemporaryDirectory() as d:
   db=Path(d)/'source.db';c=sqlite3.connect(db);c.executescript((ROOT/'db/schema.sql').read_text());c.commit();c.close();out=Path(d)/'evidence.json';doc=collect(db,out,10,10,'b'*40,'a'*40);self.assertTrue(out.exists());self.assertTrue(doc['payload']['rto_pass']);self.assertEqual(len(doc['signature']),64)
if __name__=='__main__':unittest.main()
