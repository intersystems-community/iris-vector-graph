"""R215: ``create_edge(weight=...)`` stores the weight as a Cypher ``CREATE`` of ``{weight: w}`` stores it.

Walkthrough 2 (W2): ``create_edge(weight=1.0)`` read back ``r.weight = None`` because a weight equal to the default
was not stored, and ``weight=2.0`` read back as the integer 2 because it was stored as the JSON number 2.0, which IRIS
reads as 2. A Cypher ``CREATE (a)-[:R {weight: 1.0}]->(b)`` stores the text of the number (``{"weight": "1.0"}``),
which ``r.weight`` reads back with its type; ``create_edge`` now stores that. An omitted weight stores nothing, as
before, and the temporal writer's marked row keeps its 4.5.x form.
"""

import json
from unittest.mock import MagicMock

import pytest

from iris_vector_graph.engine import IRISGraphEngine


def _engine(indexed=True):
    conn = MagicMock()
    cur = conn.cursor.return_value
    cur.fetchone.return_value = None
    cur.fetchall.return_value = []
    cur.rowcount = 1
    eng = IRISGraphEngine(conn, store=MagicMock())
    eng._adjacency_indexed_cached = indexed
    native = MagicMock()
    eng._iris_obj = lambda: native
    return eng, cur, native


def _stored_qualifiers(cur):
    for c in cur.execute.call_args_list:
        sql = str(c.args[0])
        if "INSERT INTO" in sql and "rdf_edges" in sql:
            q = c.args[1][3]
            return None if q is None else json.loads(q)
    raise AssertionError("no rdf_edges INSERT was executed")


def _cypher_create_form(value):
    """What the translator stores for CREATE ()-[:R {weight: value}]->() (translator: str(v) of the literal)."""
    return {"weight": str(value)}


@pytest.mark.parametrize("weight", [1.0, 2.0, 2.5, 1, 7, 0.0])
def test_weight_is_stored_as_cypher_create_stores_it(weight):
    eng, cur, _ = _engine()
    eng.create_edge("a", "R", "b", weight=weight)
    assert _stored_qualifiers(cur) == _cypher_create_form(weight)


def test_one_point_zero_is_stored():
    eng, cur, _ = _engine()
    eng.create_edge("a", "R", "b", weight=1.0)
    assert _stored_qualifiers(cur) == {"weight": "1.0"}


def test_an_omitted_weight_stores_nothing():
    eng, cur, _ = _engine()
    eng.create_edge("a", "R", "b")
    assert _stored_qualifiers(cur) is None


def test_weight_joins_the_other_qualifiers():
    eng, cur, _ = _engine()
    eng.create_edge("a", "R", "b", weight=2.0, qualifiers={"since": 2020})
    assert _stored_qualifiers(cur) == {"since": 2020, "weight": "2.0"}


def test_a_non_finite_weight_is_refused():
    eng, cur, _ = _engine()
    with pytest.raises(ValueError, match="finite"):
        eng.create_edge("a", "R", "b", weight=float("nan"))


@pytest.mark.parametrize("weight, expected", [(None, "1.0"), (1.0, "1.0"), (2.0, "2.0"), (3, "3.0")])
def test_the_hand_written_adjacency_entry_gets_the_weight(weight, expected):
    eng, cur, native = _engine(indexed=False)
    eng.create_edge("a", "R", "b", weight=weight)
    calls = [c for c in native.classMethodVoid.call_args_list if c.args[1] == "WriteAdjacency"]
    assert calls and calls[0].args[5] == expected


@pytest.mark.parametrize("weight, quals", [(1.0, {"temporal": True}), (0.9, {"temporal": True, "weight": 0.9})])
def test_the_temporal_marked_row_keeps_its_form(weight, quals):
    from iris_vector_graph._engine.temporal import _own_structural_row

    eng = MagicMock()
    eng.conn.cursor.return_value.fetchone.return_value = (0,)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("iris_vector_graph._engine.temporal._select_structural_row", lambda *a, **k: (0,))
        _own_structural_row(eng, "a", "R", "b", weight, None)
    kwargs = eng.create_edge.call_args.kwargs
    assert kwargs["qualifiers"] == quals
    assert kwargs.get("weight") is None
