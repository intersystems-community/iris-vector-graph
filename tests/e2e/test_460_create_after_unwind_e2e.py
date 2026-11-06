"""4.6.0, live: a CREATE after an UNWIND makes one node per row, with the values that row computes.

docs/KNOWN_ISSUES.md "A CREATE after an UNWIND fails when more than one row reaches it": an UNWIND of a list the query
computes (`WITH collect(n.node_id) AS ids UNWIND ids AS i`) gave every row the one generated node id, so the second
row's property insert failed SQLCODE -119; an UNWIND after a MATCH whose CREATE reads the MATCH's node bound
`n.node_id`'s AST as a parameter (<ARGUMENT ERROR>); and a computed value (`CASE`, `toUpper`, `coalesce`) in a CREATE,
with or without an UNWIND, failed the same way (found by fixes64).

Worked by hand, with two (:G65) nodes, g1 {k: 1, code: '007'} and g2 {k: 2, code: 7}:

* `WITH collect(n.node_id) AS ids UNWIND ids AS i CREATE (:C65 {src: i})`: two nodes, src 'g1' and 'g2';
* `... CREATE (:C65 {node_id: i + '-copy'})`: nodes g1-copy and g2-copy (a node_id in a CREATE map is the node's id);
* `MATCH (n:G65) UNWIND [1, 2] AS k CREATE (:C65 {src: n.node_id, k: k})`: four nodes, each (g, k) once;
* `UNWIND [1, 2, 3] AS i CREATE (:C65 {p: CASE WHEN i > 1 THEN 'big' ELSE 'small' END})`: small, big, big;
* `WITH n.k AS k UNWIND [k, k * 10] AS v CREATE (:C65 {v: v})`: 1, 10, 2, 20, integers;
* `WITH collect(n.code) AS cs UNWIND cs AS c CREATE (:C65 {c: c})`: the string '007' and the integer 7, each its type;
* a CASE over an UNWIND map's key (`r.k`) or a matched node's property (`n.k`), with or without the node as the
  new relationship's end: small for k = 1, big for k = 2;
* `CREATE (:C65 {a: toUpper('x'), b: coalesce(null, 3), c: CASE 2 WHEN 1 THEN 'one' WHEN 2 THEN 'two' END, d: 2 >= 3})`:
  'X', 3, 'two', false.

Namespace: an IVG460FX* scratch namespace. Native on and off (writes go through SQL either way).
"""

from __future__ import annotations

import os

import pytest

from iris_vector_graph.prop_values import decode_typed_text
from tests.e2e import ns460alt as alt

NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith("IVG460FX"), reason="IVG_NAMESPACE must name an IVG460FX* scratch namespace"),
    pytest.mark.skipif(not alt.container_running(), reason=f"{alt.CONTAINER} is not running"),
]


@pytest.fixture(scope="module", params=["native", "sql"])
def engine(request):
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = iris.connect("localhost", alt.PORT, NS, "_SYSTEM", "SYS")
    eng = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768, native_cypher=request.param == "native")
    eng.initialize_schema()
    try:
        yield eng
    finally:
        _clean(eng)
        conn.close()


def _clean(eng):
    eng.execute_cypher("MATCH (n) WHERE n:G65 OR n:C65 DETACH DELETE n", {}, raise_on_error=True)
    eng.conn.commit()


@pytest.fixture
def seeded(engine):
    _clean(engine)
    engine.execute_cypher(
        "CREATE (:G65 {node_id: 'g1', k: 1, code: '007'}), (:G65 {node_id: 'g2', k: 2, code: 7})", {}, raise_on_error=True
    )
    engine.conn.commit()
    return engine


def _created(eng) -> list:
    """Each C65 node's properties (but node_id), read with plain SQL through CY_TYPED, which marks a stored number's
    type (rdf_props.val is VARCHAR: driver 5.4.1 reads a number there as its text); sorted."""
    cur = eng.conn.cursor()
    cur.execute(
        "SELECT n.node_id, p.\"key\", Graph_KG.CY_TYPED(p.val) FROM Graph_KG.nodes n JOIN Graph_KG.rdf_labels l ON l.s = n.node_id AND "
        "l.graph_id = n.graph_id AND l.label = 'C65' LEFT JOIN Graph_KG.rdf_props p ON p.s = n.node_id AND "
        "p.graph_id = n.graph_id AND p.\"key\" <> 'node_id'"
    )
    nodes: dict = {}
    for nid, k, v in cur.fetchall():
        v = decode_typed_text(v)
        props = nodes.setdefault(nid, {})
        if k is not None:
            props[k] = (v, type(v).__name__)
    return sorted((sorted(p.items()) for p in nodes.values()), key=repr)


def _rows(*dicts) -> list:
    return sorted((sorted({k: (v, type(v).__name__) for k, v in d.items()}.items()) for d in dicts), key=repr)


def _run(eng, q):
    r = eng.execute_cypher(q, {}, raise_on_error=True)
    assert getattr(r, "error", None) is None, r.error
    eng.conn.commit()


