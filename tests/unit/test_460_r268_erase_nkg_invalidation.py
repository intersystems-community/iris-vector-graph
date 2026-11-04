"""4.6.0 R268, pinned against the source (no container): an erase that removes a node `^NKG`'s dictionary holds
invalidates `^NKG`, and the algorithm readers rebuild an invalidated `^NKG` before they read it.

The behaviour is proven against the server in tests/e2e/test_460_r268_erase_nkg_routes_e2e.py: after
`erase_graph`, `delete_node` and `bulk_delete_nodes` of a named graph (its edges deleted first or not), every `^NKG`
algorithm route, method and CALL, answers the default graph's nodes alone, with no `sync()`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

KG = Path(__file__).resolve().parents[2] / "iris_src" / "src" / "Graph" / "KG"


def _code(text: str) -> str:
    return "\n".join(re.sub(r"(?<![:\"])//.*$", "", ln) for ln in text.splitlines() if not ln.lstrip().startswith("///"))


def _method(text: str, name: str) -> str:
    m = re.search(rf"^ClassMethod {name}\(.*?^\}}", text, re.S | re.M)
    assert m, f"no ClassMethod {name}"
    return m.group(0)


@pytest.fixture(scope="module")
def eraser() -> str:
    return _code((KG / "Eraser.cls").read_text())


def test_a_graph_erase_asks_about_the_graphs_nodes_as_well_as_its_edges(eraser):
    body = _method(eraser, "EraseGraph")
    assert re.search(r"Set tNkgAtRisk = \.\.GraphEdgesInNKG\(tKey, tName\) \|\| \.\.GraphNodesInNKG\(tName\)", body)
    # decided before the node rows go
    assert body.index("GraphNodesInNKG(") < body.index("DELETE FROM Graph_KG.nodes")
    nodes = _method(eraser, "GraphNodesInNKG")
    assert "FROM Graph_KG.nodes" in nodes and "COALESCE(graph_id, '') = COALESCE(:pName, '')" in nodes
    assert "..NodeInterned(tNode)" in nodes


def test_a_node_erase_with_no_edge_still_asks_whether_nkg_holds_the_node(eraser):
    body = _method(eraser, "EraseIdSet")
    assert "Do ..InvalidateDerived(tNkgAtRisk)" in body
    assert "InvalidateDerived(tHadEdges && tNkgAtRisk)" not in body
    assert "If tHadEdges && 'tNkgAtRisk" not in body


def test_the_drop_marks_a_built_nkg_for_its_readers_and_erase_all_does_not(eraser):
    body = _method(eraser, "InvalidateDerived")
    assert 'Set tWasBuilt = ($Order(^NKG("$NI", "")) \'= "")' in body
    assert body.index("tWasBuilt =") < body.index("DropNKG()") < body.index('Set ^NKG("$meta", "invalidated") = 1')
    assert "Do ..InvalidateDerived(1, 0)" in _method(eraser, "EraseAll")


def test_only_the_builder_rebuilds_and_a_build_answers_the_mark():
    build = _code((KG / "TraversalBuild.cls").read_text())
    ensure = _method(build, "EnsureNKG")
    assert '$Get(^NKG("$meta", "invalidated"))' in ensure and "..BuildNKG()" in ensure
    assert "Lock +^IVG.NKGBuild:" in ensure and ensure.count("Lock -^IVG.NKGBuild") == 2
    assert 'Kill ^NKG("$meta", "invalidated")' in _method(build, "RewritePass")


#: every algorithm reader that answers "^NKG not built", by class: (ObjectScript methods, embedded-Python methods)
READERS = {
    "Communities.cls": (("TriangleCountJson", "KCoreJson", "SCCJson", "LeidenJson"), ("ClosenessJsonPy", "LeidenJsonPy")),
    "NKGAccelCentrality.cls": (("ClosenessGlobal", "EigenvectorGlobal", "BetweennessGlobalParallel",
                                "BetweennessNeighborhood", "ClosenessGlobalMSBFS"), ()),
}


@pytest.mark.parametrize("cls", sorted(READERS))
def test_every_algorithm_reader_rebuilds_an_invalidated_nkg_before_it_reads(cls):
    text = (KG / cls).read_text()
    code = _code(text)
    os_methods, py_methods = READERS[cls]
    named = set(os_methods) | set(py_methods)
    for m in re.finditer(r"^ClassMethod (\w+)\(", text, re.M):
        if "not built" in _method(text, m.group(1)):
            assert m.group(1) in named, f"{cls} {m.group(1)} answers 'not built' and is not listed here"
    for name in os_methods:
        body = _method(code, name)
        call = body.index("##class(Graph.KG.TraversalBuild).EnsureNKG()")
        assert call < body.index("not built"), name
    for name in py_methods:
        body = _method(text, name)
        assert body.index('iris.cls("Graph.KG.TraversalBuild").EnsureNKG()') < body.index('iris.gref("^NKG")'), name
