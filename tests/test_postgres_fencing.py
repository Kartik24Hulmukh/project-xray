"""Live PostgreSQL proofs for idempotency lease fencing predicates."""
import os, sys, unittest, uuid
from pathlib import Path
if not os.getenv("DATABASE_URL"):
    raise unittest.SkipTest("requires live PostgreSQL")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.database import db

class TestPostgresFencing(unittest.TestCase):
    def seed(self):
        key = "pg-fence-" + uuid.uuid4().hex
        with db(True) as c:
            c.execute("INSERT INTO idempotency_keys(principal,key,request_hash,state,created_at) VALUES(?,?,?,?,?)", ("pg-test",key,"a"*64,"processing","2026-09-13T00:00:00+00:00"))
        return key

    def test_completion_rejects_stale_lease(self):
        key=self.seed()
        with db(True) as c:
            cur=c.execute("UPDATE idempotency_keys SET state='completed',response_code=201,response_body='{}' WHERE principal=? AND key=? AND state='processing' AND created_at=?", ("pg-test",key,"stale"))
            self.assertEqual(cur.rowcount,0)

    def test_release_rejects_stale_lease(self):
        key=self.seed()
        with db(True) as c:
            cur=c.execute("DELETE FROM idempotency_keys WHERE principal=? AND key=? AND state='processing' AND created_at=?", ("pg-test",key,"stale"))
            self.assertEqual(cur.rowcount,0)

    def test_reclaim_exactly_one_winner(self):
        key=self.seed()
        with db(True) as c:
            first=c.execute("DELETE FROM idempotency_keys WHERE principal=? AND key=? AND state='processing' AND created_at=?", ("pg-test",key,"2026-09-13T00:00:00+00:00"))
            second=c.execute("DELETE FROM idempotency_keys WHERE principal=? AND key=? AND state='processing' AND created_at=?", ("pg-test",key,"2026-09-13T00:00:00+00:00"))
            self.assertEqual((first.rowcount,second.rowcount),(1,0))

if __name__ == "__main__": unittest.main()
