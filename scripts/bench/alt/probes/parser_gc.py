"""Show that the cypher-parser-bench q4 difference is a garbage-collection landing, not parser cost.

Run from the repository root; it launches the benchmark under each side's runner environment with GC on, off and frozen.
"""
import sys, subprocess, re, os
sys.path.insert(0, ".")
from scripts.bench.alt import run_matrix as rm, sides as sd, suites as su
suite = next(s for s in su.SUITES if s.id == "cypher-parser-bench")
sides = list(sd.SIDES.values()) if isinstance(sd.SIDES, dict) else list(sd.SIDES)
B = sides[1]
full = rm.build_env(B, suite)
def run(env, label):
    vals = []
    for _ in range(3):
        p = subprocess.run([rm.PYTHON, "tests/benchmark_parser.py"], cwd=B.tree, env=env, capture_output=True, text=True)
        m = re.search(r"WITH a.*?Avg: ([0-9.]+)", p.stdout, re.S)
        vals.append(m.group(1) if m else "?")
    print(label, vals, flush=True)
base = dict(os.environ)
no_ivg = {k: v for k, v in full.items() if not (k.startswith("IRIS_") or k.startswith("IVG_"))}
only_pp = dict(base); only_pp["PYTHONPATH"] = full["PYTHONPATH"]; only_pp["PYTHONDONTWRITEBYTECODE"] = "1"
nosc = dict(full); nosc["PYTHONPATH"] = B.tree

print("--- gc tests with full runner env")
for side in (sides[0], sides[1]):
    env = rm.build_env(side, suite)
    for label, pre in (("gc on", ""), ("gc.disable()", "import gc; gc.disable(); "), ("gc.freeze()", "import gc; gc.collect(); gc.freeze(); ")):
        vals = []
        for _ in range(3):
            code = pre + "import runpy; runpy.run_path('tests/benchmark_parser.py', run_name='__main__')"
            p = subprocess.run([rm.PYTHON, "-c", code], cwd=side.tree, env=env, capture_output=True, text=True)
            m = re.search(r"WITH a.*?Avg: ([0-9.]+)", p.stdout, re.S)
            vals.append(m.group(1) if m else "?")
        print(side.letter, label, vals, flush=True)
