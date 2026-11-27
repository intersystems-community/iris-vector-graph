"""4.7.2, live: an OR of labels on the far end of a hop (the synthetic typed graph of tests/helpers/typed_graph_472.py).

An outside run measured `MATCH (a:Doc)-[:REFERS]-(b) WHERE b:Req OR b:Test` at 2.0 s warm, against 0.027 s without the
label test and 0.04 s for the two directed queries, all answering the same 450 rows. Here (17,000 nodes, 450 REFERS
edges between documents and requirements or tests, an undirected hop with no anchor): 0.16 s against 0.008 s before the
fix. The OR of one `EXISTS` per label planned as a scan of the label table per row.

The pattern form `(b:Req|Test)` on a hop's far end ANDed the labels (no node holds both), so it answered nothing.

The expected rows come from the generated edge list (the oracle), never from a query. Two nodes are added to it: one
holding both labels (it must come back once, not once per label) and a document whose only REFERS neighbour is a spec
(an OPTIONAL MATCH keeps its row, with nulls).

The namespace is `IVG_NAMESPACE`, an IVG470* scratch namespace with IVG set up; the data carries the id prefix `T472:`
and is removed after.
"""

from __future__ import annotations

import time
from collections import Counter

import pytest

from tests.e2e import ns460alt as alt
from tests.helpers import typed_graph_472 as tg

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]

P = "T472:"
BOTH, LONELY, SPEC1 = f"{P}BOTH", f"{P}DOC:lonely", f"{P}SPEC:only"


def best_of(eng, q, n=5):
    """(rows, warm best of n seconds, cold first seconds)"""
    t = time.perf_counter()
    res = eng.execute_cypher(q, raise_on_error=True)
    cold = time.perf_counter() - t
    best = cold
    for _ in range(n):
        t = time.perf_counter()
        res = eng.execute_cypher(q, raise_on_error=True)
        best = min(best, time.perf_counter() - t)
    return res.rows, best, cold


@pytest.fixture(scope="module")
def data():
    nodes, edges = tg.rows(prefix=P)
    nodes += [(BOTH, {"namespace": ["Req", "Test"], "name": "both"}),
              (LONELY, {"namespace": "Doc", "name": "lonely"}),
              (SPEC1, {"namespace": "Spec", "name": "only"})]
    edges += [(f"{P}DOC:00001", "REFERS", BOTH, None), (BOTH, "REFERS", f"{P}DOC:00002", None),
              (LONELY, "REFERS", SPEC1, None)]
    return nodes, edges


@pytest.fixture(scope="module")
def engine(data):
    nodes, edges = data
    conn = alt.connect()
    import iris

    from iris_vector_graph import IRISGraphEngine

    schema = iris.createIRIS(conn).get("^IVG.Deploy", "schema") or "Graph_KG"
    if schema != "Graph_KG":
        conn.close()
        pytest.skip("the synthetic graph loader writes the Graph_KG schema")
    tg.unload(conn, P)
    eng = IRISGraphEngine(conn, namespace=alt.NS, schema_prefix=schema, native_cypher=False)
    tg.load(conn, nodes, edges)
    eng.sync()
    yield eng
    try:
        tg.unload(conn, P)
    finally:
        conn.close()


def expected_pairs(data, directed=None):
    """(doc, far) per REFERS edge between a Doc and a Req/Test node: `directed` "out" (doc is the source), "in", or
    None for either direction (an undirected hop)."""
    nodes, edges = data
    label = {nid: (a["namespace"] if isinstance(a["namespace"], list) else [a["namespace"]]) for nid, a in nodes}
    def far_ok(n):
        return bool({"Req", "Test"} & set(label[n]))

    out = []
    for s, p, o, _q in edges:
        if p != "REFERS":
            continue
        if directed in (None, "out") and "Doc" in label[s] and far_ok(o):
            out.append((s, o))
        if directed in (None, "in") and "Doc" in label[o] and far_ok(s):
            out.append((o, s))
    return sorted(out)


Q_WHERE = "MATCH (a:Doc)-[:REFERS]-(b) WHERE b:Req OR b:Test RETURN a.node_id, b.node_id"
Q_PATTERN = "MATCH (a:Doc)-[:REFERS]-(b:Req|Test) RETURN a.node_id, b.node_id"
Q_PLAIN = "MATCH (a:Doc)-[:REFERS]-(b) RETURN a.node_id, b.node_id"


