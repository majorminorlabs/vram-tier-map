#!/usr/bin/env python3
"""GPU fit, speed, TTFT, and strict task evaluation for one card run."""
import argparse, csv, hashlib, json, os, re, signal, shutil, subprocess, sys, time, urllib.request
from pathlib import Path
import yaml

ROOT=Path('/workspace/harness')
LLAMA=Path('/workspace/llama.cpp')
OUT=Path('/workspace/output')
CACHE=Path('/workspace/model-cache')
BASE='http://127.0.0.1:8080'
COMMIT='84e66d03218711dfcaa993e5f64a546abe82df9a'

QUALITY={'BF16':100,'F16':99,'Q8_0':95,'Q8_K_XL':94,'Q6_K':90,'Q6_K_XL':89,'Q5_K_M':85,'Q5_K_S':84,'Q4_K_M':80,'Q4_K_S':79,'Q4_K_XL':78,'Q4_0':77,'MXFP4_MOE':76,'IQ4_NL':75,'IQ4_NL_XL':74,'IQ4_XS':73,'Q3_K_M':65,'Q3_K_XL':64,'Q3_K_S':63,'IQ3_S':62,'IQ3_XXS':61,'Q2_K_XL':50,'IQ2_M':49,'IQ2_XXS':48,'IQ1_M':35}

def request(path, payload=None, timeout=600):
    data=None if payload is None else json.dumps(payload).encode()
    req=urllib.request.Request(BASE+path,data=data,headers={'Content-Type':'application/json'} if data else {})
    with urllib.request.urlopen(req,timeout=timeout) as r: return json.loads(r.read())

def start_server(model, ctx, kv, cpu_moe, log):
    cmd=[str(LLAMA/'build/bin/llama-server'),'-m',str(model),'-c',str(ctx),'-ngl','999','--host','127.0.0.1','--port','8080','--no-webui','--cache-type-k',kv,'--cache-type-v',kv]
    if cpu_moe: cmd += ['--n-cpu-moe',str(cpu_moe)]
    f=open(log,'w')
    p=subprocess.Popen(cmd,stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
    p._log_file=f
    for _ in range(360):
        if p.poll() is not None: f.close(); return None
        try:
            request('/health',timeout=3); return p
        except Exception: time.sleep(5)
    os.killpg(p.pid,signal.SIGTERM); f.close(); return None

def stop_server(p):
    if not p: return
    try: os.killpg(p.pid,signal.SIGTERM); p.wait(timeout=15)
    except Exception:
        try: os.killpg(p.pid,signal.SIGKILL)
        except Exception: pass
    finally:
        try: p._log_file.close()
        except Exception: pass

def dump_repo(target_tokens=65536):
    files=[]
    allowed={'.c','.h','.cc','.cpp','.hpp','.md','.py','.cmake','.txt','.sh'}
    for p in sorted(LLAMA.rglob('*')):
        if not p.is_file() or 'build' in p.parts or '.git' in p.parts or p.suffix.lower() not in allowed: continue
        try:
            b=p.read_bytes()
            if len(b)>500000 or b'\0' in b: continue
            files.append(f'\n\n===== {p.relative_to(LLAMA)} =====\n'+b.decode('utf-8','replace'))
        except OSError: pass
    text=''.join(files)
    lo,hi=0,len(text); best=''
    while lo<=hi:
        mid=(lo+hi)//2; candidate=text[:mid]
        try: n=len(request('/tokenize',{'content':candidate,'add_special':False},timeout=120).get('tokens',[]))
        except Exception as e: raise RuntimeError('llama-server tokenizer endpoint failed') from e
        if n<=target_tokens: best=candidate; lo=mid+1
        else: hi=mid-1
    p=Path('/workspace/repo-context-64k.txt'); p.write_text(best,encoding='utf-8')
    count=len(request('/tokenize',{'content':best,'add_special':False},timeout=120).get('tokens',[]))
    return p,count

def ttft(prefix, logdir):
    content=prefix.read_text(encoding='utf-8')+'\n\nRespond with the single word READY.'
    messages=[{'role':'user','content':content}]
    start=time.monotonic(); request('/v1/chat/completions',{'model':'local','messages':messages,'temperature':0,'max_tokens':1},timeout=600)
    return (time.monotonic()-start)*1000

def download(model, f):
    CACHE.mkdir(parents=True,exist_ok=True)
    dest=CACHE/f['file']
    free=shutil.disk_usage(CACHE).free
    required=int(f['size_bytes'])+10*1024**3
    disk_log=OUT/'logs'/'disk-space.log'; disk_log.parent.mkdir(parents=True,exist_ok=True)
    with disk_log.open('a',encoding='utf-8') as log:
        log.write(json.dumps({'event':'before_model_download','model_id':model['model_id'],'file':f['file'],'free_bytes':free,'model_bytes':int(f['size_bytes']),'required_bytes':required})+'\n')
    print(f"disk before download {f['file']}: free={free/1024**3:.2f} GiB required={(required)/1024**3:.2f} GiB",flush=True)
    if free < required:
        raise RuntimeError(f"insufficient disk before downloading {f['file']}: free {free} bytes; need {required} bytes")
    def digest(path):
        h=hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda:stream.read(8*1024*1024),b''): h.update(block)
        return h.hexdigest()
    if not dest.exists() or digest(dest)!=f['sha256']:
        url=f"https://huggingface.co/{model['gguf_repo']}/resolve/{model['gguf_revision']}/{f['file']}"
        subprocess.run(['curl','-fL','--retry','3','--retry-delay','2','-o',str(dest),url],check=True)
    if digest(dest)!=f['sha256']: raise RuntimeError('model SHA-256 mismatch: '+f['file'])
    return dest

