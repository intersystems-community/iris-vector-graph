"""4.6.0 R273 (native grouping by a whole float), pinned against Graph.KG.Cypher.Exec's source (no container): a long
group key and a group merged from workers are kept as the keys' values, which KeysJSON writes out typed (R263), not
as JSON, which writes a whole $DOUBLE as an integer.

The answers are proven live in tests/e2e/test_460_r273_native_group_keys_e2e.py.
"""

from __future__ import annotations

import re
from pathlib import Path

EXEC = Path(__file__).resolve().parents[2] / "iris_src" / "src" / "Graph" / "KG" / "Cypher" / "Exec.cls"


def _method(name: str) -> str:
    m = re.search(rf"^ClassMethod {name}\(.*?^\}}", EXEC.read_text(), re.S | re.M)
    assert m, name
    return "\n".join(ln for ln in m.group(0).splitlines() if not ln.lstrip().startswith("//"))


def test_a_long_key_s_group_stores_its_values():
    body = _method("GroupOf")
    long_branch = body.split("Set tKeys = ..Project(.pQ, pGName, .pB)", 1)[1]
    assert '^||IVG.CypherRun("g", tG) = $Char(1)_tVals' in long_branch
    assert 'Set ^||IVG.CypherRun("g", tG) = tKeys' not in body


def test_a_worker_exports_its_group_s_values_under_the_shared_json():
    body = _method("Export")
    assert 'Set tStored = tK, tK = ..KeysJSON(.pQ, tK), ^IRIS.Temp.IVG.Cypher(pShare, "k", tK) = tStored' in body


def test_the_merge_keeps_the_exported_values():
    body = _method("Parallel")
    assert 'Set tK = $Order(^IRIS.Temp.IVG.Cypher(tShare, "k", tK), 1, tStored)' in body
    assert '^||IVG.CypherRun("g", tG) = tStored' in body
