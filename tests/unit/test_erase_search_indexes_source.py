"""Every erase path reaches the BM25 indexes (source level, no container).

The behaviour, asserted on `^BM25Idx` after the call, is in tests/integration/test_erase_search_indexes.py. These
checks keep the three erase entry points, the inventory and the snapshot plan from losing the index again: each one
named it in neither place before.
"""

from __future__ import annotations

import re
from pathlib import Path

from iris_vector_graph._engine import snapshot as snapshot_mod

SRC = Path(__file__).resolve().parents[2] / "iris_src" / "src" / "Graph" / "KG"


def _method(text: str, name: str) -> str:
    """The body of `ClassMethod <name>(...)` up to the next ClassMethod."""
    start = text.index(f"ClassMethod {name}(")
    nxt = text.find("\nClassMethod ", start + 1)
    return text[start : nxt if nxt != -1 else len(text)]


def test_erase_graph_empties_the_graphs_indexes():
    body = _method((SRC / "Eraser.cls").read_text(), "EraseGraph")
    assert "Graph.KG.BM25Index).EraseGraph(tName)" in body


def test_erase_of_node_ids_removes_their_documents():
    body = _method((SRC / "Eraser.cls").read_text(), "EraseIdSet")
    assert "Graph.KG.BM25Index).EraseDocs(pName, $Name(^||IVG.EraseIds))" in body


def test_erase_all_empties_every_index():
    body = _method((SRC / "Eraser.cls").read_text(), "EraseAll")
    assert "Graph.KG.BM25Index).EraseAll()" in body


def test_the_inventory_names_the_global():
    text = (SRC / "GraphStores.cls").read_text()
    assert re.search(r'\.\.Entry\("\^BM25Idx", "global", "[^"]+", 1\)', text)


def test_the_snapshot_plan_carries_the_global():
    assert snapshot_mod.STORE_PLAN["^BM25Idx"] == "global"
    assert ("BM25Idx", [[]]) in snapshot_mod.snapshot_globals("Graph_KG")


def test_the_erase_scopes_an_index_by_the_graph_it_recorded():
    """Build records the graph in cfg; the erase reads it back, so an index of another graph is not touched."""
    text = (SRC / "BM25Index.cls").read_text()
    assert '^BM25Idx(name, "cfg", "graph") = pGraph' in text
    for method in ("EraseGraph", "EraseDocs"):
        assert '$Get(^BM25Idx(tName, "cfg", "graph"), "") \'= pGraph' in _method(text, method)
