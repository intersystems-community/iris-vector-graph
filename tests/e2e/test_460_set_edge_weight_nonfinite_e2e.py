"""4.6.0, live: `set_edge_weight(s, p, o, nan)` keeps the relationship readable and its other properties.

It wrote `json.dumps` of the qualifiers, which spells NaN as the bare token `NaN`, invalid JSON: every property read of
that relationship then failed (`CY_REL_PROP` -149), so its other keys were stored but could not be read back. And a
qualifier text it could not parse was replaced by `{"weight": w}` alone, the other keys dropped without a word.

Worked by hand: a relationship (:NW70 {node_id: 'nw:a'})-[:R {note: 'keep', w2: 2}]->(:NW70 {node_id: 'nw:b'}).
* `set_edge_weight(..., nan)` stores the weight as a Cypher `SET r.weight = 0.0 / 0` does (`"NAN"`), `r.note` reads
  'keep' and `r.w2` 2, and `isNaN(r.weight)` is true; infinity and minus infinity as `"INF"` and `"-INF"`;
* with qualifier text that is not JSON, `set_edge_weight` raises `GraphWriteError` and leaves the text as it was.

Namespace: an IVG460FX* scratch namespace that exists (IVG_NAMESPACE); this file makes and drops none.
"""

from __future__ import annotations

import os

import pytest

from tests.e2e import ns460alt as alt

NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith("IVG460FX"), reason="IVG_NAMESPACE must name an IVG460FX* scratch namespace"),
    pytest.mark.skipif(not alt.container_running(), reason=f"{alt.CONTAINER} is not running"),
]


@pytest.fixture
def engine():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = iris.connect("localhost", alt.PORT, NS, "_SYSTEM", "SYS")
    eng = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768, native_cypher=False)
    eng.execute_cypher("MATCH (n:NW70) DETACH DELETE n", {}, raise_on_error=True)
    eng.execute_cypher(
        "CREATE (:NW70 {node_id: 'nw:a'})-[:R {note: 'keep', w2: 2}]->(:NW70 {node_id: 'nw:b'})", {}, raise_on_error=True
    )
    conn.commit()
    try:
        yield eng
    finally:
        eng.execute_cypher("MATCH (n:NW70) DETACH DELETE n", {}, raise_on_error=True)
        conn.commit()
        conn.close()


def _qualifiers(eng) -> str:
    cur = eng.conn.cursor()
    cur.execute("SELECT qualifiers FROM Graph_KG.rdf_edges WHERE s = 'nw:a' AND p = 'R'")
    return cur.fetchone()[0]


def _read(eng) -> list:
    r = eng.execute_cypher(
        "MATCH ({node_id: 'nw:a'})-[r:R]->() RETURN r.note, r.w2, isNaN(r.weight), r.weight", {}, raise_on_error=True
    )
    return [list(row) for row in r.rows]


def test_a_nan_weight_keeps_the_relationship_readable(engine):
    assert engine.set_edge_weight("nw:a", "R", "nw:b", float("nan")) is True
    engine.conn.commit()
    note, w2, is_nan, _ = _read(engine)[0]
    assert (note, str(w2), bool(is_nan)) == ("keep", "2", True)


@pytest.mark.parametrize("weight, spelled", [(float("inf"), "INF"), (float("-inf"), "-INF")])
def test_an_infinite_weight_is_spelled_as_cypher_spells_it(engine, weight, spelled):
    assert engine.set_edge_weight("nw:a", "R", "nw:b", weight) is True
    engine.conn.commit()
    assert f'"weight":"{spelled}"' in _qualifiers(engine).replace(" ", "")
    note, w2, _, _ = _read(engine)[0]
    assert (note, str(w2)) == ("keep", "2")


def test_qualifiers_that_are_not_json_are_not_replaced(engine):
    from iris_vector_graph.errors import GraphWriteError

    cur = engine.conn.cursor()
    cur.execute("UPDATE Graph_KG.rdf_edges SET qualifiers = ? WHERE s = 'nw:a' AND p = 'R'", ["{not json"])
    engine.conn.commit()
    with pytest.raises(GraphWriteError):
        engine.set_edge_weight("nw:a", "R", "nw:b", 3.0)
    engine.conn.rollback()
    assert _qualifiers(engine) == "{not json"
