#!/usr/bin/env python3
"""No-rental tests for create diagnostics, retry, and disk filtering."""
import json, os, subprocess, sys, tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
SCRIPTS=ROOT/'harness'/'scripts'

MOCK=r'''#!/usr/bin/env python3
import json, os, pathlib, sys
args=sys.argv[1:]
log=pathlib.Path(os.environ['MOCK_LOG'])
with log.open('a') as f: f.write(json.dumps(args)+'\n')
if args[:3]==['search','offers','--raw']:
    offers=[
      {'id':101,'machine_id':1,'host_id':1,'gpu_name':'RTX 3090','num_gpus':1,'cpu_ram':64000,'reliability':.99,'verified':True,'rentable':True,'dph_total':.1,'pci_gen':4,'pcie_bw':31.5,'disk_space':150},
      {'id':102,'machine_id':2,'host_id':2,'gpu_name':'RTX 3090','num_gpus':1,'cpu_ram':64000,'reliability':.99,'verified':True,'rentable':True,'dph_total':.11,'pci_gen':4,'pcie_bw':31.5,'disk_space':150}]
    print(json.dumps(offers))
elif args[:3]==['create','instance','101']:
    label=args[args.index('--label')+1]
    pathlib.Path(os.environ['MOCK_INSTANCES']).write_text(json.dumps([{'id':900,'label':label}]))
    print(json.dumps({'success':False,'error':'HTTP 400 insufficient disk'})); print('HTTP 400: insufficient disk',file=sys.stderr)
elif args[:3]==['create','instance','102']:
    print(json.dumps({'success':True,'new_contract':777}))
elif args==['show','instances','--raw']:
    print(pathlib.Path(os.environ['MOCK_INSTANCES']).read_text())
elif args[:2]==['destroy','instance']:
    current=json.loads(pathlib.Path(os.environ['MOCK_INSTANCES']).read_text())
    pathlib.Path(os.environ['MOCK_INSTANCES']).write_text(json.dumps([x for x in current if str(x['id'])!=args[2]]))
    print('{}')
else:
    print('unexpected mock command '+repr(args),file=sys.stderr); sys.exit(2)
'''

def setup_mock(tmp):
    bindir=tmp/'bin'; bindir.mkdir(); binary=bindir/'vastai'; binary.write_text(MOCK); binary.chmod(0o755)
    log=tmp/'argv.jsonl'; env=os.environ.copy(); env['PATH']=f"{bindir}:{env['PATH']}"; env['MOCK_LOG']=str(log)
    state=tmp/'instances.json'; state.write_text('[]'); env['MOCK_INSTANCES']=str(state)
    return env,log

def test_retry_400_next_offer():
    with tempfile.TemporaryDirectory() as td:
        tmp=Path(td); env,log=setup_mock(tmp)
        registry=tmp/'cards.yaml'; registry.write_text('cards:\n- gpu_name: RTX 3090\n  disk_gb: 150\n  native_pcie_gen: 4\n  native_pcie_width: 16\n')
        private=tmp/'private'; private.mkdir(); failed=tmp/'failed.txt'; failed.write_text('')
        p=subprocess.run([sys.executable,str(SCRIPTS/'create_retry.py'),'test-run','vtm-test','image@sha256:abc','0.15','any','smoke',str(registry),str(private),str(failed)],capture_output=True,text=True,env=env,timeout=30)
        assert p.returncode==0, p.stderr
        result=json.loads(p.stdout); assert result['offer']['id']==102 and result['instance_id']=='777'
        calls=[json.loads(x) for x in log.read_text().splitlines()]
        searches=[x[-1] for x in calls if x[:3]==['search','offers','--raw']]
        assert searches and all('disk_space>=150' in q for q in searches), searches
        tried=[x[2] for x in calls if x[:2]==['create','instance']]
        assert tried==['101','102'], tried
        destroys=[x for x in calls if x[:2]==['destroy','instance']]
        assert destroys==[['destroy','instance','900','--yes','--raw']], destroys
        assert json.loads(Path(env['MOCK_INSTANCES']).read_text())==[]
        err=(private/'create-responses'/'test-run-attempt-01.stderr.txt').read_text()
        assert 'HTTP 400: insufficient disk' in err
        summary=json.loads((private/'create-responses'/'test-run.stderr-summary.json').read_text())
        assert summary['attempts'][0]['http_status']==400
        print('create_400_retry_next_offer: passed; offer 102 selected, orphan cleaned before next offer')

def test_empty_stdout_is_create_failure():
    with tempfile.TemporaryDirectory() as td:
        tmp=Path(td); bindir=tmp/'bin'; bindir.mkdir(); binary=bindir/'vastai'
        binary.write_text('#!/usr/bin/env python3\nimport sys\nprint("HTTP 400: rejected",file=sys.stderr)\n')
        binary.chmod(0o755); env=os.environ.copy(); env['PATH']=f"{bindir}:{env['PATH']}"
        p=subprocess.run([sys.executable,str(SCRIPTS/'vast_create.py'),'empty-run','vtm-empty','123','image','100',str(tmp),'1'],capture_output=True,text=True,env=env)
        assert p.returncode!=0
        summary=json.loads((tmp/'empty-run.stderr-summary.json').read_text())
        assert summary['attempts'][0]['stdout_json_success'] is False
        assert summary['attempts'][0]['http_status']==400
        print('empty_stdout_create_failure: passed')

def test_disk_filter():
    sys.path.insert(0,str(SCRIPTS))
    from offer_filter import filter_offers
    card=[{'gpu_name':'RTX 3090','disk_gb':150,'native_pcie_gen':4,'native_pcie_width':16}]
    low={'id':1,'machine_id':1,'gpu_name':'RTX 3090','num_gpus':1,'cpu_ram':64000,'reliability':.99,'verified':True,'rentable':True,'dph_total':.1,'pci_gen':4,'pcie_bw':31.5,'disk_space':149}
    ok={**low,'id':2,'disk_space':150}
    assert filter_offers([low],.15,'any',card) is None
    assert filter_offers([low,ok],.15,'any',card)['id']==2
    print('offer_disk_filter: passed')

def test_smoke_accepts_supported_extra_gpu_profile():
    sys.path.insert(0,str(SCRIPTS))
    from offer_filter import filter_offers
    offer={'id':3,'machine_id':3,'gpu_name':'RTX A2000','num_gpus':1,'cpu_ram':64000,'reliability':.99,'verified':True,'rentable':True,'dph_total':.11,'pci_gen':4,'pcie_bw':23.9,'disk_space':690}
    quadro={'id':4,'machine_id':4,'gpu_name':'Q RTX 4000','num_gpus':1,'cpu_ram':64000,'reliability':.99,'verified':True,'rentable':True,'dph_total':.108,'pci_gen':3,'pcie_bw':10.9,'disk_space':256}
    assert filter_offers([offer],.15,'any',[],smoke=False) is None
    selected=filter_offers([offer,quadro],.15,'any',[],smoke=True)
    assert selected and selected['id']==4 and selected['_vtm_native_pcie_width']==16 and selected['_vtm_expected_gpu_name']=='RTX 4000'
    print('smoke_any_gpu_profile: passed')

if __name__=='__main__':
    test_retry_400_next_offer(); test_empty_stdout_is_create_failure(); test_disk_filter(); test_smoke_accepts_supported_extra_gpu_profile()
