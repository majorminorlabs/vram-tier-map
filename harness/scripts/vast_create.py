#!/usr/bin/env python3
"""Invoke Vast create via saved CLI auth; keep output in memory and log only redacted data."""
import json,re,subprocess,sys
from pathlib import Path
run_id,label,offer,image,disk,logdir=sys.argv[1:]
p=subprocess.run(['vastai','create','instance',offer,'--image',image,'--disk',disk,'--label',label,'--ssh','--direct','--cancel-unavail','--raw'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
raw=p.stdout
# Reuse the conservative JSON redactor; it omits opaque responses rather than risking a secret.
red=subprocess.run([sys.executable,str(Path(__file__).with_name('redact_create_response.py'))],input=raw,stdout=subprocess.PIPE,check=True)
Path(logdir,f'{run_id}.json').write_bytes(red.stdout)
err=p.stderr.decode('utf-8','ignore').lower()
status=re.search(r'\b(\d{3})\b',err)
classes=[('unauthorized','Unauthorized'),('forbidden','Forbidden'),('authentication','AuthenticationError'),('authorization','AuthorizationError'),('timeout','TimeoutError'),('connection','ConnectionError'),('error','VastCliError')]
error_class=next((name for needle,name in classes if needle in err),None)
summary={'exit_code':p.returncode,'http_status':status.group(1) if status else None,'error_class':error_class,'stderr_bytes':len(p.stderr)}
Path(logdir,f'{run_id}.stderr-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
sys.stdout.buffer.write(raw)
sys.exit(p.returncode)
