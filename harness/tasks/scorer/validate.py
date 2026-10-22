import json
import ast
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {"coding": 12, "terminal_system": 8, "tool_use": 8, "structured_data": 6, "multi_step": 6}

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

def _reject_json_constant(value):
    raise ValueError(f"non-standard JSON constant: {value}")

def strict_json_loads(value):
    return json.loads(value, parse_constant=_reject_json_constant)

def json_equal(left, right):
    """Compare JSON values while treating integer/decimal spellings as one number type."""
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(json_equal(left[k], right[k]) for k in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(json_equal(a, b) for a, b in zip(left, right))
    return type(left) is type(right) and left == right

def score(task, response, tool_trace=None):
    try:
        parsed = strict_json_loads(response) if isinstance(response, str) else response
    except (json.JSONDecodeError, TypeError, ValueError):
        return False
    if task.get("expected_calls") is not None:
        if not isinstance(tool_trace,list) or not all(x.get("ok") for x in tool_trace): return False
        calls=[x.get("action") for x in tool_trace]
        return canonical(parsed)==canonical(task["reference_response"]) and canonical(calls)==canonical(task["expected_calls"])
    if task.get("kind") == "python_function":
        try:
            source = parsed["code"]
            if len(source) > 20000:
                return False
            tree = ast.parse(source)
            allowed = (ast.Module, ast.FunctionDef, ast.arguments, ast.arg, ast.Return, ast.Expr, ast.Assign, ast.AugAssign, ast.For,
                ast.If, ast.Compare, ast.In, ast.NotIn, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
                ast.Name, ast.Load, ast.Store, ast.Constant, ast.List, ast.Tuple, ast.Dict, ast.Set, ast.Subscript,
                ast.Slice, ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
                ast.UnaryOp, ast.USub, ast.UAdd, ast.Not, ast.BoolOp, ast.And, ast.Or, ast.Call, ast.keyword,
                ast.ListComp, ast.GeneratorExp, ast.comprehension)
            if any(not isinstance(node, allowed) for node in ast.walk(tree)):
                return False
            if len(list(ast.walk(tree))) > 500:
                return False
            if any(isinstance(n, ast.Constant) and type(n.value) is int and abs(n.value) > 1000000 for n in ast.walk(tree)):
                return False
            functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
            if len(functions) != 1 or functions[0].name != "solve":
                return False
            safe = {"sum": sum, "max": max, "min": min, "sorted": sorted, "set": set, "len": len, "range": range}
            bound = {a.arg for a in ast.walk(tree) if isinstance(a, ast.arg)} | {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name) and node.func.id in safe):
                    return False
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id not in safe and node.id not in bound:
                    return False
            scope = {"__builtins__": {}, **safe}
            exec(compile(tree, "<candidate>", "exec"), scope)
            return all(scope["solve"](*case["args"]) == case["expected"] for case in task["cases"])
        except Exception:
            return False
    accepted = task.get("accepted_responses")
    if accepted is not None:
        return any(json_equal(parsed, value) for value in accepted)
    return json_equal(parsed, task["reference_response"])

def validate():
    tasks = json.loads((ROOT / "cuda-recipes-001-tasks.json").read_text())
    counts = Counter(t["category"] for t in tasks)
    assert len(tasks) == 40, len(tasks)
    assert counts == Counter(EXPECTED), counts
    ids = [t["id"] for t in tasks]
    assert len(ids) == len(set(ids))
    for t in tasks:
        assert all(k in t for k in ("id", "prompt", "reference_response", "negative_control", "scorer")), t["id"]
        if t.get("expected_calls") is not None:
            from .tools import execute_actions
            reference_trace=execute_actions(t["expected_calls"],t["id"])
            negative_trace=execute_actions(t["negative_control"],t["id"])
            assert score(t,t["reference_response"],reference_trace),f"reference rejected: {t['id']}"
            assert not score(t,t["reference_response"],negative_trace),f"negative accepted: {t['id']}"
        else:
            assert score(t, t["reference_response"]), f"reference rejected: {t['id']}"
            assert not score(t, t["negative_control"]), f"negative accepted: {t['id']}"
            if t["category"] == "structured_data":
                assert t["scorer"] == "json-value-equality-v2", f"structured scorer version mismatch: {t['id']}"
                assert not (isinstance(t["reference_response"],dict) and set(t["reference_response"]) == {"result"}), f"structured response should match requested value: {t['id']}"
            if t["category"] == "terminal_system":
                assert t["scorer"] == "terminal-command-allowlist-v1", f"terminal scorer version mismatch: {t['id']}"
                assert 'single key named "result"' in t["prompt"], f"response schema missing: {t['id']}"
                assert any(canonical(t["reference_response"]) == canonical(x) for x in t["accepted_responses"]), t["id"]
                assert all(not score(t, value) for value in [t["negative_control"]]), f"negative allow-listed: {t['id']}"
        assert not score(t, "not json"), f"malformed accepted: {t['id']}"
    print(f"PASS: {len(tasks)} tasks, category counts {dict(counts)}, positive/negative/malformed scorer checks passed")

if __name__ == "__main__":
    validate()
