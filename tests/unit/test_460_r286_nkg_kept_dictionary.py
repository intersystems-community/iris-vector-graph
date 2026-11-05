"""4.6.0 R286 (the ^NKG item), pinned against the ObjectScript source (no container): with the dictionary kept, a
rebuild retires a dead node's name from ^NKG("$NI") to ^NKG("$NX"), and InternNode gives it its index back.

The answers are proven live in tests/e2e/test_460_r286_nkg_kept_dictionary_e2e.py.
"""

from __future__ import annotations

import re
from pathlib import Path

KG = Path(__file__).resolve().parents[2] / "iris_src" / "src" / "Graph" / "KG"


def _method(cls: str, name: str) -> str:
    m = re.search(rf"^ClassMethod {name}\(.*?^\}}", (KG / cls).read_text(), re.S | re.M)
    assert m, name
    return "\n".join(ln for ln in m.group(0).splitlines() if not ln.lstrip().startswith("//"))


def test_the_kept_dictionary_retires_the_dead_name():
    body = _method("TraversalBuild.cls", "SweepNodes")
    kept = body[body.index("If 'pRemove {"):]
    assert kept.index("Do ..RetireNode(tI, tName)") < kept.index("Continue")


def test_retire_moves_the_name_under_the_per_name_lock_and_puts_it_back_when_a_put_raced():
    body = _method("TraversalBuild.cls", "RetireNode")
    assert body.index('Lock +^NKG("$NI", pName):5') < body.index('Kill ^NKG("$NI", pName)')
    assert 'Set ^NKG("$NX", pName) = +pI' in body
    put_back = body[body.index('Set ^NKG("$NX", pName) = +pI'):]
    assert "If ..NodeInKG(pName) || $Data(^NKG(-1, pI)) || $Data(^NKG(-2, pI)) {" in put_back
    assert 'Set ^NKG("$NI", pName) = +pI' in put_back
    # the dictionary entry itself stays: no gap below nodeCount for a reader that walks it by index
    assert 'Kill ^NKG("$ND"' not in body


def test_intern_gives_a_retired_name_its_index_back_before_allocating():
    body = _method("GraphIndex.cls", "InternNode")
    nx = body.index('Set idx = $Get(^NKG("$NX", id))')
    assert nx < body.index('$Increment(^NKG("$meta", "nodeCount"))')
    assert 'If $Get(^NKG("$ND", idx)) = id {' in body[nx:]


def test_the_sweep_drops_retired_names_with_nothing_to_take_back():
    body = _method("TraversalBuild.cls", "SweepNodes")
    assert 'Set tName = $Order(^NKG("$NX", tName), 1, tI)' in body
    assert "If ($Get(^NKG(\"$ND\", tI), $Char(0)) '= tName) || $Data(^NKG(\"$NI\", tName)) {" in body
    assert 'Kill ^NKG("$NX", pName)' in _method("TraversalBuild.cls", "PutNodeBack")
