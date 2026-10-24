#!/usr/bin/env python3
"""RunPod control plane adapter. Authentication is read only by runpodctl's saved config."""
import json, os, re, subprocess, sys, time
from pathlib import Path

CLI=os.environ.get('VTM_RUNPODCTL','runpodctl')

def redact(value):
    return re.sub(r'(?<![A-Za-z0-9])[A-Za-z0-9_./+=-]{32,}(?![A-Za-z0-9])','[redacted]',str(value or ''))

def call(args, timeout=180):
    p=subprocess.run([CLI,*args],capture_output=True,text=True,timeout=timeout)
    if p.returncode:
        # The private diagnostic is redacted; raw control-plane output is never printed.
        path=os.environ.get('VTM_RUNPOD_DIAGNOSTICS')
        if path:
            Path(path).parent.mkdir(parents=True,exist_ok=True)
            with open(path,'a') as stream: stream.write(json.dumps({'command':args[0],'exit_code':p.returncode,'stdout':redact(p.stdout),'stderr':redact(p.stderr)})+'\n')
        raise RuntimeError(f"runpodctl {args[0]} failed (exit {p.returncode})" + (f"; see {path}" if path else ''))
    return p.stdout

def parse(raw):
    try: return json.loads(raw)
    except Exception as e: raise RuntimeError('RunPod CLI returned malformed JSON') from e

def rows(raw):
    data=parse(raw)
    if isinstance(data,list): return data
    if isinstance(data,dict):
        for key in ('pods','data','items'):
            if isinstance(data.get(key),list): return data[key]
        if any(k in data for k in ('id','podId')): return [data]
    raise RuntimeError('RunPod CLI response has no pod list')

def pod_id(row):
    for key in ('id','podId','pod_id'):
        if row.get(key) is not None: return str(row[key])
    return ''

def pod_name(row): return str(row.get('name') or row.get('podName') or '')

def active(row):
    state=str(row.get('runtimeStatus') or row.get('desiredStatus') or row.get('status') or '').lower()
    return state not in ('terminated','exited','stopped','deleted')

def list_pods(all_pods=False):
    args=['pod','list','--output','json']
    if all_pods: args.append('--all')
    return rows(call(args))

def find_label(label): return [p for p in list_pods(True) if pod_name(p)==label and active(p)]

def delete_pod(pid, poll_seconds=180):
    call(['pod','delete',pid,'--output','json'])
    end=time.monotonic()+poll_seconds
    while time.monotonic()<end:
        if not any(pod_id(p)==str(pid) and active(p) for p in list_pods(True)): return True
        time.sleep(5)
    raise RuntimeError(f'pod {pid} remained after delete retries')

def destroy_label(label, poll_seconds=180):
    end=time.monotonic()+poll_seconds
    while time.monotonic()<end:
        matches=find_label(label)
        if not matches: return True
        for pod in matches:
            pid=pod_id(pod)
            if pid: call(['pod','delete',pid,'--output','json'])
        time.sleep(5)
    raise RuntimeError(f'pods named {label} remain after teardown')

def create(label, gpu_name, cap, disk_gb, cloud='COMMUNITY'):
    gpu_rows=parse(call(['gpu','list','--output','json']))
    if not isinstance(gpu_rows,list): raise RuntimeError('RunPod GPU listing malformed')
    gpu=next((g for g in gpu_rows if gpu_name.casefold() in str(g.get('displayName','')).casefold()),None)
    if not gpu: raise RuntimeError(f'RunPod has no listing for {gpu_name}')
    price_field='communityPricePerHr' if cloud=='COMMUNITY' else 'securePricePerHr'
    hourly=float(gpu.get(price_field) or 0)
    if hourly<=0 or hourly>float(cap): raise RuntimeError(f'RunPod {cloud.lower()} hourly price is unavailable or exceeds ${cap}/h cap')
    cmd=['pod','create','--output','json','--name',label,'--gpu-id',str(gpu['gpuId']),'--gpu-count','1','--cloud-type',cloud,'--image',os.environ['VTM_PINNED_IMAGE'],'--container-disk-in-gb','50','--volume-in-gb',str(disk_gb),'--volume-mount-path','/workspace/storage','--ports','22/tcp']
    if cloud=='COMMUNITY': cmd.append('--public-ip')
    raw=call(cmd,timeout=300)
    response=parse(raw)
    candidates=[]
    if isinstance(response,dict):
        candidates.append(response)
        candidates.extend(v for v in response.values() if isinstance(v,dict))
    pid=next((pod_id(x) for x in candidates if pod_id(x)), '')
    # Create can succeed even when output cannot be parsed; adopt by unique name.
    if not pid:
        matches=find_label(label)
        pid=pod_id(matches[0]) if matches else ''
    if not pid: raise RuntimeError('RunPod create returned no pod ID and label lookup found no pod')
    return {'pod_id':pid,'name':label,'gpu_name':gpu.get('displayName',gpu_name),'pod_type':cloud.lower(),'hourly_rate_usd':hourly}