def pairs(rows):
    return sorted((r[0], r[1]) for r in rows)


def test_oracle_is_the_reporters_shape(data):
    # the synthetic graph has the reporter's 450 REFERS rows between documents and requirements or tests, plus ours
    assert len(expected_pairs(data)) == 450 + 2


@pytest.mark.parametrize("q", [Q_WHERE, Q_PATTERN], ids=["where-or", "pattern-or"])
def test_undirected_far_end_label_or_answers_the_oracle(engine, data, q):
    rows, _warm, _cold = best_of(engine, q, n=1)
    want = expected_pairs(data)
    assert pairs(rows) == want
    # one row per edge: the node holding both labels comes back once per edge, not once per label
    assert Counter(pairs(rows))[(f"{P}DOC:00001", BOTH)] == 1
    assert Counter(pairs(rows))[(f"{P}DOC:00002", BOTH)] == 1


@pytest.mark.parametrize("direction, q", [
    ("out", "MATCH (a:Doc)-[:REFERS]->(b) WHERE b:Req OR b:Test RETURN a.node_id, b.node_id"),
    ("out", "MATCH (a:Doc)-[:REFERS]->(b:Req|Test) RETURN a.node_id, b.node_id"),
    ("in", "MATCH (a:Doc)<-[:REFERS]-(b) WHERE b:Req OR b:Test RETURN a.node_id, b.node_id"),
    ("in", "MATCH (a:Doc)<-[:REFERS]-(b:Req|Test) RETURN a.node_id, b.node_id"),
])
def test_directed_far_end_label_or_answers_the_oracle(engine, data, direction, q):
    rows, _w, _c = best_of(engine, q, n=1)
    assert pairs(rows) == expected_pairs(data, direction)


def test_three_label_or_and_a_label_none_holds(engine, data):
    rows, _w, _c = best_of(engine, "MATCH (a:Doc)-[:REFERS]-(b) WHERE b:Req OR b:Test OR b:NoSuch RETURN a.node_id, b.node_id", n=1)
    assert pairs(rows) == expected_pairs(data)
    rows, _w, _c = best_of(engine, "MATCH (a:Doc)-[:REFERS]-(b:NoSuch|Nope) RETURN a.node_id, b.node_id", n=1)
    assert rows == []


def test_optional_far_end_label_or_keeps_a_row_of_nulls(engine):
    q = (f"MATCH (a:Doc) WHERE a.node_id IN ['{LONELY}', '{P}DOC:00001'] "
         "OPTIONAL MATCH (a)-[:REFERS]->(b:Req|Test) RETURN a.node_id, b.node_id")
    rows, _w, _c = best_of(engine, q, n=1)
    got = pairs([[r[0], r[1] or ""] for r in rows])
    # the lonely document's only neighbour is a spec: one row of nulls. The other: its REFERS rows to a Req or Test
    assert (LONELY, "") in got and len([g for g in got if g[0] == LONELY]) == 1
    assert (f"{P}DOC:00001", BOTH) in got


def test_standalone_label_or_answers_a_node_holding_two_of_the_labels_once(engine, data):
    nodes, _edges = data
    want = sorted(nid for nid, a in nodes
                  if {"Req", "Test"} & set(a["namespace"] if isinstance(a["namespace"], list) else [a["namespace"]]))
    rows, _w, _c = best_of(engine, f"MATCH (b:Req|Test) WHERE b.node_id STARTS WITH '{P}' RETURN b.node_id", n=1)
    assert sorted(r[0] for r in rows) == want
    assert [r[0] for r in rows].count(BOTH) == 1


def test_undirected_label_or_costs_no_more_than_the_hop_without_it(engine):
    """The ceiling: warm best of five within 0.05 s (or three times) of the same hop with no label test. Before the
    fix: 0.16 s against 0.008 s on this graph (the outside run: 2.0 s against 0.027 s)."""
    _r, plain, _c = best_of(engine, Q_PLAIN)
    for q in (Q_WHERE, Q_PATTERN):
        _r, warm, _c = best_of(engine, q)
        assert warm <= max(3 * plain, plain + 0.05), (q, warm, plain)
        assert warm <= 2.0
