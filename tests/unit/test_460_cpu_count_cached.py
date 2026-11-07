"""The native executor asks the CPU count once per process, not per query (4.6.0 speed gate).

`$System.Util.NumberOfCPUs()` costs 1.67 ms a call on a 128-core host (IRIS 2026.3, dpgenai1), and every native query
asked it two or three times in its parallel checks (Exec.ParallelWanted*, VarLength.SplitLast): 3.3 of a one-hop
read's 3.9 ms there. `Graph.KG.VarLength.CPUs()` asks once and keeps the answer in a process-private global; the
executor's per-query paths call it instead. A count asked once per algorithm run (BuildNKG, centrality) is left alone.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "iris_src" / "src" / "Graph" / "KG"
PER_QUERY = (SRC / "Cypher" / "Exec.cls", SRC / "VarLength.cls")


def _methods(text: str) -> dict:
    """Each ClassMethod's name and body text (up to the next member)."""
    parts = re.split(r"^(?=(?:ClassMethod|Method|Parameter|Property|Query|XData) )", text, flags=re.M)
    out = {}
    for part in parts:
        m = re.match(r"(?:ClassMethod|Method) (\w+)\(", part)
        if m:
            out[m.group(1)] = part
    return out


def test_per_query_paths_do_not_ask_the_cpu_count_each_time():
    for cls in PER_QUERY:
        for name, body in _methods(cls.read_text()).items():
            if cls.name == "VarLength.cls" and name == "CPUs":
                continue
            code = "\n".join(line.split("//", 1)[0] for line in body.splitlines() if not line.lstrip().startswith("///"))
            assert "NumberOfCPUs" not in code, f"{cls.name} {name} asks $System.Util.NumberOfCPUs() itself"


def test_cpus_keeps_the_count_in_a_process_private_global():
    body = _methods((SRC / "VarLength.cls").read_text())["CPUs"]
    assert "^||IVG.CPUs" in body
    assert body.count("NumberOfCPUs()") == 1


def test_the_executor_calls_the_cached_count():
    text = (SRC / "Cypher" / "Exec.cls").read_text()
    assert text.count("##class(Graph.KG.VarLength).CPUs()") >= 4
