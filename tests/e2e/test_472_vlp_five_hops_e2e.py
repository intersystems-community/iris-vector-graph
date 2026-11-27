"""4.7.2, live: a variable-length hop of five hops that binds `r` or tests a property completes.

An outside run: `CY_VLP_PATHS` failed with `<STRINGSTACK>` at five hops and 5,273 trails, with or without a qualifier
test on the relationship, well under the documented 23,800-leaf limit. The function builds every trail of a start as one
JSON string and IRIS refuses a string past 3,641,144 characters; each trail carried four columns (the node ids, the
types, the relationships with their properties, the edge keys), and a query reads one or none of them. The translator
now asks the function for the columns the SQL reads (`_prune_vlp_columns`), so a trail takes under half the text.

The graph is a layered one (tests/helpers/typed_graph_472.py `layered`): 6 nodes in each of 5 layers, every node tied
to every node of the next layer, `NEXT` edges with a `state` qualifier, one in seven stale. From the one start there
are 6 + 36 + 216 + 1,296 + 7,776 = 9,330 trails of one to five hops, 5.4 million characters of the full JSON, over the
limit; the unfiltered trails and the trails with no stale edge are both counted by a plain walk over the generated
edges (the oracle), which no query supplies.

Each answer is the exact set of far ends (and the trail counts), and equals what the same question asks as a fixed
chain of five hops and by predicate (the type, with no qualifier).

The namespace is `IVG_NAMESPACE`, an IVG470* scratch namespace with IVG set up (its SQL function CY_VLP_PATHS taking the
`cols` argument); the data carries the id prefix `T472V:` and is removed after.
"""

from __future__ import annotations

import time

import pytest

from tests.e2e import ns460alt as alt
from tests.helpers import typed_graph_472 as tg

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]

P = "T472V"
WIDTH, LAYERS = 6, 5


@pytest.fixture(scope="module")
def graph():
    nodes, edges, start = tg.layered(width=WIDTH, layers=LAYERS, prefix=P, stale_every=7)
    return nodes, edges, start


@pytest.fixture(scope="module")
def engine(graph):
    nodes, edges, _start = graph
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


def run(eng, q):
    res = eng.execute_cypher(q, raise_on_error=True)
    return res.rows


def not_live(q):
    return q.get("state") != "live"


def test_the_engine_finds_the_function_taking_cols(engine):
    # the probe the translator's pruning depends on, live: without it every test below would pass on 4.7.1's columns
    assert engine._vlp_cols_deployed is True
    t = engine.execute_cypher(f"MATCH (a {{node_id: '{P}:start'}})-[r*1..2]->(b) RETURN count(r)", raise_on_error=True)
    assert ", 'r')" in t.sql


def test_the_graph_is_over_the_string_limit(graph):
    # the oracle's own counts: the point of the shape, 9,330 trails at 5 hops, well over what one string holds
    _nodes, edges, start = graph
    ends, trails = tg.walk_ends(edges, start, 1, 5)
    assert trails == sum(WIDTH ** k for k in range(1, LAYERS + 1)) == 9330
    assert len(ends) == WIDTH * LAYERS


def test_qualifier_test_through_five_hops_gives_the_oracle_ends(engine, graph):
    _n, edges, start = graph
    want, trails = tg.walk_ends(edges, start, 1, 5, skip=not_live)
    assert 0 < trails < 9330 and want  # some trails survive the stale ones, not all
    rows = run(engine, f"MATCH (a {{node_id: '{start}'}})-[r*1..5]->(b) "
                       "WHERE all(x IN r WHERE x.state <> 'stale') RETURN DISTINCT b.node_id")
    assert {r[0] for r in rows} == want and len(rows) == len(want)


def test_the_filtered_trails_are_counted_not_only_their_ends(engine, graph):
    _n, edges, start = graph
    _ends, trails = tg.walk_ends(edges, start, 1, 5, skip=not_live)
    rows = run(engine, f"MATCH (a {{node_id: '{start}'}})-[r*1..5]->(b) "
                       "WHERE all(x IN r WHERE x.state <> 'stale') RETURN count(r)")
    assert rows == [[trails]]


@pytest.mark.parametrize("ret, check", [
    ("count(r)", "count"),
    ("count(*)", "count"),
    ("b.node_id", "rows"),
], ids=["count(r)", "count(*)", "end per trail"])
def test_binding_r_without_a_filter_through_five_hops(engine, graph, ret, check):
    _n, edges, start = graph
    ends, trails = tg.walk_ends(edges, start, 1, 5)
    rows = run(engine, f"MATCH (a {{node_id: '{start}'}})-[r*1..5]->(b) RETURN {ret}")
    if check == "count":
        assert rows == [[trails]]
    else:
        assert len(rows) == trails and {r[0] for r in rows} == ends