def quote(gpu_name, cap, cloud='COMMUNITY'):
    gpu_rows=parse(call(['gpu','list','--output','json']))
    if not isinstance(gpu_rows,list): raise RuntimeError('RunPod GPU listing malformed')
    gpu=next((g for g in gpu_rows if gpu_name.casefold() in str(g.get('displayName','')).casefold()),None)
    if not gpu: raise RuntimeError(f'RunPod has no listing for {gpu_name}')
    field='communityPricePerHr' if cloud=='COMMUNITY' else 'securePricePerHr'
    hourly=float(gpu.get(field) or 0)
    if hourly<=0 or hourly>float(cap): raise RuntimeError(f'RunPod {cloud.lower()} hourly price is unavailable or exceeds ${cap}/h cap')
    return {'gpu_name':gpu.get('displayName',gpu_name),'pod_type':cloud.lower(),'hourly_rate_usd':hourly}

def ssh_info(pid):
    data=parse(call(['ssh','info',pid,'--output','json']))
    def walk(x):
        if isinstance(x,dict):
            host=next((x[k] for k in ('ip','publicIp','host','hostname') if x.get(k)),None)
            port=next((x[k] for k in ('port','publicPort','sshPort') if x.get(k)),None)
            if host and port: return str(host),int(port)
            command=x.get('sshCommand') or x.get('ssh_command') or x.get('command')
            if command:
                import re
                m=re.search(r'-p\s+(\d+).*?@([^\s]+)',str(command))
                if m:return str(m.group(2)),int(m.group(1))
                m=re.search(r'@([^\s]+).*?-p\s+(\d+)',str(command))
                if m:return str(m.group(1)),int(m.group(2))
            for v in x.values():
                found=walk(v)
                if found:return found
        if isinstance(x,list):
            for v in x:
                found=walk(v)
                if found:return found
        return None
    found=walk(data)
    if not found: raise RuntimeError('RunPod SSH info did not include host and port')
    return found

def zero_active_pods(): return not [p for p in list_pods(True) if active(p)]

def process_alive(pid):
    try: os.kill(int(pid),0); return True
    except ProcessLookupError: return False
    except PermissionError: return True

def main():
    op=sys.argv[1]
    if op=='create':
        _,_,label,gpu,cap,disk,cloud=sys.argv
        print(json.dumps(create(label,gpu,cap,disk,cloud)))
    elif op=='quote':
        _,_,gpu,cap,cloud=sys.argv
        print(json.dumps(quote(gpu,cap,cloud)))
    elif op=='list-label': print(json.dumps(find_label(sys.argv[2])))
    elif op=='delete': print(json.dumps({'deleted':delete_pod(sys.argv[2])}))
    elif op=='destroy-label': print(json.dumps({'deleted':destroy_label(sys.argv[2])}))
    elif op=='ssh-info':
        host,port=ssh_info(sys.argv[2]); print(json.dumps({'host':host,'port':port}))
    elif op=='zero': print('true' if zero_active_pods() else 'false')
    elif op=='watch':
        state=Path(sys.argv[2]); owner=int(sys.argv[3]); deadline=int(sys.argv[4]); state.mkdir(parents=True,exist_ok=True)
        pidfile=state/'pod-id.txt'; labelfile=state/'label.txt'
        while True:
            label=labelfile.read_text().strip() if labelfile.exists() else ''
            matches=find_label(label) if label else []
            if not matches and (not process_alive(owner) or (state/'main.done').exists()):
                break
            if time.time()>=deadline or not process_alive(owner) or (state/'main.done').exists():
                if pidfile.exists(): delete_pod(pidfile.read_text().strip())
                elif label: destroy_label(label)
                break
            time.sleep(5)
    else: raise SystemExit('unknown backend operation')

if __name__=='__main__':
    try: main()
    except Exception as exc:
        print(str(exc),file=sys.stderr); raise SystemExit(1)
