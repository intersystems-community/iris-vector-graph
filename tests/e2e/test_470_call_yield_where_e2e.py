"""4.7.0 row 33, live: `CALL ... YIELD ... WHERE` filters the yielded rows, on each path a CALL takes.

Measured on 4.6.5 (f1a442e7, IRIS 2026.3, IVG470CY): every query below raised CypherParseError
(`Expected EOF, got WHERE`).

The graph, default graph, label T470Y: nodes a, b, c, d; edges a->c, b->c, d->c of type R470Y, weight 1.5 each;
768-dimension embeddings a = e0, b = e1, c = 0.6 e0 + 0.8 e1, d none. The known answers, from networkx or by hand:
- out-degree (ivg.degreeCentrality's `degree`): a, b, d 1; c 0;
- personalized PageRank from a (networkx, alpha 0.85): a 0.541, c 0.459, b and d 0, so `score > 0.5` is a alone;
- PageRank (gds.pageRank.stream; networkx over the namespace's default graph, read from its tables, so the case holds
  in a namespace with other nodes): c above the three leaves, which tie; the threshold is the midpoint;
- the cheapest a -> c path costs 1.5;
- vector search for e0: a scores 1, c 0.6, b 0, so `score > 0.5` is a and c;
- db.labels: T470Y and T470YB (a second label, so that an answer of every label shows).

The paths: the translator's CTE procedures (degreeCentrality, ppr), the two the translator answers that also have an
engine handler (vector.search, shortestPath.weighted: with a WHERE the translator answers them), and the engine
handlers (gds.pageRank.stream, db.labels), each with a RETURN and standalone. The default route tries the native
executor first, which declines every CALL; both routes are run all the same. An in-query CALL (`MATCH ... CALL ...`)
is not here: IVG fails it with or without a WHERE (SQLCODE -23), so the unit tests cover its parse.
"""

from __future__ import annotations

import warnings

import pytest

from tests.e2e import ns463

pytestmark = [pytest.mark.e2e, *ns463.requires_namespace]

LABEL, REL, P = "T470Y", "R470Y", "t470y:"
IDS = [P + n for n in "abcd"]
DIM = 768


def _vec(**at):
    v = [0.0] * DIM
    for i, x in at.items():
        v[int(i[1:])] = x
    return v


EMB = {P + "a": _vec(e0=1.0), P + "b": _vec(e1=1.0), P + "c": _vec(e0=0.6, e1=0.8)}


def _rebuild_nkg(e):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        e.rebuild_nkg()


@pytest.fixture(scope="module")
def engines():
    conn = ns463.connect()
    engs = ns463.engines(conn)
    e = engs["sql"]
    for lbl in (LABEL, LABEL + "B"):
        ns463.rows(e, f"MATCH (n:{lbl}) DETACH DELETE n")
    for nid in IDS:
        assert e.create_node(nid, labels=[LABEL])
    assert e.create_node("t470yb:z", labels=[LABEL + "B"])
    for s in "abd":
        assert e.create_edge(P + s, REL, P + "c", weight=1.5)
    for nid, v in EMB.items():
        e.store_embedding(nid, v)
    conn.commit()
    _rebuild_nkg(e)
    yield engs
    for lbl in (LABEL, LABEL + "B"):
        ns463.rows(e, f"MATCH (n:{lbl}) DETACH DELETE n")
    conn.commit()
    _rebuild_nkg(e)
    conn.close()


def _rows(engines, route, q, params=None):
    return ns463.rows(engines[route], q, params)


def _ids(rows):
    return sorted(r[0] for r in rows)


