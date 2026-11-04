"""4.6.0 R125: `CALL ivg.degreeCentrality(...)` answers each node's degree, as `engine.degree_centrality()` does.

The procedure passed its predicate to `kg_DegreeCentrality` as the SQL literal `''`, which reaches the ObjectScript
function body as `$Char(0)`, SQL's empty string, not ObjectScript's "": `DegreeCentralityJson` read it as a
predicate named $Char(0) and counted no edge, so every node scored 0.

The graph, by hand, in the default graph of a namespace emptied first (`Graph.KG.Eraser.EraseAll`): a star
c -S-> l1, c -S-> l2, c -S-> l3, and one more edge l1 -X-> l2. Four nodes, so score = degree / 3.

    direction  predicate   c  l1  l2  l3
    out        (all)       3   1   0   0
    in         (all)       0   1   2   1
    both       (all)       3   2   2   1
    out        S           3   0   0   0
    in         S           0   1   1   1
"""

from __future__ import annotations

import pytest

from tests.e2e import ns460alt as alt

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]

NODES = ("c", "l1", "l2", "l3")
EDGES = (("c", "S", "l1"), ("c", "S", "l2"), ("c", "S", "l3"), ("l1", "X", "l2"))
TABLE = {
    ("out", ""): (3, 1, 0, 0),
    ("in", ""): (0, 1, 2, 1),
    ("both", ""): (3, 2, 2, 1),
    ("out", "S"): (3, 0, 0, 0),
    ("in", "S"): (0, 1, 1, 1),
}


def _id(n: str) -> str:
    return f"r125:{n}"


@pytest.fixture(scope="module")
def star():
    import iris

    alt.make_namespace()
    conn = alt.connect()
    engine = alt.engine(conn)
    engine.initialize_schema()
    iris.createIRIS(conn).classMethodValue("Graph.KG.Eraser", "EraseAll")
    conn.commit()
    for n in NODES:
        engine.create_node(_id(n), labels=["R125"])
    for s, p, o in EDGES:
        engine.create_edge(_id(s), p, _id(o))
    conn.commit()
    yield engine
    for n in NODES:
        engine.delete_node(_id(n), graph=None)
    conn.commit()
    conn.close()


def _expected(direction: str, predicate: str) -> dict:
    return {_id(n): (d, round(d / 3, 6)) for n, d in zip(NODES, TABLE[(direction, predicate)])}


@pytest.mark.parametrize("direction,predicate", sorted(TABLE))
def test_the_method_answers_the_hand_worked_degrees(star, direction, predicate):
    rows = star.degree_centrality(direction=direction, predicate=predicate or None)
    assert {r["id"]: (r["degree"], round(r["score"], 6)) for r in rows} == _expected(direction, predicate)


@pytest.mark.parametrize("direction,predicate", sorted(TABLE))
def test_the_call_answers_the_hand_worked_degrees(star, direction, predicate):
    options = f"direction: '{direction}'" + (f", predicate: '{predicate}'" if predicate else "")
    rows = star.execute_cypher(
        f"CALL ivg.degreeCentrality({{{options}}}) YIELD node, score, degree RETURN node, score, degree",
        raise_on_error=True,
    ).rows
    assert {row[0]: (int(row[-1]), round(float(row[-2]), 6)) for row in rows} == _expected(direction, predicate)


def test_the_call_with_an_explicit_empty_predicate_counts_every_predicate(star):
    rows = star.execute_cypher(
        "CALL ivg.degreeCentrality({direction: 'out', predicate: ''}) YIELD node, degree RETURN node, degree",
        raise_on_error=True,
    ).rows
    assert {row[0]: int(row[-1]) for row in rows} == {k: v[0] for k, v in _expected("out", "").items()}
