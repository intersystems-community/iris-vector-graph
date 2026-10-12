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
ADJACENCY = IRIS_SRC / "Graph" / "KG" / "Adjacency.cls"
GRAPH_INDEX = IRIS_SRC / "Graph" / "KG" / "GraphIndex.cls"

# Since spec 245 the stores are written in Graph.KG.Adjacency: EdgeScan's
# WriteAdjacency and DeleteAdjacency are one-line delegations to Put and Remove, so
# the ownership statements below are checked on those bodies.


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _method_body(source: str, name: str) -> str:
    """The text of one ClassMethod, from its signature to the next one."""
    start = source.index(f"ClassMethod {name}(")
    rest = source[start + 1 :]
    end = rest.find("\nClassMethod ")
    return rest if end == -1 else rest[:end]


# --- Adjacency owns the mirror and the generation (spec 248) -----------------------
#
# The stored counters ^KG("deg") and ^KG("degp") are gone, so the four-store symmetry
# these tests used to pin is now a three-store one: out, in and the generation
# ^KG("gen", graph, src) that the degree cache validates against. Changed on purpose:
# a counter kept with $Increment drifts when a transaction rolls back, which is the
# ghost-counter defect the whole alternative exists to remove (ADR-0010).


def test_delete_adjacency_bumps_the_generation_with_a_graph_key():
    body = _method_body(_source(ADJACENCY), "Remove")
    assert re.search(r'\^KG\("gen",\s*pGraph,\s*pSrc', body), (
        'Adjacency.Remove (DeleteAdjacency) does not bump ^KG("gen", pGraph, pSrc): the degree '
        "cache would keep serving the count of an edge that is gone."
    )


def test_delete_adjacency_does_not_touch_the_removed_counters():
    body = _method_body(_source(ADJACENCY), "Remove")
    assert not re.search(r'\^KG\("degp?"', body), (
        'Adjacency.Remove still writes ^KG("deg") or ^KG("degp"); the degree is counted and cached.'
    )


def test_delete_adjacency_spells_the_generation_with_a_graph_key():
    """A generation without the graph subscript puts the node id in the graph slot."""
    body = _method_body(_source(ADJACENCY), "Remove")
    for bad in ['^KG("gen", pSrc)', '^KG("gen",pSrc)']:
        assert bad not in body, (
            f"Adjacency.Remove writes {bad}, omitting the graph key. "
            "Put bumps ^KG(\"gen\", pGraph, pSrc), so this would bump a different node and "
            "pollute the graph-key level of the tree."
        )


def test_write_and_delete_adjacency_touch_the_same_three_stores():
    """Symmetry is the property; a half that does not bump the generation is the leak."""
    source = _source(ADJACENCY)
    write = _method_body(source, "Put")
    delete = _method_body(source, "Remove")
    for store in ("out", "in", "gen"):
        assert f'^KG("{store}"' in write, f'WriteAdjacency does not write ^KG("{store}")'
        assert f'^KG("{store}"' in delete, (
            f'WriteAdjacency writes ^KG("{store}") but DeleteAdjacency never '
            "touches it - that asymmetry is the leak."
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


def test_bulk_delete_adjacency_delegates_to_adjacency():
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
    assert "Adjacency).RemoveNode(" in body, (
        "BulkDeleteAdjacency does not delegate to Graph.KG.Adjacency.RemoveNode, so the "
        "per-edge invariant is stated in two places."
    )


def test_bulk_delete_adjacency_does_not_kill_the_cache_itself():
    """A second owner of the generation is the defect, not the missing lines."""
    body = _method_body(_source(EDGE_SCAN), "BulkDeleteAdjacency")
    for bad in ('Kill ^KG("deg"', 'Kill ^KG("degp"', 'Kill ^KG("gen"', 'Kill ^KG("degc"'):
        assert bad not in body, (
            f"BulkDeleteAdjacency contains `{bad}`, taking ownership of a store "
            "that Graph.KG.Adjacency already owns."
        )


def test_bulk_delete_adjacency_walks_the_inbound_direction():
    """Its sources lose out-degree, so inbound edges must be visited too."""
    body = _method_body(_source(ADJACENCY), "RemoveNode")
    assert '^KG("in", pGraph, pNode' in body, (
        "Adjacency.RemoveNode never walks the node's inbound edges, so every node "
        "that pointed at it keeps counting the deleted edge."
    )
