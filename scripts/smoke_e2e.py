#!/usr/bin/env python3
import json,os,subprocess,sys,tempfile,time,urllib.request,urllib.error
from pathlib import Path
from recovery import backup,restore
ROOT=Path(__file__).resolve().parents[1];ADMIN='smoke-admin-token-123456789';RA='smoke-reviewer-a-token';RB='smoke-reviewer-b-token'
def call(port,path,method='GET',body=None,token=None):
 h={'Content-Type':'application/json'}
 if token:h['Authorization']='Bearer '+token
 req=urllib.request.Request(f'http://127.0.0.1:{port}{path}',data=json.dumps(body).encode() if body is not None else None,headers=h,method=method)
 with urllib.request.urlopen(req,timeout=5) as r:return r.status,json.loads(r.read()) if 'json' in r.headers.get('Content-Type','') else r.read().decode()
def start(db,port):
 env={**os.environ,'DB_PATH':str(db),'PORT':str(port),'ADMIN_TOKEN':ADMIN,'REVIEWER_TOKENS':f'reviewer-a:{RA},reviewer-b:{RB}','APP_ENV':'test'}
 p=subprocess.Popen([sys.executable,'app/server.py'],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
 for _ in range(50):
  try:
   if call(port,'/ready')[0]==200:return p
  except Exception:time.sleep(.1)
 raise RuntimeError(p.stderr.read().decode())
def stop(p):p.terminate();p.wait(timeout=5)
def main():
 with tempfile.TemporaryDirectory() as d:
  db=Path(d)/'live.db';port=18123;p=start(db,port)
  try:
   pid=call(port,'/api/projects','POST',{'title':'Smoke fixture','authority':'Synthetic Authority','summary':'Restart and restore proof','synthetic':True},ADMIN)[1]['id']
   sid=call(port,f'/api/projects/{pid}/sources','POST',{'publisher':'Synthetic source','url':'https://example.invalid/smoke','source_class':'official','retrieved_at':'2026-07-14T00:00:00Z','sha256':'c'*64,'passage':'Synthetic anchor'},ADMIN)[1]['id']
   cid=call(port,f'/api/projects/{pid}/claims','POST',{'claim_type':'official_claim','text':'Synthetic smoke claim','source_id':sid,'passage':'Synthetic anchor'},ADMIN)[1]['id']
   call(port,f'/api/projects/{pid}/claims/{cid}/reviews','POST',{'decision':'approve'},RA);call(port,f'/api/projects/{pid}/claims/{cid}/reviews','POST',{'decision':'approve'},RB);call(port,f'/api/projects/{pid}/claims/{cid}/publish','POST',{},ADMIN)
   call(port,f'/api/projects/{pid}/gaps','POST',{'document_name':'Synthetic record','search_scope':'Synthetic fixture'},ADMIN);call(port,f'/api/projects/{pid}/responses','POST',{'responder':'Synthetic Authority','text':'Synthetic response','source_id':sid},ADMIN);call(port,f'/api/projects/{pid}/publish','POST',{},ADMIN)
   assert call(port,f'/api/projects/{pid}')[1]['claims'][0]['text']=='Synthetic smoke claim'
   report=call(port,f'/api/projects/{pid}/report')[1]
   assert 'https://example.invalid/smoke' in report and 'c'*64 in report
   rti=call(port,f'/api/projects/{pid}/rti')[1]
   assert 'Synthetic record' in rti and 'Draft RTI' in rti
   capsule=call(port,f'/api/projects/{pid}/capsule')[1]
   capsule_path=Path(d)/'capsule.json';capsule_path.write_text(json.dumps(capsule))
   subprocess.run([sys.executable,'scripts/verify_capsule.py',str(capsule_path)],cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
   audit=call(port,f'/api/projects/{pid}/audit',token=ADMIN)[1]
   assert audit['verification']['events']>0
   for asset in ['/', '/app.js', '/styles.css']:
    assert len(call(port,asset)[1])>100
  finally:stop(p)
  p=start(db,port)
  try:assert call(port,f'/api/projects/{pid}')[0]==200
  finally:stop(p)
  archive=Path(d)/'backup.db';restored=Path(d)/'clean'/'restored.db';backup(db,archive);restore(archive,restored)
  p=start(restored,port)
  try:assert call(port,f'/api/projects/{pid}')[0]==200
  finally:stop(p)
  print(json.dumps({'status':'ok','project_id':pid,'restart':'passed','restore':'passed','report':'passed','rti':'passed','capsule':'passed','audit':'passed','static_assets':'passed'}));return 0
if __name__=='__main__':sys.exit(main())