def measure_peak():
    try:
        val=subprocess.check_output(['nvidia-smi','--query-gpu=memory.used','--format=csv,noheader,nounits'],text=True).splitlines()[0]
        vram=float(val.strip())/1024
    except Exception: vram=0
    try:
        mem={}
        for line in Path('/proc/meminfo').read_text().splitlines():
            k,v=line.split(':',1); mem[k]=int(v.strip().split()[0])
        ram=(mem['MemTotal']-mem['MemAvailable'])/1048576
    except Exception: ram=0
    return vram,ram

def fit(model, f, run_id):
    path=download(model,f)
    plans=[(131072,'f16',0)] + [(131072,'q8_0',0)]
    if 'MOE' in model['model_id'].upper() or 'A2B' in model['model_id'].upper() or 'A3B' in model['model_id'].upper() or 'A4B' in model['model_id'].upper() or 'A9B' in model['model_id'].upper():
        plans += [(131072,'q8_0',n) for n in (1,2,4,8)]
    plans += [(65536,'q8_0',0)]
    for ctx,kv,ncpu in plans:
        log=OUT/'logs'/f"fit-{model['model_id']}-{f['quant']}-ctx{ctx}-kv{kv}-cpu{ncpu}.log"; log.parent.mkdir(parents=True,exist_ok=True)
        p=start_server(path,ctx,kv,ncpu,log)
        if p:
            peak=measure_peak(); stop_server(p)
            server_flags=['-m',str(path),'-c',str(ctx),'-ngl','999','--cache-type-k',kv,'--cache-type-v',kv]
            if ncpu: server_flags += ['--n-cpu-moe',str(ncpu)]
            config={'model_id':model['model_id'],'quant':f['quant'],'file':f['file'],'sha256':f['sha256'],'ctx':ctx,'kv':kv,'n_cpu_moe':ncpu,'n_gpu_layers':999,'server_flags':server_flags,'bench_flags':['-m',str(path),'-ngl','999','-ctk',kv,'-ctv',kv,'-p','512','-n','128','-d','65536|122880','-r','5'],'path':str(path)}
            cdir=OUT/'configs'; cdir.mkdir(parents=True,exist_ok=True)
            (cdir/f"{model['model_id']}-{f['quant']}.json").write_text(json.dumps(config,indent=2)+'\n')
            return config,peak
    path.unlink(missing_ok=True)
    return None,(0,0)

