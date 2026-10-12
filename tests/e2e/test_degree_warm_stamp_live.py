"""ADR-0012 against IRIS: the whole-graph warm stamp (`^KG("degw")`, `Graph.KG.Adj.AllWarm`).

While the stamp holds, bulk readers (centrality, the NKG and two-hop builds, KHopCount) read one global per node and treat an
absent cache entry as zero. It must therefore turn off on ANY write that could change an answer, and a reader must give the
same answer as a recount whether the stamp holds or not. Runs only in the scratch namespace (see test_degree_cache_live.py):

    IVG_NAMESPACE=IVGPAROS pytest tests/e2e/test_degree_warm_stamp_live.py
"""

from __future__ import annotations

import os

import pytest

pytestmark = [pytest.mark.e2e]

SCRATCH = os.environ.get("IVG_PAROS_NAMESPACE", "IVGPAROS")
G = "warm248"


@pytest.fixture
def native(iris_connection):
    ns = getattr(iris_connection, "namespace", None)
    assert ns == SCRATCH, f"connected to {ns!r}, not the scratch namespace {SCRATCH!r}; set IVG_NAMESPACE={SCRATCH}"
    import iris

    handle = iris.createIRIS(iris_connection)
    _clear(handle)
    yield handle
    _clear(handle)


def _clear(n):
    n.classMethodValue("Graph.KG.Adjacency", "RemoveGraph", G)
    n.classMethodValue("Graph.KG.Adjacency", "RemoveGraph", 0)


def _put(n, s, p, o, graph=G):
    n.classMethodValue("Graph.KG.Adjacency", "Put", graph, s, p, o, "1.0")


def _warm(n, graph=G):
    return int(n.classMethodValue("Graph.KG.Adj", "WarmAll", graph))


def _allwarm(n, graph=G):
    return int(n.classMethodValue("Graph.KG.Adj", "AllWarm", graph))


def _count(n, s, p="", graph=G):
    return int(n.classMethodValue("Graph.KG.Adj", "Count", graph, s, p))


def _degree(n, s, p="", graph=G):
    return int(n.classMethodValue("Graph.KG.Adj", "Degree", graph, s, p))


def test_the_stamp_is_off_until_a_warm_fill_and_on_after_it(native):
    _put(native, "a", "R", "b")
    assert _allwarm(native) == 0
    assert _warm(native) == 1
    assert _allwarm(native) == 1


def test_any_adjacency_write_turns_the_stamp_off(native):
    _put(native, "a", "R", "b")
    _warm(native)
    assert _allwarm(native) == 1

    _put(native, "a", "R", "c")
    assert _allwarm(native) == 0, "a put must turn the stamp off"

    _warm(native)
    native.classMethodValue("Graph.KG.Adjacency", "Remove", G, "a", "R", "c")
    assert _allwarm(native) == 0, "a remove must turn the stamp off"

    _warm(native)
    native.classMethodValue("Graph.KG.Adjacency", "RemoveSource", G, "a")
    assert _allwarm(native) == 0, "removing a source must turn the stamp off"


def test_an_absent_entry_is_zero_only_while_the_stamp_holds(native):
    _put(native, "a", "R", "b")
    _put(native, "c", "S", "d")
    _warm(native)
    assert _allwarm(native) == 1
    assert native.classMethodValue("Graph.KG.Adj", "Degree", G, "zz") in (0, "0")
    assert _degree(native, "a", "S") == 0

    native.classMethodValue("Graph.KG.Adjacency", "RemoveSource", G, "c")
    assert _allwarm(native) == 0
    assert _degree(native, "c") == 0
    assert _degree(native, "a") == 1


def test_erasing_the_graph_kills_the_stamp_and_turns_it_off(native):
    _put(native, "a", "R", "b")
    _warm(native)
    native.classMethodValue("Graph.KG.Adjacency", "RemoveGraph", G)
    assert native.isDefined("^KG", "degw", G) == 0
    assert _allwarm(native) == 0


def test_a_partial_warm_fill_does_not_set_the_stamp(native):
    """A reader of one source sees its entries filled, but the graph is not warm until every source is."""
    _put(native, "a", "R", "b")
    _put(native, "c", "R", "d")
    assert _degree(native, "a") == 1  # fills one source only
    assert _allwarm(native) == 0


def test_readers_agree_with_a_recount_with_and_without_the_stamp(native):
    """KHopCount (graph 0), centrality and the NKG degree sum give the same answers warm and cold."""
    rows = [("n1", "R", "n2"), ("n1", "R", "n3"), ("n1", "S", "n3"), ("n2", "R", "n3"), ("n4", "S", "n1")]
    for s, p, o in rows:
        _put(native, s, p, o, graph=0)

    def khop(s, p=""):
        return int(native.classMethodValue("Graph.KG.TraversalKHop", "KHopCount", s, p))

    def snapshot():
        return {(s, p): (khop(s, p), _count(native, s, p, graph=0)) for s in ("n1", "n2", "n4", "zz") for p in ("", "R", "S")}

    cold = snapshot()
    assert all(a == b for a, b in cold.values()), cold

    _warm(native, graph=0)
    assert _allwarm(native, graph=0) == 1
    warm = snapshot()
    assert all(a == b for a, b in warm.values()), warm
    assert warm == cold

    _put(native, "n1", "R", "n9", graph=0)  # a write after the warm fill: stamp off, answers still exact
    assert _allwarm(native, graph=0) == 0
    after = snapshot()
    assert all(a == b for a, b in after.values()), after
    assert after[("n1", "R")][0] == cold[("n1", "R")][0] + 1
