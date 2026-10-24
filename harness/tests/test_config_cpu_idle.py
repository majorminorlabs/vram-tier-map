#!/usr/bin/env python3
import importlib.util
from pathlib import Path
p=Path(__file__).resolve().parents[1]/'scripts/card_runner.py'
spec=importlib.util.spec_from_file_location('card_runner',p);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
assert mod.cpu_idle_gate(0,10)=='pass'
assert mod.cpu_idle_gate(1,90)=='pass'
assert mod.cpu_idle_gate(1,89.9)=='skipped: CPU busy'
print('CPU idle policy: only CPU-offloaded configs require >=90% idle')