def run_model(model, args):
    telemetry_path=OUT/'hosts'/args.host_id/'telemetry.csv'
    try:
        import csv
        with telemetry_path.open(newline='') as f: telemetry_start=sum(1 for _ in csv.DictReader(f))
    except Exception: telemetry_start=0
    files=sorted(model['files'],key=lambda f:QUALITY.get(f['quant'],0),reverse=True)
    picked=None; peak=(0,0)
    for f in files:
        picked,peak=fit(model,f,args.run_id)
        if picked: break
    if not picked:
        return {'model_id':model['model_id'],'fit':'failed','flags':['does_not_fit']}
    path=Path(picked['path']); logdir=OUT/'logs'; logdir.mkdir(parents=True,exist_ok=True)
    config_path=OUT/'configs'/f"{model['model_id']}-{picked['quant']}.json"
    bench_dir=OUT/'bench'/model['model_id']/picked['quant']; bench_dir.mkdir(parents=True,exist_ok=True)
    env=os.environ.copy(); env['PATH']=str(LLAMA/'build/bin')+':'+env.get('PATH','')
    env['BENCH_DEPTHS']='65536 122880' if picked['ctx']>=131072 else '65536'
    bench_extra=['-ngl','999','-ctk',picked['kv'],'-ctv',picked['kv']]
    if picked['n_cpu_moe']: bench_extra += ['--n-cpu-moe',str(picked['n_cpu_moe'])]
    subprocess.run([str(ROOT/'scripts/bench.sh'),str(path),str(bench_dir),'5',*bench_extra],check=True,env=env)
    p=start_server(path,picked['ctx'],picked['kv'],picked['n_cpu_moe'],logdir/f"serve-{model['model_id']}.log")
    if not p: return {'model_id':model['model_id'],'quant':picked['quant'],'fit':'pass','benchmark':'failed','flags':['server_restart_failed']}
    try:
        target_tokens=min(65536,max(56000,picked['ctx']-4096))
        task_prefix,context_tokens=dump_repo(target_tokens)
        (OUT/'repo-context-metadata.json').write_text(json.dumps({'repository':'https://github.com/ggml-org/llama.cpp','commit':COMMIT,'tokens':context_tokens,'sha256':hashlib.sha256(task_prefix.read_bytes()).hexdigest()},indent=2)+'\n')
        ttft_ms=ttft(task_prefix,logdir)
        task_result=OUT/'tasks'/f"{model['model_id']}-{picked['quant']}.json"; task_result.parent.mkdir(parents=True,exist_ok=True)
        subprocess.run(['python3',str(ROOT/'scripts/tasks_runner.py'),'--base-url',BASE+'/v1','--model','local','--prefix-file',str(task_prefix),'--output',str(task_result)],check=True)
        tasks=json.loads(task_result.read_text())
    finally: stop_server(p)
    def bench_rates(depth):
        try:
            files=sorted(bench_dir.glob(f'bench-depth-{depth}-attempt*.json'))
            x=json.loads(files[-1].read_text()); r=x if isinstance(x,list) else x.get('results',[])
            pp=next((a for a in r if int(a.get('n_prompt',0))>0),r[0] if r else {})
            tg=next((a for a in r if int(a.get('n_gen',0))>0),r[-1] if r else {})
            return float(pp.get('avg_ts',pp.get('tokens_per_second',0))),float(tg.get('avg_ts',tg.get('tokens_per_second',0)))
        except Exception: return 0,0
    p64,d64=bench_rates(65536)
    p120,d120=bench_rates(122880) if picked['ctx']>=131072 else (0,0)
    cv=[]
    for depth in (65536,122880):
        cp=bench_dir/f'bench-depth-{depth}-cv-percent.txt'
        if cp.exists(): cv.append(float(cp.read_text().strip()))
    # Use run-wide telemetry from this model's fit/speed/task window.
    telemetry=telemetry_path
    vram,ram=peak
    if telemetry.exists():
        import csv
        with telemetry.open(newline='') as f:
            for index,row in enumerate(csv.DictReader(f)):
                if index < telemetry_start: continue
                try: vram=max(vram,float(row['memory_used_mib'])/1024)
                except Exception: pass
                try: ram=max(ram,float(row['host_ram_used_gib']))
                except Exception: pass
    result={'model_id':model['model_id'],'quant':picked['quant'],'fit':'pass','fit_max_ctx':picked['ctx'],'kv_type':picked['kv'],'n_cpu_moe':picked['n_cpu_moe'],'peak_vram_gb':round(vram,3),'peak_ram_gb':round(ram,3),'prompt_tps_64k':p64,'decode_tps_64k':d64,'decode_tps_120k':d120,'ttft_64k_ms':round(ttft_ms,2),'strict_pass_rate':tasks['strict_pass_rate'],'task_wall_clock_s':tasks['wall_clock_s'],'speed_cv_percent':max(cv) if cv else 0,'flags':['q4_kv'] if picked['kv']=='q4_0' else []}
    (OUT/'configs'/f"{model['model_id']}-{picked['quant']}.json").write_text(json.dumps({**picked,'ttft_64k_ms':result['ttft_64k_ms']},indent=2)+'\n')
    del path
    return result

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--card-id',required=True); ap.add_argument('--run-id',required=True); ap.add_argument('--host-id',required=True); ap.add_argument('--mode',default='full')
    args=ap.parse_args(); OUT.mkdir(parents=True,exist_ok=True)
    for folder in ('configs','hosts','logs','tasks','bench'): (OUT/folder).mkdir(parents=True,exist_ok=True)
    (OUT/'hosts'/args.host_id).mkdir(parents=True,exist_ok=True)
    registry=yaml.safe_load((ROOT/'registry/models.yaml').read_text())
    subprocess.run(['git','-C',str(LLAMA),'rev-parse','HEAD'],check=True,stdout=open(OUT/'llama-commit.txt','w'))
    rows=[]
    for model in registry['models']:
        row=run_model(model,args); rows.append(row)
        preflight=list(csv.reader(open(OUT/'hosts'/args.host_id/'gpu-preflight.csv',newline='')))
        pcie_gen=int(preflight[0][4].strip())
        pcie_width=int(preflight[0][6].strip())
        flags=list(row.get('flags',[]))
        if pcie_gen == 3 and args.card_id in ('rtx-3090-24gb','rtx-4060-ti-16gb','rtx-3060-12gb','rtx-3070-8gb'): flags.append('pcie_gen3_on_gen4_card')
        result={'card_id':args.card_id,'run_id':args.run_id,'model_id':row['model_id'],'quant':row.get('quant','not-fit'),'task_set_version':'cuda-recipes-001-v1','fit_max_ctx':row.get('fit_max_ctx',''),'kv_type':row.get('kv_type','none'),'n_cpu_moe':row.get('n_cpu_moe',''),'peak_vram_gb':row.get('peak_vram_gb',''),'peak_ram_gb':row.get('peak_ram_gb',''),'prompt_tps_64k':row.get('prompt_tps_64k',''),'decode_tps_64k':row.get('decode_tps_64k',''),'decode_tps_120k':row.get('decode_tps_120k',''),'ttft_64k_ms':row.get('ttft_64k_ms',''),'strict_pass_rate':row.get('strict_pass_rate',''),'task_wall_clock_s':row.get('task_wall_clock_s',''),'speed_cv_percent':row.get('speed_cv_percent',''),'cost_usd':'','pcie_gen_actual':pcie_gen,'pcie_width_actual':pcie_width,'flags':';'.join(flags) or ('fit_failed' if row.get('fit')=='failed' else 'none')}
        p=OUT/'results.csv'
        import csv
        fields=['card_id','run_id','model_id','quant','task_set_version','fit_max_ctx','kv_type','n_cpu_moe','peak_vram_gb','peak_ram_gb','prompt_tps_64k','decode_tps_64k','decode_tps_120k','ttft_64k_ms','strict_pass_rate','task_wall_clock_s','speed_cv_percent','cost_usd','pcie_gen_actual','pcie_width_actual','flags']
        with p.open('a',newline='') as f:
            w=csv.DictWriter(f,fieldnames=fields)
            if p.stat().st_size==0:w.writeheader()
            w.writerow(result)
        # The instance has a limited scratch disk; weights are fetched and removed one model at a time.
        for f in CACHE.glob('*'): f.unlink()
    (OUT/'card-summary.json').write_text(json.dumps({'card_id':args.card_id,'run_id':args.run_id,'host_id':args.host_id,'llama_cpp_commit':COMMIT,'repo_dump':{'repository':'https://github.com/ggml-org/llama.cpp','commit':COMMIT},'results':rows},indent=2)+'\n')
if __name__=='__main__': main()
