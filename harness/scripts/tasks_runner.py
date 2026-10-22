#!/usr/bin/env python3
"""Run and strictly score the vendored CUDA Recipes task set against llama-server."""
import argparse, hashlib, json, sys, time
from pathlib import Path
from urllib.request import Request, urlopen

REPO = Path(__file__).resolve().parents[2]
TASKS = REPO / 'harness/tasks/cuda-recipes-001-tasks.json'
sys.path.insert(0, str(REPO / 'harness/tasks'))
from scorer.tools import Simulator
from scorer.validate import score

def json_type(value):
    if isinstance(value, bool): return 'boolean'
    if isinstance(value, int): return 'integer'
    if isinstance(value, float): return 'number'
    if isinstance(value, str): return 'string'
    if isinstance(value, list): return 'array'
    if isinstance(value, dict): return 'object'
    return 'string'

def make_tools(task):
    calls = task.get('expected_calls') or []
    grouped = {}
    for call in calls:
        name = call['tool']
        props = grouped.setdefault(name, {})
        for key,value in call.items():
            if key != 'tool': props[key] = {'type': json_type(value)}
    return [{'type':'function','function':{'name':name,'description':'Perform one task-specified simulated action.','parameters':{'type':'object','properties':props,'required':list(props),'additionalProperties':False}}} for name,props in grouped.items()]

def chat(base, model, messages, tools):
    payload={'model':model,'messages':messages,'temperature':0,'max_tokens':2048}
    if tools: payload['tools']=tools; payload['tool_choice']='auto'
    req=Request(base.rstrip('/')+'/chat/completions', data=json.dumps(payload).encode(), headers={'Content-Type':'application/json'})
    with urlopen(req, timeout=600) as response: return json.loads(response.read())['choices'][0]['message']

def run_task(task, base, model, prefix):
    start=time.monotonic(); messages=[]
    if prefix: messages.append({'role':'system','content':'Repository context for this benchmark task follows. Use it as context; solve the user task after reading it.\n\n'+prefix})
    messages.append({'role':'user','content':task['prompt']})
    tools=make_tools(task); sim=Simulator(task['id']); trace=[]; final=''
    for _ in range(9):
        message=chat(base,model,messages,tools)
        calls=message.get('tool_calls') or []
        if not calls:
            final=message.get('content') or ''
            messages.append(message)
            break
        messages.append(message)
        for call in calls:
            try:
                name=call['function']['name']; args=json.loads(call['function'].get('arguments') or '{}')
                action={'tool':name,**args}
                result,ok=sim.step(action)
            except Exception as exc:
                action={'tool':'<malformed>'}; result={'error':type(exc).__name__}; ok=False
            trace.append({'action':action,'result':result,'ok':bool(ok)})
            messages.append({'role':'tool','tool_call_id':call.get('id',''),'content':json.dumps(result,ensure_ascii=False)})
    passed=bool(score(task,final,trace))
    return {'task_id':task['id'],'passed':passed,'response':final,'tool_trace':trace,'wall_clock_s':round(time.monotonic()-start,3)}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--base-url',default='http://127.0.0.1:8080/v1')
    ap.add_argument('--model',default='local')
    ap.add_argument('--prefix-file',required=True)
    ap.add_argument('--output',required=True)
    ap.add_argument('--tasks',default=str(TASKS))
    args=ap.parse_args()
    prefix_path=Path(args.prefix_file); prefix=prefix_path.read_text(encoding='utf-8')
    tasks=json.loads(Path(args.tasks).read_text(encoding='utf-8'))
    rows=[]; start=time.monotonic()
    for task in tasks:
        rows.append(run_task(task,args.base_url,args.model,prefix))
        print(f"{task['id']}: {'PASS' if rows[-1]['passed'] else 'FAIL'}",flush=True)
    out={'task_set':'cuda-recipes-001-v1','prefix_sha256':hashlib.sha256(prefix_path.read_bytes()).hexdigest(),'prefix_bytes':prefix_path.stat().st_size,'task_count':len(rows),'pass_count':sum(x['passed'] for x in rows),'strict_pass_rate':sum(x['passed'] for x in rows)/len(rows) if rows else 0,'wall_clock_s':round(time.monotonic()-start,3),'tasks':rows}
    Path(args.output).write_text(json.dumps(out,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(f"strict_pass_rate={out['strict_pass_rate']:.4f} wall_clock_s={out['wall_clock_s']}")
if __name__=='__main__': main()
