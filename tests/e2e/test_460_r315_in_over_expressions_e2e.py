"""4.6.0 R315, live: `x IN <list>` over a stored list, a WITH alias, a parameter or a collect() answers by openCypher.

By hand. Servers web1 ['prod', 'web'], web2 ['web'], db1 ['prod', 'db'], none1 [] (kind 'web' on web1 and db1), each
written by a different writer (bulk_create_nodes, create_node, Cypher CREATE, Cypher SET); a box b1 nums [1, 2, 3];
a relationship web1 -[:T315 {name: 'r1', tags: ['hot', 'new']}]-> db1, and web2 -[:T315 {name: 'r2', tags: ['cold']}]->
db1. openCypher: x IN list is true when an element equals x; null when none does and x or an element is null; false
otherwise (an empty list: false, x null or not). '2' IN [1, 2, 3] is false (a string is no number).

On d004e79e `WHERE 'web' IN s.tags` answered no rows with SQLCODE -1, and `WITH s, s.tags AS t WHERE 'web' IN t`
raised `Undefined variable: t`.

The namespace is `IVG_NAMESPACE` (an IVG460* scratch namespace with IVG set up); each case runs on the default
graph's data (labels S315D, B315D, type T315D) and again under `USE GRAPH 'g315'` on the same data written there with
labels S315N, B315N and type T315N (a read with no USE GRAPH reads every graph, so the two sets are told apart by label).
"""

from __future__ import annotations

import pytest

from tests.e2e import ns460alt as alt

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]

GRAPHS = [None, "g315"]


def _use(graph):
    return f"USE GRAPH '{graph}' " if graph else ""


def _q(query, graph):
    suffix = "N" if graph else "D"
    return _use(graph) + query.replace("S315", "S315" + suffix).replace("B315", "B315" + suffix).replace(
        "T315", "T315" + suffix)


@pytest.fixture(scope="module")
def engine():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = alt.connect()
    schema = iris.createIRIS(conn).get("^IVG.Deploy", "schema") or "Graph_KG"
    eng = IRISGraphEngine(conn, namespace=alt.NS, schema_prefix=schema)

    def clear():
        for g in GRAPHS:
            eng.execute_cypher(_q("MATCH (n) WHERE n:S315 OR n:B315 DETACH DELETE n", g), raise_on_error=True)

    clear()
    for g in GRAPHS:
        eng.bulk_create_nodes([{"id": f"web1{g or ''}", "labels": ["S315" + ("N" if g else "D")],
                                "properties": {"name": "web1", "kind": "web", "tags": ["prod", "web"]}}], graph=g)
        eng.create_node(f"web2{g or ''}", labels=["S315" + ("N" if g else "D")], properties={"name": "web2", "tags": ["web"]}, graph=g)
        eng.execute_cypher(_q("CREATE (:S315 {id: $i, name: 'db1', kind: 'web', tags: ['prod', 'db']})", g),
                           {"i": f"db1{g or ''}"}, raise_on_error=True)
        eng.execute_cypher(_q("CREATE (:S315 {id: $i, name: 'none1'})", g), {"i": f"none1{g or ''}"},
                           raise_on_error=True)
        eng.execute_cypher(_q("MATCH (s:S315 {name: 'none1'}) SET s.tags = []", g), raise_on_error=True)
        eng.execute_cypher(_q("CREATE (:B315 {name: 'b1', nums: [1, 2, 3]})", g), raise_on_error=True)
        eng.execute_cypher(_q("MATCH (a:S315 {name: 'web1'}), (b:S315 {name: 'db1'}) "
                           "CREATE (a)-[:T315 {name: 'r1', tags: ['hot', 'new']}]->(b)", g), raise_on_error=True)
        eng.execute_cypher(_q("MATCH (a:S315 {name: 'web2'}), (b:S315 {name: 'db1'}) "
                           "CREATE (a)-[:T315 {name: 'r2', tags: ['cold']}]->(b)", g), raise_on_error=True)
    yield eng
    try:
        clear()
    finally:
        conn.close()


CASES = [
    # (query, params, rows)
    ("MATCH (s:S315) WHERE 'web' IN s.tags RETURN s.name ORDER BY s.name", {}, [["web1"], ["web2"]]),
    ("MATCH (s) WHERE s:S315 AND 'web' IN s.tags RETURN s.name ORDER BY s.name", {}, [["web1"], ["web2"]]),
    ("MATCH (s:S315) WHERE NOT 'web' IN s.tags RETURN s.name ORDER BY s.name", {}, [["db1"], ["none1"]]),
    ("MATCH (s:S315) RETURN s.name, 'web' IN s.tags AS b ORDER BY s.name",
     {}, [["db1", False], ["none1", False], ["web1", True], ["web2", True]]),
    ("MATCH (s:S315) WHERE $x IN s.tags RETURN s.name ORDER BY s.name", {"x": "prod"}, [["db1"], ["web1"]]),
    ("MATCH (s:S315) WHERE s.kind IN s.tags RETURN s.name ORDER BY s.name", {}, [["web1"]]),
    ("MATCH (s:S315) RETURN s.name, null IN s.tags AS b ORDER BY s.name",
     {}, [["db1", None], ["none1", False], ["web1", None], ["web2", None]]),
    ("MATCH (s:S315) RETURN s.name, $x IN s.tags AS b ORDER BY s.name",
     {"x": None}, [["db1", None], ["none1", False], ["web1", None], ["web2", None]]),
    ("MATCH (b:B315) RETURN 2 IN b.nums AS a, '2' IN b.nums AS b, 9 IN b.nums AS c", {}, [[True, False, False]]),
    ("MATCH ()-[r:T315]->() WHERE 'hot' IN r.tags RETURN r.name", {}, [["r1"]]),
    ("MATCH ()-[r:T315]->() RETURN r.name, 'hot' IN r.tags AS b ORDER BY r.name", {}, [["r1", True], ["r2", False]]),
    ("MATCH (s:S315) WITH s, s.tags AS t WHERE 'web' IN t RETURN s.name ORDER BY s.name", {}, [["web1"], ["web2"]]),
    ("MATCH (s:S315) WITH s, s.tags AS t RETURN s.name, 'db' IN t AS b ORDER BY s.name",
     {}, [["db1", True], ["none1", False], ["web1", False], ["web2", False]]),
    ("MATCH (s:S315) WITH s, s.tags AS t WHERE size(t) > 1 RETURN s.name ORDER BY s.name", {}, [["db1"], ["web1"]]),
    ("MATCH (s:S315) WITH s.name AS n, s.tags AS t WHERE 'prod' IN t RETURN n ORDER BY n", {}, [["db1"], ["web1"]]),
    ("MATCH (s:S315) WHERE s.name IN $l RETURN s.name ORDER BY s.name", {"l": ["web1", "db1"]}, [["db1"], ["web1"]]),
    ("MATCH (s:S315) WITH collect(s.name) AS names RETURN 'web1' IN names AS a, 'nope' IN names AS b", {},
     [[True, False]]),
    ("MATCH (s:S315) WHERE any(t IN s.tags WHERE t = 'web') RETURN s.name ORDER BY s.name", {},
     [["web1"], ["web2"]]),
]


@pytest.mark.parametrize("graph", GRAPHS, ids=["default", "named"])
@pytest.mark.parametrize("query, params, rows", CASES, ids=[c[0][:60] for c in CASES])
def test_in_answers_by_opencypher(engine, graph, query, params, rows):
    result = engine.execute_cypher(_q(query, graph), params)
    assert result.error is None, result.error
    assert result.rows == rows
