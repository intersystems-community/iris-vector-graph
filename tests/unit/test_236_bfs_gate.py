"""Spec 236, Story 3: BFS never hands the callout an adjacency it cannot take.

The gate lives in ObjectScript (the string only exists there), so the unit tier reads the
class text; the live proof is tests/integration/test_236_bfs_size_gate.py.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

from iris_vector_graph.stores.iris_sql_store import (
    IRISGraphStore,
    _ArnoBfsAdapter,
    _ObjectScriptBfsAdapter,
)

CLS = Path(__file__).resolve().parents[2] / "iris_src/src/Graph/KG/NKGAccelTraversal.cls"
FALLBACK = 'BFSFastJsonSorted(seed, predicatesJson, maxHops, "", "out", maxResults)'


def _bfs_body() -> str:
    return re.search(r"^ClassMethod BFSJson\(.*?^\}", CLS.read_text(), re.S | re.M).group(0)


def test_the_budget_counts_every_string_argument_not_only_the_adjacency():
    body = _bfs_body()
    check = body[body.index("$Length(adjStr)") : body.index("> ..#ZFMAXCHARS")]
    for arg in ("resultGlobal", "seed", "predicatesJson"):
        assert f"$Length({arg})" in check


def test_the_limit_is_a_named_constant_at_the_measured_value():
    assert re.search(r"^Parameter ZFMAXCHARS = 34808;", CLS.read_text(), re.M)
    assert "zf-heap-and-exstr.md" in _bfs_body()


def test_the_length_is_checked_after_the_export_and_before_the_callout():
    body = _bfs_body()
    export = body.index("ExportAdjacencyWithPreds(")
    check = body.index("$Length(adjStr)")
    call = body.index("$ZF(-5, dllid, fnid, adjStr")
    assert export < check < call


def test_an_oversize_adjacency_returns_the_objectscript_answer():
    body = _bfs_body()
    check = body.index("$Length(adjStr)")
    gate = body[check : body.index("}", check)]
    assert FALLBACK in gate


def _store(arno=True, bfs=True):
    store = IRISGraphStore.__new__(IRISGraphStore)
    store.conn = MagicMock()
    store._arno_available = arno
    store._arno_capabilities = {"bfs": bfs}
    store._detect_arno = lambda: arno
    return store


def test_bfs_routing_by_graph_and_direction_is_unchanged():
    store = _store()
    assert isinstance(store._select_bfs_strategy(None, direction="out"), _ArnoBfsAdapter)
    assert isinstance(store._select_bfs_strategy("", direction="out"), _ArnoBfsAdapter)
    assert isinstance(store._select_bfs_strategy("g1", direction="out"), _ObjectScriptBfsAdapter)
    for direction in ("in", "both"):
        assert isinstance(store._select_bfs_strategy(None, direction=direction), _ObjectScriptBfsAdapter)


def test_kg_bfs_compute_is_not_adopted():
    # FR-015: adoption needs arno's negative-integer-subscript result first.
    for path in CLS.parent.glob("*.cls"):
        assert "kg_bfs_compute" not in path.read_text(), path.name


def test_the_export_stops_at_the_budget_instead_of_building_the_whole_adjacency():
    """Measured: on 22k edges the export alone was 34 ms (617,000 characters), paid on every
    call before the gate turned the call away (docs/research/cypher-stage-timing.md)."""
    adjacency = CLS.parent / "NKGAccelAdjacency.cls"
    text = adjacency.read_text()
    sig = re.search(r"ClassMethod ExportAdjacencyWithPreds\(([^)]*)\)", text).group(1)
    assert "pMaxChars" in sig
    body = _bfs_body()
    assert "ExportAdjacencyWithPreds(,tCap)" in body.replace(" ", "")
    assert '"OVERSIZE"' in body