@pytest.mark.parametrize("route", ns463.ROUTES)
class TestTranslatorProcedures:
    def test_degree_centrality_with_return(self, engines, route):
        q = (f"CALL ivg.degreeCentrality({{}}) YIELD node, degree WHERE degree = 0 AND node.id STARTS WITH '{P}' "
             "RETURN node.id, degree")
        assert ns463.typed(_rows(engines, route, q)) == [[(P + "c", "str"), (0, "int")]]
        q = (f"CALL ivg.degreeCentrality({{}}) YIELD node, degree WHERE degree = 1 AND node.id STARTS WITH '{P}' "
             "RETURN node.id ORDER BY node.id")
        assert _rows(engines, route, q) == [[P + "a"], [P + "b"], [P + "d"]]

    def test_a_threshold_above_every_degree_answers_no_rows(self, engines, route):
        q = "CALL ivg.degreeCentrality({}) YIELD node, degree WHERE degree > 1000000 RETURN node.id"
        assert _rows(engines, route, q) == []

    def test_aliases(self, engines, route):
        q = (f"CALL ivg.degreeCentrality({{}}) YIELD node AS n, degree AS d WHERE d = 1 AND n.id STARTS WITH '{P}' "
             "RETURN n.id, d ORDER BY n.id")
        assert _rows(engines, route, q) == [[P + "a", 1], [P + "b", 1], [P + "d", 1]]

    def test_standalone(self, engines, route):
        """No RETURN: the yielded names, a node as its ID, as `CALL ... YIELD node, degree` answers them."""
        q = f"CALL ivg.degreeCentrality({{}}) YIELD node, degree WHERE degree = 0 AND node.id STARTS WITH '{P}'"
        r = engines[route].execute_cypher(q, {}, raise_on_error=True)
        assert r.columns == ["node", "degree"] and r.rows == [[P + "c", 0]]
        q = f"CALL ivg.degreeCentrality({{}}) YIELD node AS n, degree AS d WHERE d = 0 AND n.id STARTS WITH '{P}'"
        r = engines[route].execute_cypher(q, {}, raise_on_error=True)
        assert r.columns == ["n", "d"] and r.rows == [[P + "c", 0]]

    def test_where_then_with(self, engines, route):
        q = (f"CALL ivg.degreeCentrality({{}}) YIELD node, degree WHERE degree = 1 AND node.id STARTS WITH '{P}' "
             f"WITH node WHERE node.id <> '{P}b' RETURN node.id ORDER BY node.id")
        assert _rows(engines, route, q) == [[P + "a"], [P + "d"]]

    def test_personalized_pagerank(self, engines, route):
        q = f"CALL ivg.ppr(['{P}a']) YIELD node, score WHERE score > 0.5 RETURN node.id"
        assert _rows(engines, route, q) == [[P + "a"]]
        q = f"CALL ivg.ppr(['{P}a']) YIELD node, score WHERE score > $t RETURN node.id ORDER BY node.id"
        assert _rows(engines, route, q, {"t": 0.4}) == [[P + "a"], [P + "c"]]
        assert _rows(engines, route, q, {"t": 1.0}) == []


@pytest.mark.parametrize("route", ns463.ROUTES)
class TestProceduresWithAHandlerAndATranslation:
    def test_vector_search(self, engines, route):
        q = (f"CALL ivg.vector.search('{LABEL}', 'emb', $v, 3) YIELD node, score WHERE score > 0.5 "
             "RETURN node.id ORDER BY node.id")
        assert _rows(engines, route, q, {"v": _vec(e0=1.0)}) == [[P + "a"], [P + "c"]]
        q = f"CALL ivg.vector.search('{LABEL}', 'emb', $v, 3) YIELD node, score WHERE score > 1.5 RETURN node.id"
        assert _rows(engines, route, q, {"v": _vec(e0=1.0)}) == []

    def test_weighted_shortest_path(self, engines, route):
        q = f"CALL ivg.shortestPath.weighted('{P}a', '{P}c') YIELD path, totalCost WHERE totalCost < 2 RETURN totalCost"
        assert _rows(engines, route, q) == [[1.5]]
        q = f"CALL ivg.shortestPath.weighted('{P}a', '{P}c') YIELD path, totalCost WHERE totalCost > 2 RETURN totalCost"
        assert _rows(engines, route, q) == []


def _networkx_pagerank(engine):
    """PageRank of the namespace's default graph, from its tables, by networkx (alpha 0.85, as kg_PAGERANK)."""
    nx = pytest.importorskip("networkx")
    cur = engine.conn.cursor()
    schema = engine._ivg_schema
    cur.execute(f"SELECT node_id FROM {schema}.nodes WHERE graph_id IS NULL OR graph_id = ''")
    nodes = [r[0] for r in cur.fetchall()]
    cur.execute(f"SELECT s, o_id FROM {schema}.rdf_edges WHERE graph_id IS NULL OR graph_id = ''")
    G = nx.DiGraph()
    G.add_nodes_from(nodes)
    G.add_edges_from((s, o) for s, o in cur.fetchall())
    return nx.pagerank(G, alpha=0.85)


