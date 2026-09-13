#!/usr/bin/env python3
import hashlib,json,platform,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
files=[]
for raw in subprocess.run(['git','ls-files','-z'],cwd=ROOT,capture_output=True,check=True).stdout.split(b'\0'):
 if not raw:continue
 p=ROOT/raw.decode();files.append({'path':raw.decode(),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
out={'bomFormat':'CycloneDX','specVersion':'1.5','version':1,'metadata':{'component':{'type':'application','name':'project-xray-india','version':'0.4.6'},'properties':[{'name':'runtime.python','value':platform.python_version()},{'name':'dependencies.third_party_runtime','value':'psycopg2-binary (optional, PostgreSQL mode)'}]},'components':[],'properties':[{'name':'project.files','value':json.dumps(files,separators=(',',':'))}]}
path=ROOT/'artifacts'/'sbom.cdx.json';path.parent.mkdir(exist_ok=True);path.write_text(json.dumps(out,indent=2,sort_keys=True)+'\n');print(path)
