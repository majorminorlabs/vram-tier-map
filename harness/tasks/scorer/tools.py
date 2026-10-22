"""Allowlisted, side-effect-free fixture tools for benchmark tool tasks."""
class Simulator:
    def __init__(self, task_id):
        self.task_id=task_id
        self.state={"stock":12,"files":{"drafts/report.csv":"id,value\n1,3\n2,4\n"},"copied":False,"verified":False,"deleted":False,"done":set(),"events":[("09:00","10:00"),("11:00","12:00")],"availability_checked":False,"summary":None,"summary_written":False,"tests_passed":False,"package_built":False}

    def step(self, action):
        if not isinstance(action,dict) or not isinstance(action.get("tool"),str): return {"error":"malformed action"},False
        tool=action["tool"]
        try:
            if self.task_id=="TO01" and tool=="read" and action=={"tool":"read","path":"/workspace/README.md"}:
                return {"path":action["path"],"content":"Fixture workspace: benchmark sandbox."},True
            if self.task_id=="TO02" and tool=="add" and set(action)=={"tool","a","b"} and all(type(action[k]) is int for k in ("a","b")):
                return {"value":action["a"]+action["b"]},True
            if self.task_id=="TO03" and tool=="get" and action=={"tool":"get","id":"T-204"}:
                return {"id":"T-204","status":"open"},True
            if self.task_id=="TO04" and tool=="list" and action=={"tool":"list","date":"2026-11-02"}:
                return {"date":action["date"],"events":[]},True
            if self.task_id=="TO05" and tool=="query" and action=={"tool":"query","sql":"SELECT COUNT(*) FROM orders;"}:
                return {"count":7},True
            if self.task_id=="TO06" and tool=="open" and action=={"tool":"open","url":"https://example.org"}:
                return {"url":action["url"],"status":200,"title":"Example Domain"},True
            if self.task_id=="TO07" and tool=="status" and action=={"tool":"status"}:
                return {"branch":"benchmark-fixture","changes":2},True
            if self.task_id=="TO07" and tool=="diff" and action=={"tool":"diff"}:
                return {"files_changed":["src/a.py","README.md"]},True
            if self.task_id=="TO08" and tool=="forecast" and action=={"tool":"forecast","city":"Oslo","date":"2026-12-01"}:
                return {"city":"Oslo","date":action["date"],"forecast":"cloudy"},True
            return self._multistep(tool,action)
        except Exception as exc:
            return {"error":str(exc)},False

    def _multistep(self,tool,a):
        s=self.state
        if self.task_id=="MU01":
            if tool=="get_stock" and a=={"tool":tool,"product":"cable"} and not s.get("adjusted"):
                s["stock_seen"]=True; return {"product":"cable","quantity":s["stock"]},True
            if tool=="adjust_stock" and a=={"tool":tool,"product":"cable","delta":-5} and s.get("stock_seen"):
                s["stock"]-=5; s["adjusted"]=True; return {"quantity":s["stock"]},True
            if tool=="get_stock" and a=={"tool":tool,"product":"cable"} and s.get("adjusted"):
                s["verified"]=s["stock"]==7; return {"product":"cable","quantity":s["stock"]},True
        if self.task_id=="MU02":
            if tool=="list_file" and a=={"tool":tool,"path":"drafts/report.csv"} and not s["deleted"]:
                return {"path":"drafts/report.csv","exists":True,"bytes":len(s["files"]["drafts/report.csv"])},True
            if tool=="copy_file" and a=={"tool":tool,"src":"drafts/report.csv","dst":"archive/report.csv"} and "drafts/report.csv" in s["files"]:
                s["files"]["archive/report.csv"]=s["files"]["drafts/report.csv"]; s["copied"]=True; return {"copied":True},True
            if tool=="verify_file" and a=={"tool":tool,"path":"archive/report.csv"} and s["copied"]:
                s["verified"]=s["files"]["archive/report.csv"]==s["files"]["drafts/report.csv"]; return {"matches_source":s["verified"]},s["verified"]
            if tool=="delete_file" and a=={"tool":tool,"path":"drafts/report.csv"} and s["verified"]:
                del s["files"]["drafts/report.csv"]; s["deleted"]=True; return {"deleted":True},True
        if self.task_id=="MU03" and tool=="complete_task" and set(a)=={"tool","id"}:
            deps={"D":set(),"B":{"D"},"C":{"D"},"A":{"B","C"}}; task=a["id"]
            if task not in deps or not deps[task].issubset(s["done"]) or task in s["done"]: raise ValueError("task dependency or duplicate violation")
            s["done"].add(task); return {"completed":task},True
        if self.task_id=="MU04":
            if tool=="find_availability" and a=={"tool":tool,"date":"2026-11-03","start":"10:00","end":"11:00"}:
                s["availability_checked"]=True; return {"available":True},True
            if tool=="create_event" and a=={"tool":tool,"date":"2026-11-03","start":"10:00","end":"11:00","title":"Review"} and s["availability_checked"]:
                s["events"].append(("10:00","11:00")); s["created"]=True; return {"created":True},True
        if self.task_id=="MU05":
            if tool=="summarize_source" and a=={"tool":tool,"name":"quarterly"}:
                s["summary"]={"rows":3,"total":42}; return s["summary"],True
            if tool=="write_summary" and a=={"tool":tool,"name":"quarterly-summary","rows":3} and s["summary"]:
                s["summary_written"]=True; return {"written":True,"rows":3},True
            if tool=="verify_summary" and a=={"tool":tool,"name":"quarterly-summary"} and s["summary_written"]:
                return {"valid":True,"rows":3},True
        if self.task_id=="MU06":
            if tool=="run_tests" and a=={"tool":tool,"project":"app"}:
                s["tests_passed"]=True; return {"passed":True,"failed":0},True
            if tool=="build_package" and a=={"tool":tool,"project":"app"} and s["tests_passed"]:
                s["package_built"]=True; return {"package":"app-rc.tar.gz"},True
            if tool=="verify_package" and a=={"tool":tool,"project":"app"} and s["package_built"]:
                return {"valid":True,"deployed":False},True
        raise ValueError("unsupported, unsafe, or out-of-order simulated action")

def execute_actions(actions,task_id):
    sim=Simulator(task_id); trace=[]
    for action in actions:
        result,ok=sim.step(action); trace.append({"action":action,"result":result,"ok":ok})
        if not ok: break
    return trace
