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
    print(json.dumps({'success':False,'error':'HTTP 400 no_such_ask'})); print('HTTP 400: error 404/3603: no_such_ask Instance type is not available',file=sys.stderr)
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
        assert 'HTTP 400: error 404/3603: no_such_ask' in err
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
    at_reliability_floor={**ok,'id':5,'reliability':.97,'pcie_bw':8}
    assert filter_offers([at_reliability_floor],.15,'any',card)['id']==5
    unverified={**ok,'id':6,'verified':False,'cpu_ram':32000}
    assert filter_offers([unverified],.15,'any',[{**card[0],'ram_floor_gb':32}])['id']==6
    below_ram_floor={**unverified,'id':7,'cpu_ram':31999}
    assert filter_offers([below_ram_floor],.15,'any',[{**card[0],'ram_floor_gb':32}]) is None
    unreliable={**ok,'id':3,'reliability':.969}
    bw_below_x16={**ok,'id':4,'pcie_bw':7.99}
    assert filter_offers([unreliable],.15,'any',card) is None
    assert filter_offers([bw_below_x16],.15,'any',card) is None
    x8_card=[{'gpu_name':'RTX 4060 Ti','disk_gb':100,'native_pcie_gen':4,'native_pcie_width':8}]
    x8_offer={**ok,'gpu_name':'RTX 4060 Ti','disk_space':100,'pcie_bw':6}
    assert filter_offers([x8_offer],.15,'any',x8_card)['id']==2
    # Per-card floors/caps are data, and an unverified host remains eligible.
    ram16_card=[{'gpu_name':'RTX 4060 Ti','disk_gb':100,'native_pcie_gen':4,'native_pcie_width':8,'ram_floor_gb':16}]
    ram16_offer={**x8_offer,'cpu_ram':16000,'verified':False}
    assert filter_offers([ram16_offer],.35,'any',ram16_card)['id']==2
    assert filter_offers([{**ram16_offer,'dph_total':.21}],.20,'any',ram16_card) is None
    cheapest={**ram16_offer,'id':11,'dph_total':.10,'inet_down':120}
    fast_nearby={**ram16_offer,'id':12,'dph_total':.12,'inet_down':250}
    assert filter_offers([cheapest,fast_nearby],.35,'any',ram16_card)['id']==12
    too_expensive_fast={**fast_nearby,'id':13,'dph_total':.121}
    assert filter_offers([cheapest,too_expensive_fast],.35,'any',ram16_card)['id']==11
    print('offer_disk_filter: passed')

def test_per_config_ram_requirement():
    sys.path.insert(0,str(SCRIPTS))
    import card_runner
    assert card_runner.ram_requirement(30,24)==(24,6,14)
    assert card_runner.ram_requirement(30,30)==(30,0,8)
    # Exactly the required free-RAM threshold passes; anything below is skipped.
    assert 14 >= card_runner.ram_requirement(30,24)[2]
    assert 13.99 < card_runner.ram_requirement(30,24)[2]
    print('per_config_ram_requirement: passed')

def test_smoke_requires_registered_gpu():
    sys.path.insert(0,str(SCRIPTS))
    from offer_filter import filter_offers
    offer={'id':3,'machine_id':3,'gpu_name':'RTX A2000','num_gpus':1,'cpu_ram':64000,'reliability':.99,'verified':True,'rentable':True,'dph_total':.11,'pci_gen':4,'pcie_bw':23.9,'disk_space':690}
    quadro={'id':4,'machine_id':4,'gpu_name':'Q RTX 4000','num_gpus':1,'cpu_ram':64000,'reliability':.99,'verified':True,'rentable':True,'dph_total':.108,'pci_gen':3,'pcie_bw':10.9,'disk_space':256}
    assert filter_offers([offer],.15,'any',[],smoke=False) is None
    selected=filter_offers([offer,quadro],.15,'any',[],smoke=True)
    assert selected is None
    registered=[{'gpu_name':'RTX 3060','disk_gb':100,'native_pcie_gen':4,'native_pcie_width':16}]
    registered_offer={**offer,'gpu_name':'RTX 3060'}
    assert filter_offers([registered_offer],.15,'any',registered,smoke=True)['id']==3
    smoke_ignores_quality={**registered_offer,'cpu_ram':8000,'pci_gen':1,'pcie_bw':1}
    assert filter_offers([smoke_ignores_quality],.15,'any',registered,smoke=True)['id']==3
    print('smoke_registry_only: passed')

if __name__=='__main__':
    test_retry_400_next_offer(); test_empty_stdout_is_create_failure(); test_disk_filter(); test_per_config_ram_requirement(); test_smoke_requires_registered_gpu()
