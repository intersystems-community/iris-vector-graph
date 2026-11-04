"""4.6.0 R249: ``count_nodes``, ``get_node_count``, ``get_edge_count`` and ``get_label_distribution`` count one graph,
the default graph when ``graph=`` is omitted (R219's rule); ``get_bucket_groups`` answers exactly its window (R217's
rule for ``get_temporal_aggregate``).

Counts, by hand: graph ``g1`` (unique to the run) gets 3 nodes labelled ``L249_<run>`` and 2 edges ``P249_<run>``,
graph ``g2`` 2 nodes and 1 edge, and the default graph 1 node and no edge, all with that label and predicate. So
``count_nodes(label)`` and ``get_node_count(label)`` are 1 (default), 3 (g1), 2 (g2); ``get_edge_count(predicate)`` 0,
2, 1; ``get_label_distribution()[label]`` 1, 3, 2. Before, each counted every graph: 6 nodes and 3 edges. Unlabelled
counts move by exactly what each graph got (1, 3, 2 nodes; 0, 2, 1 edges).

Bucket groups, by hand (T a multiple of 300): ``ann -CALLED->`` at T+5 (weight 2), T+100 (4), T+299 (9), T+300 (1),
T+650 (5), and ``ben -CALLED->`` at T+10 (7), as R217's test:
- ``[T+100, T+300]``: ann T+100, T+299, T+300 -> count 3, sum 14.0, min 1.0, max 9.0; ben has none in the window
  (before: ann 4 and 16.0, and ben 1: the T+5 and T+10 events share T+100's bucket);
- ``[T, T+1000]``: ann count 5, sum 21.0; ben count 1, sum 7.0;
- ``[T+301, T+649]``: no group.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.e2e]

T = 1_900_000_200


@pytest.fixture(scope="module")
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    e = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    g1, g2 = f"r249a_{run}", f"r249b_{run}"
    label, pred = f"L249_{run}", f"P249_{run}"
    assert T % 300 == 0
    ann, ben = f"r249_{run}_ann", f"r249_{run}_ben"
    ev = [
        (ann, f"r249_{run}_t1", T + 5, 2.0),
        (ann, f"r249_{run}_t1", T + 100, 4.0),
        (ann, f"r249_{run}_t2", T + 299, 9.0),
        (ann, f"r249_{run}_t3", T + 300, 1.0),
        (ann, f"r249_{run}_t1", T + 650, 5.0),
        (ben, ann, T + 10, 7.0),
    ]
    pfx = f"r249_{run}_"
    assert int(e.bulk_create_edges_temporal([{"s": s, "p": "CALLED", "o": o, "ts": ts, "w": w} for s, o, ts, w in ev])) == 6
    # the temporal writes mirror their endpoints into the default graph: counted before the counted writes
    before = {gr: (e.count_nodes(graph=gr), e.get_node_count(graph=gr), e.get_edge_count(graph=gr)) for gr in (None, g1, g2)}
    ids = {k: f"r249_{run}_{k}" for k in ("a", "b", "c", "x")}
    for k in ("a", "b", "c"):
        assert e.create_node(ids[k], labels=[label], graph=g1)
    for s, o in (("a", "b"), ("b", "c")):
        assert e.create_edge(ids[s], pred, ids[o], graph=g1)
    for k in ("a", "b"):
        assert e.create_node(ids[k], labels=[label], graph=g2)
    assert e.create_edge(ids["a"], pred, ids["b"], graph=g2)
    assert e.create_node(ids["x"], labels=[label])

    yield e, (g1, g2, label, pred, before), (ann, ben, pfx)
    for graph in (g1, g2):
        try:
            e.erase_graph(graph)
        except Exception:
            pass
    try:
        e.delete_node(ids["x"], graph=None)
    except Exception:
        pass


@pytest.mark.parametrize("which, nodes, edges", [(None, 1, 0), ("g1", 3, 2), ("g2", 2, 1)])
def test_counts_by_label_and_predicate_count_one_graph(g, which, nodes, edges):
    e, (g1, g2, label, pred, _), _ = g
    graph = {"g1": g1, "g2": g2}.get(which)
    kw = {} if graph is None else {"graph": graph}
    assert e.count_nodes(label, **kw) == nodes
    assert e.get_node_count(label, **kw) == nodes
    assert e.get_edge_count(pred, **kw) == edges
    assert e.get_label_distribution(**kw).get(label, 0) == nodes


@pytest.mark.parametrize("which, nodes, edges", [(None, 1, 0), ("g1", 3, 2), ("g2", 2, 1)])
def test_unlabelled_counts_grew_by_each_graphs_own_writes(g, which, nodes, edges):
    e, (g1, g2, _, _, before), _ = g
    graph = {"g1": g1, "g2": g2}.get(which)
    b_count, b_nodes, b_edges = before[graph]
    kw = {} if graph is None else {"graph": graph}
    assert e.count_nodes(**kw) - b_count == nodes
    assert e.get_node_count(**kw) - b_nodes == nodes
    assert e.get_edge_count(**kw) - b_edges == edges


def _groups(e, pfx, lo, hi):
    out = {}
    for row in e.get_bucket_groups("CALLED", T + lo, T + hi, source_prefix=pfx):
        out[row["source"]] = (row["count"], row["sum"], row["min"], row["max"])
    return out


def test_bucket_groups_answer_exactly_the_window(g):
    e, _, (ann, ben, pfx) = g
    assert _groups(e, pfx, 100, 300) == {ann: (3, 14.0, 1.0, 9.0)}


def test_bucket_groups_over_whole_buckets(g):
    e, _, (ann, ben, pfx) = g
    assert _groups(e, pfx, 0, 1000) == {ann: (5, 21.0, 1.0, 9.0), ben: (1, 7.0, 7.0, 7.0)}


def test_bucket_groups_of_a_window_with_no_event(g):
    e, _, (_, _, pfx) = g
    assert _groups(e, pfx, 301, 649) == {}
