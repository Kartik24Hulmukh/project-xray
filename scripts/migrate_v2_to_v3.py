#!/usr/bin/env python3
"""SQLite-only migrator (schema v2 -> v3).

PostgreSQL production deployments must use operator-managed migrations against
db/schema_postgres.sql. This script only rewrites local SQLite database files.
"""
import argparse,json,os,sqlite3,sys
from contextlib import closing
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));SCHEMA=(ROOT/'db/schema.sql').read_text()
from app.manifest import create as create_manifest
from app.audit import event_hash,checkpoint_signature
if os.getenv('DATABASE_URL'):
    raise SystemExit(json.dumps({'status':'error','error':'migrate_v2_to_v3.py is SQLite-only; unset DATABASE_URL and pass a .db path'}))

def migrate(path,backup_key=None,audit_key=None):
 path=Path(path);backup_key=backup_key or os.getenv('BACKUP_HMAC_KEY','development-backup-key-not-for-production');audit_key=audit_key or os.getenv('AUDIT_HMAC_KEY','development-audit-key-not-for-production');backup=path.with_suffix(path.suffix+'.pre-v3.bak');manifest=backup.with_suffix(backup.suffix+'.manifest.json');tmp=path.with_suffix(path.suffix+'.v3.tmp')
 if not path.is_file():raise FileNotFoundError(path)
 src=sqlite3.connect(path);version=src.execute('PRAGMA user_version').fetchone()[0]
 if version==3:src.close();return {'status':'already_v3','database':str(path)}
 if version!=2:src.close();raise RuntimeError(f'expected schema v2, found v{version}')
 if any(x.exists() for x in (backup,manifest,tmp)):src.close();raise FileExistsError('migration artifacts already exist')
 with closing(sqlite3.connect(backup)) as dst:src.backup(dst)
 src.close();os.chmod(backup,0o600);create_manifest(manifest,backup,backup_key,{'schema_version':2,'purpose':'pre-v3-migration'})
 with closing(sqlite3.connect(path)) as old, closing(sqlite3.connect(tmp)) as new:old.backup(new)
 c=sqlite3.connect(tmp);c.row_factory=sqlite3.Row
 try:
  c.execute('PRAGMA foreign_keys=OFF')
  for table in ('documents','claims','claim_reviews','claim_revisions'):c.execute(f'ALTER TABLE {table} RENAME TO {table}_v2')
  c.executescript(SCHEMA)
  c.execute("INSERT INTO documents(id,project_id,source_id,filename,media_type,size_bytes,sha256,storage_state,scan_result,created_at) SELECT id,project_id,source_id,filename,media_type,size_bytes,sha256,storage_state,scan_result,created_at FROM documents_v2")
  c.execute("INSERT INTO claims(id,project_id,source_id,claim_type,publication_state,text,passage,page_ref,created_by,version,created_at,updated_at) SELECT id,project_id,source_id,claim_type,'candidate',text,passage,page_ref,created_by,1,created_at,updated_at FROM claims_v2")
  c.execute("INSERT INTO claim_reviews(id,claim_id,claim_version,reviewer,decision,note,created_at) SELECT id,claim_id,1,reviewer,decision,note,created_at FROM claim_reviews_v2")
  for r in c.execute('SELECT * FROM claim_revisions_v2 ORDER BY claim_id,created_at').fetchall():
   current=c.execute('SELECT version FROM claims WHERE id=?',(r['claim_id'],)).fetchone()['version'];new_version=current+1;c.execute('INSERT INTO claim_revisions VALUES(?,?,?,?,?,?,?,?,?)',(r['id'],r['claim_id'],current,new_version,r['previous_text'],r['new_text'],r['reason'],r['actor'],r['created_at']));c.execute('UPDATE claims SET version=? WHERE id=?',(new_version,r['claim_id']))
  c.execute("UPDATE projects SET status='research' WHERE status='published'")
  for table in ('documents_v2','claims_v2','claim_reviews_v2','claim_revisions_v2'):c.execute(f'DROP TABLE {table}')
  previous='';count=0
  for r in c.execute('SELECT * FROM audit_events ORDER BY id').fetchall():
   count+=1;expected=event_hash(previous,r['event_id'],r['actor'],r['action'],r['object_type'],r['object_id'],r['detail'],r['created_at'])
   if r['previous_hash']!=previous or r['event_hash']!=expected:raise RuntimeError(f'legacy audit chain broken at {r["id"]}')
   c.execute('INSERT INTO audit_checkpoints(event_id,event_count,head_hash,signature,created_at) VALUES(?,?,?,?,?)',(r['event_id'],count,expected,checkpoint_signature(r['event_id'],count,expected,audit_key),r['created_at']));previous=expected
  c.execute('PRAGMA user_version=3');c.commit()
  if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('post-migration integrity check failed')
 finally:c.close()
 os.chmod(tmp,0o600);tmp.replace(path);return {'status':'migrated','database':str(path),'backup':str(backup),'manifest':str(manifest),'claims_require_fresh_review':True}
def main():
 p=argparse.ArgumentParser();p.add_argument('database');a=p.parse_args()
 try:print(json.dumps(migrate(a.database),sort_keys=True));return 0
 except Exception as e:print(json.dumps({'status':'error','error':str(e)}));return 1
if __name__=='__main__':sys.exit(main())
