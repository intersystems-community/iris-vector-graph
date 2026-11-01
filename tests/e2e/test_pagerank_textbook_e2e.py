"""Global PageRank (Graph.KG.PageRank.PageRankGlobalJson) is textbook PageRank.

Before 4.5.2 it was not: a node with no out-edges kept (1 - d) * rank and passed the rest nowhere, N counted only
nodes with out-edges, and a parallel edge counted in the degree but passed rank once. Its scores summed to about 0.57
on an 8-node path, and the reader gate (spec 236) failed arno's PageRank, which is the textbook one, against it.

The definition held here, over the default graph's adjacency ^KG("out", 0, s, p, o): the nodes are every source and
every target; a node passes d * rank to its out-edges in proportion to their multiplicity (a keyed triple counts its
"#" edges); a node with no out-edges spreads d * rank over all N nodes; every node gets (1 - d) / N. Scores sum to 1.

The reference is computed in Python from the globals as found, so whatever else the namespace's default graph holds
is part of the question rather than noise in it.
"""

from __future__ import annotations

import json
import os
import uuid

import pytest

from tests.fixtures.arno_reader_graphs import GRAPHS

SKIP_IRIS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS, reason="SKIP_IRIS_TESTS=true")]

ADJ = "Graph.KG.Adjacency"


@pytest.fixture
def adj(iris_connection):
    """Write edges into the default graph's adjacency under a fresh prefix, and take them out after."""
    import iris

    o = iris.createIRIS(iris_connection)
    prefix = f"prtb_{uuid.uuid4().hex[:6]}_"
    written = []

    def put(s, p, dst, parallel=1):
        """One edge through the writer, or a triple carrying ``parallel`` keyed edges in the storage shape spec 234
        gives it (``"#"`` = the count, one child per edge key; Graph.KG.Adj.Count reads it). PutKeyed learns the
        keys from the triple's SQL rows, which this test does not write."""
        s, dst = prefix + s, prefix + dst
        if parallel == 1:
            assert o.classMethodValue(ADJ, "Put", 0, s, p, dst, 1) == 1
        else:
            o.set(1, "^KG", "out", 0, s, p, dst)
            for key in range(parallel):
                o.set(1, "^KG", "out", 0, s, p, dst, key)
            o.set(parallel, "^KG", "out", 0, s, p, dst, "#")
        written.append((s, p, dst, parallel))

    yield o, prefix, put
    for s, p, dst, parallel in reversed(written):
        if parallel == 1:
            o.classMethodValue(ADJ, "Remove", 0, s, p, dst)
        else:
            o.kill("^KG", "out", 0, s, p, dst)


def _adjacency(o):
    """{src: {dst: multiplicity}} and the node set, read from ^KG("out", 0)."""
    out, nodes = {}, set()
    for s in o.iterator("^KG", "out", 0).subscripts():
        s = str(s)
        nodes.add(s)
        for p in o.iterator("^KG", "out", 0, s).subscripts():
            for dst in o.iterator("^KG", "out", 0, s, p).subscripts():
                k = int(o.get("^KG", "out", 0, s, p, dst, "#") or 1)
                out.setdefault(s, {})
                out[s][str(dst)] = out[s].get(str(dst), 0) + k
                nodes.add(str(dst))
    return out, nodes


def _pagerank(out, nodes, d=0.85, iterations=20):
    n = len(nodes)
    rank = {v: 1.0 / n for v in nodes}
    for _ in range(iterations):
        dangling = sum(r for v, r in rank.items() if v not in out)
        new = {v: (1 - d) / n + d * dangling / n for v in nodes}
        for s, targets in out.items():
            total = sum(targets.values())
            for dst, k in targets.items():
                new[dst] += d * rank[s] * k / total
        rank = new
    return rank


def _owner(o, d=0.85, iterations=20):
    return {r["id"]: float(r["score"]) for r in json.loads(str(o.classMethodValue(
        "Graph.KG.PageRank", "PageRankGlobalJson", str(d), str(iterations))))}


def _assert_close(got, want, rel=1e-9):
    assert set(got) == set(want), (sorted(set(want) - set(got))[:5], sorted(set(got) - set(want))[:5])
    bad = {v: (got[v], want[v]) for v in want if abs(got[v] - want[v]) > rel * max(abs(want[v]), 1e-12)}
    assert not bad, dict(list(bad.items())[:5])


@pytest.mark.parametrize("name", list(GRAPHS))
def test_global_pagerank_is_textbook_on_the_reader_gate_graphs(adj, name):
    o, _, put = adj
    for s, p, dst in GRAPHS[name]["edges"]:
        put(s, p, dst)
    out, nodes = _adjacency(o)
    _assert_close(_owner(o), _pagerank(out, nodes))


def test_a_node_with_no_out_edges_passes_its_rank_on_and_the_scores_sum_to_one(adj):
    o, prefix, put = adj
    for i in range(7):
        put(f"p{i}", "NEXT", f"p{i + 1}")
    got = _owner(o)
    assert abs(sum(got.values()) - 1.0) < 1e-9, sum(got.values())
    # the end of the path collects the most: 0.1877 on its own, not 0.1223
    ours = {k[len(prefix):]: v for k, v in got.items() if k.startswith(prefix)}
    assert max(ours, key=ours.get) == "p7"


def test_a_parallel_edge_passes_rank_in_proportion_to_its_multiplicity(adj):
    o, prefix, put = adj
    put("s", "R", "a", parallel=3)
    put("s", "R", "b")
    out, nodes = _adjacency(o)
    assert out[prefix + "s"] == {prefix + "a": 3, prefix + "b": 1}
    got = _owner(o)
    _assert_close(got, _pagerank(out, nodes))
    # a and b have no other in-edges: the difference between them is s's share, 3 to 1
    base = got[prefix + "s"]  # s has no in-edges: it holds the base every node gets
    assert abs((got[prefix + "a"] - base) / (got[prefix + "b"] - base) - 3.0) < 1e-6


def test_it_converges_to_networkx_pagerank(adj):
    import networkx as nx

    o, _, put = adj
    for s, p, dst in GRAPHS["multi_component"]["edges"] + GRAPHS["star"]["edges"]:
        put(s, p, dst)
    out, nodes = _adjacency(o)
    g = nx.MultiDiGraph()
    g.add_nodes_from(nodes)
    for s, targets in out.items():
        for dst, k in targets.items():
            g.add_edges_from([(s, dst)] * k)
    _assert_close(_owner(o, iterations=200), nx.pagerank(g, alpha=0.85, tol=1e-14, max_iter=1000), rel=1e-6)


def test_with_the_default_graph_in_multigraph_mode_the_library_is_not_asked(adj):
    """arno-callout reads ^NKG's export, which lists a keyed triple once: it would weigh a parallel edge 1 where the
    Owner weighs it by its "#" count. So in multigraph mode the native entry refuses and the Owner answers."""
    o, _, _ = adj
    assert int(o.classMethodValue("Graph.KG.GraphMode", "IsMulti", "")) == 0
    o.set("multi", "^IVG.GraphMode", 0)
    try:
        assert int(o.classMethodValue("Graph.KG.GraphMode", "IsMulti", "")) == 1
        with pytest.raises(Exception, match="multigraph"):
            o.classMethodValue("Graph.KG.ArnoAccel", "PageRankNativeJson", "0.85", "20")
    finally:
        o.kill("^IVG.GraphMode", 0)
    assert int(o.classMethodValue("Graph.KG.GraphMode", "IsMulti", "")) == 0