@pytest.mark.parametrize("route", ns463.ROUTES)
class TestHandlerProcedures:
    def test_pagerank(self, engines, route):
        pr = _networkx_pagerank(engines["sql"])
        hub, leaf = pr[P + "c"], max(pr[P + n] for n in "abd")
        assert hub > leaf
        t = (hub + leaf) / 2
        q = (f"USE GRAPH '' CALL gds.pageRank.stream('g') YIELD nodeId, score "
             f"WHERE score > $t AND nodeId IN {IDS!r} RETURN nodeId")
        assert _rows(engines, route, q, {"t": t}) == [[P + "c"]]
        q = f"USE GRAPH '' CALL gds.pageRank.stream('g') YIELD nodeId, score WHERE score > {max(pr.values()) + 1} RETURN nodeId"
        assert _rows(engines, route, q) == []

    def test_pagerank_standalone(self, engines, route):
        pr = _networkx_pagerank(engines["sql"])
        t = (pr[P + "c"] + max(pr[P + n] for n in "abd")) / 2
        q = f"USE GRAPH '' CALL gds.pageRank.stream('g') YIELD nodeId, score WHERE score > $t AND nodeId IN {IDS!r}"
        r = engines[route].execute_cypher(q, {"t": t}, raise_on_error=True)
        assert r.columns == ["nodeId", "score"]
        assert [row[0] for row in r.rows] == [P + "c"]
        assert r.rows[0][1] == pytest.approx(pr[P + "c"], abs=1e-3)

    def test_labels(self, engines, route):
        assert _rows(engines, route, "CALL db.labels() YIELD label WHERE label = 'T470Y' RETURN label") == [[LABEL]]
        assert _rows(engines, route, "CALL db.labels() YIELD label WHERE label = $l", {"l": LABEL}) == [[LABEL]]
        assert _rows(engines, route, "CALL db.labels() YIELD label AS l WHERE l = 'T470Y-none' RETURN l") == []


#: Before 4.7.0 each of these answered every label: the handler answered the CALL, and the WITH, MATCH or RETURN
#: expression after it was dropped. Each now answers the filter's labels, or raises naming the clause.
FILTERED = [
    ("CALL db.labels() YIELD label WITH label WHERE label = 'T470YB' RETURN label", [[LABEL + "B"]]),
    ("CALL db.labels() YIELD label WHERE label IN ['T470Y', 'T470YB'] WITH label WHERE label <> 'T470Y' "
     "RETURN label", [[LABEL + "B"]]),
    ("CALL db.labels() YIELD label AS l WITH l WHERE l = 'T470Y' RETURN l", [[LABEL]]),
]
REFUSED = [
    ("CALL db.labels() YIELD label MATCH (n:T470YB) RETURN label", "MATCH"),
    ("CALL db.labels() YIELD label WITH label ORDER BY label LIMIT 1 RETURN label", "WITH"),
    ("CALL db.labels() YIELD label RETURN count(label) AS c", "RETURN"),
    ("CALL db.labels() YIELD label WITH label WHERE label STARTS WITH 'T470YB' RETURN label", "STARTS"),
]


@pytest.mark.parametrize("route", ns463.ROUTES)
class TestHandlerClausesAreAppliedOrRefused:
    @pytest.mark.parametrize("q, want", FILTERED)
    def test_a_with_filter_after_a_handler_is_applied(self, engines, route, q, want):
        assert _rows(engines, route, q) == want

    @pytest.mark.parametrize("q, clause", REFUSED)
    def test_a_clause_a_handler_cannot_apply_raises(self, engines, route, q, clause):
        with pytest.raises(ValueError, match=clause):
            _rows(engines, route, q)

    def test_no_shape_answers_every_label(self, engines, route):
        every = sorted(r[0] for r in _rows(engines, route, "CALL db.labels() YIELD label RETURN label"))
        assert {LABEL, LABEL + "B"} <= set(every)
        for q, _ in FILTERED:
            assert sorted(r[0] for r in _rows(engines, route, q)) != every, q