def test_distinct_ends_binding_r_through_five_hops(engine, graph):
    _n, edges, start = graph
    ends, _trails = tg.walk_ends(edges, start, 1, 5)
    rows = run(engine, f"MATCH (a {{node_id: '{start}'}})-[r*1..5]->(b) RETURN DISTINCT b.node_id")
    assert {r[0] for r in rows} == ends


def test_relationships_of_each_trail_through_five_hops_are_read(engine, graph):
    # RETURN r reads the relationships column itself: 9,330 rows, a list of one to five relationships each
    _n, edges, start = graph
    ends, trails = tg.walk_ends(edges, start, 1, 5, skip=not_live)
    rows = run(engine, f"MATCH (a {{node_id: '{start}'}})-[r*1..5]->(b) "
                       "WHERE all(x IN r WHERE x.state <> 'stale') RETURN b.node_id, r")
    assert len(rows) == trails
    assert {r[0] for r in rows} == ends
    for _end, rels in rows:
        assert 1 <= len(rels) <= 5 and all(rel["props"]["state"] == "live" and rel["type"] == "NEXT" for rel in rels)


def test_the_same_ends_as_a_fixed_chain_and_by_predicate(engine, graph):
    _n, edges, start = graph
    # a fixed chain of five hops, each hop tested by qualifier
    hops = "".join(f"-[r{i}:NEXT]->(m{i})" for i in range(1, 5)) + "-[r5:NEXT]->(b)"
    cond = " AND ".join(f"r{i}.state <> 'stale'" for i in range(1, 6))
    chain = {r[0] for r in run(engine, f"MATCH (a {{node_id: '{start}'}}){hops} WHERE {cond} RETURN DISTINCT b.node_id")}
    want5, _ = tg.walk_ends(edges, start, 5, 5, skip=not_live)
    assert chain == want5
    # by predicate (the type alone, every stale edge too): the trails of one to five hops end where the oracle says
    by_pred = {r[0] for r in run(engine, f"MATCH (a {{node_id: '{start}'}})-[:NEXT*1..5]->(b) RETURN DISTINCT b.node_id")}
    assert by_pred == tg.walk_ends(edges, start, 1, 5)[0]
    # and the qualifier test through exactly five hops equals the chain's
    five = {r[0] for r in run(engine, f"MATCH (a {{node_id: '{start}'}})-[r*5..5]->(b) "
                                      "WHERE all(x IN r WHERE x.state <> 'stale') RETURN DISTINCT b.node_id")}
    assert five == chain


def test_the_text_the_function_returns_stays_under_the_string_limit(engine, graph):
    """A budget on bytes per operation: IRIS refuses a string past 3,641,144 characters. 9,330 trails with every
    column are 5.4 million; with the relationships alone, under half of that and under the limit; with none, tiny."""
    import iris

    _n, _edges, start = graph
    native = iris.createIRIS(engine.conn)

    def call(cols):
        args = (start, "[]", "out", 1, 5, 1, "-", "Graph_KG.rdf_edges") + ((cols,) if cols else ())
        return native.classMethodValue("Graph.KG.funcCYVLPPATHS", "CYVLPPATHS", *args)

    rels, none = call("r"), call("x")
    assert len(rels) < 3_641_144 // 1.2 and len(none) < 500_000, (len(rels), len(none))
    with pytest.raises(Exception):
        call("")  # every column (the default): over the limit, as it was


def test_five_hops_cost_ceiling(engine, graph):
    """A ceiling on time per operation: the qualifier test over 9,330 trails (the walk reads each node's edges once)
    within 5 s warm best of three (live: about 1 s); the by-predicate form stays in its own class, under 0.5 s."""
    _n, _edges, start = graph
    q = (f"MATCH (a {{node_id: '{start}'}})-[r*1..5]->(b) "
         "WHERE all(x IN r WHERE x.state <> 'stale') RETURN DISTINCT b.node_id")
    pred = f"MATCH (a {{node_id: '{start}'}})-[:NEXT*1..5]->(b) RETURN DISTINCT b.node_id"
    for query, ceiling in ((q, 5.0), (pred, 0.5)):
        run(engine, query)
        best = min(_timed(engine, query) for _ in range(3))
        assert best < ceiling, (query, best)


def _timed(eng, q):
    t = time.perf_counter()
    run(eng, q)
    return time.perf_counter() - t
