"""4.6.0, live: `UNWIND range(0, $n) AS i CREATE (:L {i: i})` creates one node per element.

Found by fixes64: with n = 3 it failed SQLCODE -119 on rdf_props pk_props, where `range(0, 3)` written out creates
four nodes. A range whose bounds are literals is unrolled before the query runs, one CREATE per element; a bound that
was a parameter was not, so the CREATE made one node and then wrote one `i` row per element on it, the same
(graph_id, s, key) four times. Now a parameter bound is read as the literal would be.

Worked by hand: range(0, 3) is 0, 1, 2, 3, so four nodes, each with its own integer i; range(2, 8, 3) is 2, 5, 8; a
range that is empty (range(3, 0)) creates nothing.

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
    eng.execute_cypher("MATCH (n:R65) DETACH DELETE n", {}, raise_on_error=True)
    conn.commit()
    try:
        yield eng
    finally:
        eng.execute_cypher("MATCH (n:R65) DETACH DELETE n", {}, raise_on_error=True)
        conn.commit()
        conn.close()


@pytest.fixture(autouse=True)
def _clean(engine):
    engine.execute_cypher("MATCH (n:R65) DETACH DELETE n", {}, raise_on_error=True)
    engine.conn.commit()


def _stored_i(eng) -> list:
    """Each R65 node's `i` row, read with plain SQL through CY_TYPED, which marks a stored number's type (driver 5.4.1
    reads a number in the VARCHAR column as its text): (graph, value, its Python type)."""
    cur = eng.conn.cursor()
    cur.execute(
        "SELECT n.graph_id, Graph_KG.CY_TYPED(p.val) FROM Graph_KG.nodes n JOIN Graph_KG.rdf_labels l ON l.s = n.node_id AND "
        "l.graph_id = n.graph_id AND l.label = 'R65' LEFT JOIN Graph_KG.rdf_props p ON p.s = n.node_id AND "
        "p.graph_id = n.graph_id AND p.\"key\" = 'i'"
    )
    return sorted((g or "", decode_typed_text(v), type(decode_typed_text(v)).__name__) for g, v in cur.fetchall())


@pytest.mark.parametrize(
    "query, params, expected",
    [
        ("UNWIND range(0, $n) AS i CREATE (:R65 {i: i})", {"n": 3}, [0, 1, 2, 3]),
        ("UNWIND range($a, $b, $s) AS i CREATE (:R65 {i: i})", {"a": 2, "b": 8, "s": 3}, [2, 5, 8]),
        ("UNWIND range(3, $n) AS i CREATE (:R65 {i: i})", {"n": 0}, []),
    ],
)
def test_a_range_with_a_parameter_bound_creates_one_node_per_element(engine, query, params, expected):
    r = engine.execute_cypher(query, params, raise_on_error=True)
    assert getattr(r, "error", None) is None, r.error
    engine.conn.commit()
    # the literal range's answer, element for element: integers, in the default graph
    assert _stored_i(engine) == [("", v, "int") for v in expected]