CASES = {
    "collect_unwind": (
        "MATCH (n:G65) WITH collect(n.node_id) AS ids UNWIND ids AS i CREATE (:C65 {src: i})",
        [{"src": "g1"}, {"src": "g2"}],
    ),
    "match_unwind_reads_node": (
        "MATCH (n:G65) UNWIND [1, 2] AS k CREATE (:C65 {src: n.node_id, k: k})",
        [{"src": g, "k": k} for g in ("g1", "g2") for k in (1, 2)],
    ),
    "case_after_literal_unwind": (
        "UNWIND [1, 2, 3] AS i CREATE (:C65 {p: CASE WHEN i > 1 THEN 'big' ELSE 'small' END})",
        [{"p": "small"}, {"p": "big"}, {"p": "big"}],
    ),
    "case_after_with": (
        "UNWIND [1, 2, 3] AS i WITH i CREATE (:C65 {p: CASE WHEN i > 1 THEN 'big' ELSE 'small' END})",
        [{"p": "small"}, {"p": "big"}, {"p": "big"}],
    ),
    "computed_list_unwind": (
        "MATCH (n:G65) WITH n.k AS k UNWIND [k, k * 10] AS v CREATE (:C65 {v: v})",
        [{"v": 1}, {"v": 10}, {"v": 2}, {"v": 20}],
    ),
    "typed_strings_through_collect": (
        "MATCH (n:G65) WITH collect(n.code) AS cs UNWIND cs AS c CREATE (:C65 {c: c})",
        [{"c": "007"}, {"c": 7}],
    ),
    "case_over_a_map_element": (
        "UNWIND [{k: 1}, {k: 2}] AS r CREATE (:C65 {p: CASE WHEN r.k > 1 THEN 'big' ELSE 'small' END})",
        [{"p": "small"}, {"p": "big"}],
    ),
    "case_over_a_matched_property": (
        "MATCH (n:G65) CREATE (:C65 {p: CASE WHEN n.k > 1 THEN 'big' ELSE 'small' END})",
        [{"p": "small"}, {"p": "big"}],
    ),
    "computed_values_in_a_plain_create": (
        "CREATE (:C65 {a: toUpper('x'), b: coalesce(null, 3), c: CASE 2 WHEN 1 THEN 'one' WHEN 2 THEN 'two' END, "
        "d: 2 >= 3})",
        [{"a": "X", "b": 3, "c": "two", "d": "false"}],
    ),
}


@pytest.mark.parametrize("case", list(CASES))
def test_one_node_per_row_with_its_values(seeded, case):
    query, expected = CASES[case]
    _run(seeded, query)
    assert _created(seeded) == _rows(*expected)


def test_a_computed_node_id_names_each_rows_node(seeded):
    _run(seeded, "MATCH (n:G65) WITH collect(n.node_id) AS ids UNWIND ids AS i CREATE (:C65 {node_id: i + '-copy'})")
    cur = seeded.conn.cursor()
    cur.execute("SELECT s FROM Graph_KG.rdf_labels WHERE label = 'C65' ORDER BY s")
    assert [r[0] for r in cur.fetchall()] == ["g1-copy", "g2-copy"]


def test_a_case_over_the_end_nodes_property_creates_one_linked_node_per_row(seeded):
    _run(seeded, "MATCH (n:G65) CREATE (n)-[:R]->(:C65 {p: CASE WHEN n.k > 1 THEN 'big' ELSE 'small' END})")
    assert _created(seeded) == _rows({"p": "small"}, {"p": "big"})
    cur = seeded.conn.cursor()
    cur.execute(
        "SELECT e.s, p.val FROM Graph_KG.rdf_edges e JOIN Graph_KG.rdf_props p ON p.s = e.o_id AND p.\"key\" = 'p' "
        "WHERE e.p = 'R' AND e.s IN ('g1', 'g2') ORDER BY e.s"
    )
    assert [tuple(r) for r in cur.fetchall()] == [("g1", "small"), ("g2", "big")]


@pytest.mark.parametrize("key", ["node_id", "id"])
def test_a_node_id_computed_from_the_unwind_row_is_the_nodes_id(seeded, key):
    """fixes64: `UNWIND range(0, 2) AS i CREATE (:L {node_id: 'z' + toString(i), i: i})` stored node_id as a property
    and gave each node a generated UUID, so `MATCH (n {node_id: 'z1'})` found nothing. A literal node_id was the id."""
    _run(seeded, f"UNWIND range(0, 2) AS i CREATE (:C65 {{{key}: 'z' + toString(i), i: i}})")
    cur = seeded.conn.cursor()
    cur.execute("SELECT s FROM Graph_KG.rdf_labels WHERE label = 'C65' ORDER BY s")
    assert [r[0] for r in cur.fetchall()] == ["z0", "z1", "z2"]
    r = seeded.execute_cypher("MATCH (n {node_id: 'z1'}) RETURN n.i", {}, raise_on_error=True)
    assert [list(row) for row in r.rows] == [[1]]
