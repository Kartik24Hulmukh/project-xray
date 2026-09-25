import hashlib,hmac

def event_hash(previous,event_id,actor,action,object_type,object_id,detail,created_at):return hashlib.sha256('|'.join((previous,event_id,actor,action,object_type,object_id,detail,created_at)).encode()).hexdigest()
def checkpoint_signature(event_id,event_count,head_hash,key):return hmac.new(key.encode(),f'{event_id}|{event_count}|{head_hash}'.encode(),hashlib.sha256).hexdigest()
def append(c,event_id,actor,action,object_type,object_id,detail,created_at,key):
 if getattr(c, '_is_pg', False):c.execute('SELECT pg_advisory_xact_lock(1481785689)')
 prev=c.execute('SELECT event_hash FROM audit_events ORDER BY id DESC LIMIT 1').fetchone();previous=prev['event_hash'] if prev else ''
 head=event_hash(previous,event_id,actor,action,object_type,object_id,detail,created_at)
 c.execute('INSERT INTO audit_events(event_id,actor,action,object_type,object_id,detail,previous_hash,event_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(event_id,actor,action,object_type,object_id,detail,previous,head,created_at))
 count=c.execute('SELECT COUNT(*) n FROM audit_events').fetchone()['n'];signature=checkpoint_signature(event_id,count,head,key)
 c.execute('INSERT INTO audit_checkpoints(event_id,event_count,head_hash,signature,created_at) VALUES(?,?,?,?,?)',(event_id,count,head,signature,created_at));return head
def verify(c,key):
 if getattr(c, '_is_pg', False):c.execute('SELECT pg_advisory_xact_lock(1481785689)')
 previous='';count=0
 checkpoints={r['event_id']:r for r in c.execute('SELECT * FROM audit_checkpoints')}
 for r in c.execute('SELECT * FROM audit_events ORDER BY id'):
  count+=1;expected=event_hash(previous,r['event_id'],r['actor'],r['action'],r['object_type'],r['object_id'],r['detail'],r['created_at'])
  if r['previous_hash']!=previous or r['event_hash']!=expected:raise RuntimeError(f'audit chain broken at id={r["id"]}')
  cp=checkpoints.get(r['event_id'])
  if not cp or cp['event_count']!=count or cp['head_hash']!=expected:raise RuntimeError(f'audit checkpoint missing or inconsistent at id={r["id"]}')
  signature=checkpoint_signature(r['event_id'],count,expected,key)
  if not hmac.compare_digest(cp['signature'],signature):raise RuntimeError(f'audit checkpoint signature invalid at id={r["id"]}')
  previous=expected
 if len(checkpoints)!=count:raise RuntimeError('orphan audit checkpoint detected')
 return {'events':count,'head':previous}


def verify_head(c,key,strict_count=True):
 """O(1) audit head check: validates the newest event's checkpoint HMAC.

 Confirms the head event has a checkpoint whose event_count matches the
 table cardinality and whose signature binds (event_id,count,head_hash)
 under `key`. It does NOT walk the chain; callers that need full-prefix
 integrity must use verify(). Raises RuntimeError on any inconsistency.
 """
 if getattr(c, '_is_pg', False):c.execute('SELECT pg_advisory_xact_lock(1481785689)')
 row=c.execute('SELECT * FROM audit_events ORDER BY id DESC LIMIT 1').fetchone()
 if not row:
  n=c.execute('SELECT COUNT(*) n FROM audit_checkpoints').fetchone()['n']
  if n:raise RuntimeError('orphan audit checkpoint detected')
  return {'events':0,'head':''}
 expected=event_hash(row['previous_hash'],row['event_id'],row['actor'],row['action'],row['object_type'],row['object_id'],row['detail'],row['created_at'])
 if row['event_hash']!=expected:raise RuntimeError('audit head event hash mismatch')
 cp=c.execute('SELECT * FROM audit_checkpoints WHERE event_id=?',(row['event_id'],)).fetchone()
 if not cp or cp['head_hash']!=row['event_hash']:raise RuntimeError('audit head checkpoint missing or inconsistent')
 # strict_count=False skips the O(n) cardinality scan on the hot probe path;
 # the head hash and its HMAC are still recomputed, and deletions inside the
 # chain are caught by streaming full verification and the orphan guard.
 n=cp['event_count'] if not strict_count else c.execute('SELECT COUNT(*) n FROM audit_events').fetchone()['n']
 if cp['event_count']!=n:raise RuntimeError('audit head checkpoint missing or inconsistent')
 signature=checkpoint_signature(row['event_id'],n,row['event_hash'],key)
 if not hmac.compare_digest(cp['signature'],signature):raise RuntimeError('audit head checkpoint signature invalid')
 return {'events':n,'head':row['event_hash']}


_VERIFY_STREAM_BATCH=256


def verify_segment(c,key,start_id,previous,limit,count_offset):
 """Verify a bounded slice of the audit chain without materialising it.

 Streams at most `limit` events with id > start_id, joined to their
 checkpoint row, so peak memory is O(_VERIFY_STREAM_BATCH) rather than O(limit) or O(chain). Returns
 the resumable cursor plus whether the tail of the chain was reached.
 Raises RuntimeError on any tamper, exactly like verify().
 """
 cur=c.execute('SELECT e.id AS id,e.event_id AS event_id,e.actor AS actor,e.action AS action,e.object_type AS object_type,e.object_id AS object_id,e.detail AS detail,e.previous_hash AS previous_hash,e.event_hash AS event_hash,e.created_at AS created_at,cp.event_count AS cp_count,cp.head_hash AS cp_head,cp.signature AS cp_signature FROM audit_events e LEFT JOIN audit_checkpoints cp ON cp.event_id=e.event_id WHERE e.id>? ORDER BY e.id LIMIT ?',(start_id,limit))
 count=count_offset;last_id=start_id;scanned=0
 while True:
  batch=cur.fetchmany(_VERIFY_STREAM_BATCH)
  if not batch:break
  for r in batch:
   scanned+=1;count+=1
   expected=event_hash(previous,r['event_id'],r['actor'],r['action'],r['object_type'],r['object_id'],r['detail'],r['created_at'])
   if r['previous_hash']!=previous or r['event_hash']!=expected:raise RuntimeError(f'audit chain broken at id={r["id"]}')
   if r['cp_signature'] is None or r['cp_count']!=count or r['cp_head']!=expected:raise RuntimeError(f'audit checkpoint missing or inconsistent at id={r["id"]}')
   if not hmac.compare_digest(r['cp_signature'],checkpoint_signature(r['event_id'],count,expected,key)):raise RuntimeError(f'audit checkpoint signature invalid at id={r["id"]}')
   previous=expected;last_id=r['id']
 return {'last_id':last_id,'previous':previous,'count':count,'scanned':scanned,'complete':scanned<limit}


def verify_orphans(c,count):
 """Cardinality guard run once per completed streaming verification."""
 n=c.execute('SELECT COUNT(*) n FROM audit_checkpoints').fetchone()['n']
 if n!=count:raise RuntimeError('orphan audit checkpoint detected')
 return count
