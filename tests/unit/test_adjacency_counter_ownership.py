"""One module owns `^KG` adjacency, including its counters.

`Graph.KG.EdgeScan.WriteAdjacency` writes four things for one edge:
`^KG("out")`, `^KG("in")`, `^KG("deg")` and `^KG("degp")`, all under the graph
key. Its opposite number, `DeleteAdjacency`, removed only the first two and
delegated the rest to `Graph.KG.GraphIndex.DeleteIndex`, which:

  * hardcoded the graph key to `0`, so a named graph's adjacency survived
    deletion entirely, and
  * spelled the counters `^KG("deg", s)` and `^KG("degp", s, p)` — with no graph
    subscript at all. Since `WriteAdjacency` writes `^KG("deg", graph, s)`, the
    node id landed in the *graph key* position. The real counter was never
    decremented and a bogus sibling was created next to every real graph.

The split is what allowed the asymmetry: writes and deletes lived in different
modules, so `^KG` had two owners that disagreed. These tests lock the ownership
rather than the symptom — `EdgeScan` owns `^KG`, `GraphIndex` owns `^NKG`. That
is the settled shape from grilling Q13 (subsume the counters into `EdgeScan`).

Source-level, so it runs without a container. The behaviour is locked in
`tests/integration/test_delete_adjacency.py`.
"""

from pathlib import Path
import re

IRIS_SRC = Path(__file__).resolve().parents[2] / "iris_src" / "src"
EDGE_SCAN = IRIS_SRC / "Graph" / "KG" / "EdgeScan.cls"
GRAPH_INDEX = IRIS_SRC / "Graph" / "KG" / "GraphIndex.cls"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _method_body(source: str, name: str) -> str:
    """The text of one ClassMethod, from its signature to the next one."""
    start = source.index(f"ClassMethod {name}(")
    rest = source[start + 1 :]
    end = rest.find("\nClassMethod ")
    return rest if end == -1 else rest[:end]


# --- EdgeScan owns the counters ---------------------------------------------


def test_delete_adjacency_decrements_the_degree_counter():
    body = _method_body(_source(EDGE_SCAN), "DeleteAdjacency")
    assert re.search(r'\^KG\("deg",\s*graph', body), (
        'DeleteAdjacency does not touch ^KG("deg", graph, ...) — the degree '
        "counter outlives the edge it counted."
    )


def test_delete_adjacency_decrements_the_per_predicate_counter():
    body = _method_body(_source(EDGE_SCAN), "DeleteAdjacency")
    assert re.search(r'\^KG\("degp",\s*graph', body), (
        'DeleteAdjacency does not touch ^KG("degp", graph, ...).'
    )


def test_delete_adjacency_spells_the_counters_with_a_graph_key():
    """A counter without the graph subscript puts the node id in the graph slot."""
    body = _method_body(_source(EDGE_SCAN), "DeleteAdjacency")
    for bad in ['^KG("deg", s)', '^KG("deg",s)', '^KG("degp", s,', '^KG("degp",s,']:
        assert bad not in body, (
            f"DeleteAdjacency writes {bad}, omitting the graph key. "
            "WriteAdjacency writes ^KG(\"deg\", graph, s), so this decrements "
            "nothing and pollutes the graph-key level of the tree."
        )


def test_write_and_delete_adjacency_touch_the_same_four_stores():
    """Symmetry is the property; the counters were the half that went missing."""
    source = _source(EDGE_SCAN)
    write = _method_body(source, "WriteAdjacency")
    delete = _method_body(source, "DeleteAdjacency")
    for store in ("out", "in", "deg", "degp"):
        assert f'^KG("{store}"' in write, f'WriteAdjacency does not write ^KG("{store}")'
        assert f'^KG("{store}"' in delete, (
            f'WriteAdjacency writes ^KG("{store}") but DeleteAdjacency never '
            "removes it — that asymmetry is the leak."
        )


# --- GraphIndex owns ^NKG, and only ^NKG ------------------------------------


def test_delete_index_no_longer_touches_kg():
    """Two owners of ^KG is what let the two disagree (grilling Q13)."""
    body = _method_body(_source(GRAPH_INDEX), "DeleteIndex")
    assert "^KG(" not in body, (
        "Graph.KG.GraphIndex.DeleteIndex still writes ^KG. EdgeScan owns ^KG "
        "adjacency; GraphIndex owns ^NKG interning. Splitting one global across "
        "two modules is what produced the hardcoded graph key 0."
    )


def test_delete_index_still_removes_the_nkg_entries():
    """Deleting its ^KG responsibility must not delete its real one."""
    body = _method_body(_source(GRAPH_INDEX), "DeleteIndex")
    assert "^NKG(-1," in body and "^NKG(-2," in body, (
        "DeleteIndex no longer removes the ^NKG adjacency entries."
    )


def test_the_known_defect_comment_is_gone():
    """The comment described the bug these tests fix; it must not outlive it."""
    assert "KNOWN DEFECT" not in _source(GRAPH_INDEX), (
        "GraphIndex still carries the KNOWN DEFECT note about the hardcoded "
        "graph key. Either the defect is unfixed or the comment is stale — both "
        "are wrong."
    )


# --- bulk deletion has the same obligation ----------------------------------


def test_bulk_delete_adjacency_delegates_to_the_per_edge_delete():
    """Bulk deletion must not restate the per-edge invariant, or it will drift.

    Removing a node touches both ends of every edge it participates in: the
    targets' `^KG("in")` mirrors, and the out-degree of every source that pointed
    at it. `BulkDeleteAdjacency` reached neither, because it killed subtrees
    directly instead of going through `DeleteAdjacency` — a second statement of
    the same rule, which is how the two came to disagree.

    The behaviour is locked in `tests/integration/test_delete_adjacency.py`; this
    guards the ownership, so the logic cannot be copied back in.
    """
    body = _method_body(_source(EDGE_SCAN), "BulkDeleteAdjacency")
    assert "..DeleteAdjacency(" in body, (
        "BulkDeleteAdjacency does not delegate to DeleteAdjacency, so the "
        "per-edge invariant is stated in two places."
    )


def test_bulk_delete_adjacency_does_not_kill_counters_itself():
    """A second owner of the counters is the defect, not the missing lines."""
    body = _method_body(_source(EDGE_SCAN), "BulkDeleteAdjacency")
    for bad in ('Kill ^KG("deg"', 'Kill ^KG("degp"'):
        assert bad not in body, (
            f"BulkDeleteAdjacency contains `{bad}`, taking ownership of a counter "
            "that DeleteAdjacency already owns."
        )


def test_bulk_delete_adjacency_walks_the_inbound_direction():
    """Its sources lose out-degree, so inbound edges must be visited too."""
    body = _method_body(_source(EDGE_SCAN), "BulkDeleteAdjacency")
    assert '^KG("in", g, nodeId' in body, (
        "BulkDeleteAdjacency never walks the node's inbound edges, so every node "
        "that pointed at it keeps counting the deleted edge."
    )
