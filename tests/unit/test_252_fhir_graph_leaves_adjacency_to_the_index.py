"""Spec 252, T022: with the adjacency index declared, Graph.KG.FHIRGraph does not write adjacency a row write just wrote.

Its writers inserted or deleted an rdf_edges / rdf_labels row and then wrote the same ^KG("out"/"in"/"label") entry
again through Graph.KG.Adjacency: with the index, the second write is a second generation and version bump per edge.
Those writes are skipped when the instance's Indexed flag is set (read once, beside GKey).

The repairs stay: a resync re-puts adjacency a surviving row lost and removes adjacency no row justifies, and a drop
sweeps adjacency left without rows. No row is written there, so the index cannot do it
(test_fhirgraph_failures_e2e.py).
"""

from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "iris_src" / "src" / "Graph" / "KG" / "FHIRGraph.cls"


def _text():
    return SRC.read_text(encoding="utf-8")


def _method(name):
    text = _text()
    start = text.index(f"Method {name}(")
    return text[start : text.index("\n}\n", start)]


def test_the_flag_is_read_beside_the_graph_key():
    text = _text()
    assert "Property Indexed As %Boolean" in text
    assert "tCtx.Indexed = ##class(Graph.KG.Adjacency).Indexed()" in text


def test_the_put_after_an_edge_insert_is_skipped_when_indexed():
    lines = _text().splitlines()
    inserts = [i for i, ln in enumerate(lines) if "INSERT INTO Graph_KG.rdf_edges" in ln]
    assert inserts
    for i in inserts:
        assert "Do:'..Indexed ##class(Graph.KG.Adjacency).Put(" in lines[i + 1]


def test_delete_edge_leaves_the_remove_to_the_index():
    assert "Do:'..Indexed ##class(Graph.KG.Adjacency).Remove(" in _method("DeleteEdge")


def test_label_kills_beside_a_label_row_delete_are_skipped_when_indexed():
    assert "Kill:'..Indexed ^KG(\"label\", ..GKey, tL, pKey)" in _method("SyncProps")
    assert "If '..Indexed, $Data(^KG(\"label\"" in _method("DropNode")


def test_the_repairs_are_not_skipped():
    resync = _method("ResyncKey")
    assert "If '$Data(^KG(\"out\", ..GKey, pKey, tP, tO)) {" in resync  # a surviving row's lost adjacency
    assert "\t\t\tDo ##class(Graph.KG.Adjacency).Remove(..GKey, pKey, tP, tO)" in resync  # adjacency no row justifies
    assert "Do ##class(Graph.KG.Adjacency).Remove(..GKey, $ListGet(tDrift(tI), 1)" in _method("DropNode")
