"""4.6.0 R305 follow-up, live: a CREATE or MERGE between matched endpoints whose relationship property is computed from
the row raises GraphWriteError and writes nothing; the same value passed through a WITH is stored.

On the release head `MATCH (a), (b) CREATE (a)-[:T {w: a.name, k: 1}]->(b)` created the relationship with no
properties (k lost too; measured on ivg-iris-enterprise IVG460FX50A, 2026-11-05). Storing a row's value there is a
4.6.1 item (docs/KNOWN_ISSUES.md); the write is refused instead of losing data.

By hand: (:Z306 {name: 'a', k: 5}), (:Z306 {name: 'b'}). The refused CREATE and MERGE leave no Q306 relationship. `WITH
a, b, a.name AS w, a.k AS k CREATE (a)-[:W306 {w: w, k: k}]->(b)` stores w 'a' and k 5.

The namespace is `IVG_NAMESPACE` (an IVG460*/IVG262* scratch namespace with IVG set up); this file's label is removed
before and after.
"""

from __future__ import annotations


import pytest

from tests.e2e import ns460alt as alt

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]

MATCHED = "MATCH (a:Z306 {name: 'a'}), (b:Z306 {name: 'b'}) "


@pytest.fixture(scope="module")
def engine():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = alt.connect()
    schema = iris.createIRIS(conn).get("^IVG.Deploy", "schema") or "Graph_KG"
    eng = IRISGraphEngine(conn, namespace=alt.NS, schema_prefix=schema)
    eng.execute_cypher("MATCH (n:Z306) DETACH DELETE n", raise_on_error=True)
    eng.execute_cypher("CREATE (:Z306 {name: 'a', k: 5}), (:Z306 {name: 'b'})", raise_on_error=True)
    yield eng
    try:
        eng.execute_cypher("MATCH (n:Z306) DETACH DELETE n", raise_on_error=True)
    finally:
        conn.close()


@pytest.mark.parametrize("raise_on_error", [False, True])
@pytest.mark.parametrize("verb", ["CREATE", "MERGE"])
def test_a_row_computed_property_is_refused_and_nothing_is_written(engine, verb, raise_on_error):
    from iris_vector_graph.errors import GraphWriteError

    with pytest.raises(GraphWriteError, match=r"property `w`.*KNOWN_ISSUES"):
        engine.execute_cypher(MATCHED + f"{verb} (a)-[:Q306 {{w: a.name, k: 1}}]->(b)", raise_on_error=raise_on_error)
    r = engine.execute_cypher("MATCH (:Z306)-[r:Q306]->() RETURN count(r)", raise_on_error=True)
    assert r.rows == [[0]]


def test_the_value_through_a_with_is_stored(engine):
    engine.execute_cypher(MATCHED + "WITH a, b, a.name AS w, a.k AS k CREATE (a)-[:W306 {w: w, k: k}]->(b)",
                          raise_on_error=True)
    r = engine.execute_cypher("MATCH (:Z306)-[r:W306]->() RETURN properties(r)", raise_on_error=True)
    assert [row[0] for row in r.rows] == [{"w": "a", "k": 5}]  # 4.6.0 R311: the dict
