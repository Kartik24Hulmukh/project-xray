import hashlib,hmac

def event_hash(previous,event_id,actor,action,object_type,object_id,detail,created_at):return hashlib.sha256('|'.join((previous,event_id,actor,action,object_type,object_id,detail,created_at)).encode()).hexdigest()
def checkpoint_signature(event_id,event_count,head_hash,key):return hmac.new(key.encode(),f'{event_id}|{event_count}|{head_hash}'.encode(),hashlib.sha256).hexdigest()
def append(c,event_id,actor,action,object_type,object_id,detail,created_at,key):
 if getattr(c, '_is_pg', False):c.execute('SELECT pg_advisory_xact_lock(1481785689)')
 prev=c.execute('SELECT event_hash FROM audit_events ORDER BY id DESC LIMIT 1').fetchone();previous=prev['event_hash'] if prev else ''
 head=event_hash(previous,event_id,actor,action,object_type,object_id,detail,created_at)
 c.execute('INSERT INTO audit_events(event_id,actor,action,object_type,object_id,detail,previous_hash,event_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(event_id,actor,action,object_type,object_id,detail,previous,head,created_at))
 # Session 23 root cause: event_count used to be a bare COUNT(*) of the whole
 # table, read in its own statement/snapshot after the insert. Two writers
 # interleaved (autocommit SQLite handles, or PG if the advisory lock were
 # ever bypassed) could mint the SAME count for two events, which later made
 # a perfectly healthy chain fail the position check in verify / streaming
 # verification. The count is now the event's POSITION in the chain -
 # COUNT(*) over id <= this event's own id. That value is fixed once the row
 # exists (ids only grow; deletes are blocked by triggers), so it is immune
 # to any concurrent append and equals the walk order used by verifiers.
 own=c.execute('SELECT id FROM audit_events WHERE event_id=?',(event_id,)).fetchone()
 count=c.execute('SELECT COUNT(*) n FROM audit_events WHERE id<=?',(own['id'],)).fetchone()['n']
 signature=checkpoint_signature(event_id,count,head,key)
 c.execute('INSERT INTO audit_checkpoints(event_id,event_count,head_hash,signature,created_at) VALUES(?,?,?,?,?)',(event_id,count,head,signature,created_at));return head
def verify(c,key):
 """Full-prefix chain verification, race-free under concurrent appends.

 Session 23: this used to materialise every checkpoint in one statement and
 then walk every event in a SECOND statement. Two snapshots means a writer
 that commits an (event, checkpoint) pair in between shows up as an event
 with no checkpoint - a false 'audit checkpoint missing or inconsistent' on a
 healthy chain. The walk is now the same single-statement event LEFT JOIN
 checkpoint stream that verify_segment() uses (one snapshot per bounded
 segment, count derived from id order), followed by the exact anti-join
 orphan guard. Tamper detection is unchanged: broken hashes, wrong counts,
 bad signatures and deleted events all still raise.
 """
 if getattr(c, '_is_pg', False):c.execute('SELECT pg_advisory_xact_lock(1481785689)')
 previous='';count=0;last_id=0
 while True:
  segment=verify_segment(c,key,last_id,previous,_VERIFY_STREAM_BATCH,count)
  count=segment['count'];previous=segment['previous'];last_id=segment['last_id']
  if segment['complete']:break
 verify_orphans(c,count)
 return {'events':count,'head':previous}


def verify_head(c,key,strict_count=True):
 """O(1) audit head check: validates the newest event's checkpoint HMAC.

 Confirms the head event has a checkpoint whose event_count matches the
 table cardinality and whose signature binds (event_id,count,head_hash)
 under `key`. It does NOT walk the chain; callers that need full-prefix
 integrity must use verify(). Raises RuntimeError on any inconsistency.
 """
 if getattr(c, '_is_pg', False):c.execute('SELECT pg_advisory_xact_lock(1481785689)')
 # Session 23 root cause: the head row, its checkpoint and the cardinality
 # COUNT(*) used to be three separate statements. On an autocommit read
 # connection (SQLite) or READ COMMITTED (PostgreSQL) each statement takes its
 # own snapshot, so a writer committing an (event, checkpoint) pair between
 # them made cp.event_count != COUNT(*) and a HEALTHY chain raised
 # 'audit head checkpoint missing or inconsistent' - the residual
 # dependency_check_failed 503s in the session-22 5-wave soak. One statement
 # is one snapshot on both engines, so the proof is now race-free.
 row=c.execute('SELECT e.previous_hash AS previous_hash,e.event_id AS event_id,e.actor AS actor,e.action AS action,e.object_type AS object_type,e.object_id AS object_id,e.detail AS detail,e.created_at AS created_at,e.event_hash AS event_hash,cp.event_count AS cp_count,cp.head_hash AS cp_head,cp.signature AS cp_signature,(SELECT COUNT(*) FROM audit_events) AS n FROM audit_events e LEFT JOIN audit_checkpoints cp ON cp.event_id=e.event_id ORDER BY e.id DESC LIMIT 1').fetchone()
 if not row:
  n=c.execute('SELECT COUNT(*) n FROM audit_checkpoints').fetchone()['n']
  if n:raise RuntimeError('orphan audit checkpoint detected')
  return {'events':0,'head':''}
 expected=event_hash(row['previous_hash'],row['event_id'],row['actor'],row['action'],row['object_type'],row['object_id'],row['detail'],row['created_at'])
 if row['event_hash']!=expected:raise RuntimeError('audit head event hash mismatch')
 if row['cp_head'] is None:raise RuntimeError('audit head checkpoint missing or inconsistent')
 if row['cp_head']!=row['event_hash']:raise RuntimeError('audit head checkpoint missing or inconsistent')
 # strict_count=False skips the O(n) cardinality scan on the hot probe path;
 # the head hash and its HMAC are still recomputed, and deletions inside the
 # chain are caught by streaming full verification and the orphan guard.
 # When strict, n comes from the SAME statement/snapshot as the checkpoint.
 n=row['cp_count'] if not strict_count else row['n']
 if row['cp_count']!=n:raise RuntimeError('audit head checkpoint missing or inconsistent')
 signature=checkpoint_signature(row['event_id'],n,row['event_hash'],key)
 if not hmac.compare_digest(row['cp_signature'],signature):raise RuntimeError('audit head checkpoint signature invalid')
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
 """Orphan guard run once per completed streaming verification.

 An orphan is a checkpoint whose event no longer exists - that is what a
 deletion-style tamper leaves behind. This used to be a cardinality equality
 (checkpoints == events scanned), which is **not** race-free: writers append
 an (event,checkpoint) pair while the streaming verification is in flight, so
 a perfectly healthy instance raised 'orphan audit checkpoint detected' and
 /readyz flapped to 503 with a false tamper alarm under concurrent load.
 The anti-join is exact, concurrency-safe and still O(checkpoints).
 """
 n=c.execute('SELECT COUNT(*) n FROM audit_checkpoints cp LEFT JOIN audit_events e ON e.event_id=cp.event_id WHERE e.event_id IS NULL').fetchone()['n']
 if n:raise RuntimeError('orphan audit checkpoint detected')
 return count
