"""4.6.0 R120: a CREATE after a WITH stage runs once per row of the stage.

`UNWIND range(1,500) AS i WITH i LIMIT 3 CREATE (:X {i: i})` translated to one static statement set with one node id
for every row (`_create_row_key` names no row of a stage), so the label went in once and the three property inserts
for the same node hit the (s, key) constraint: one node, SQLCODE -119. openCypher creates a node per row.

Each answer below is worked from the query text: the stage's rows are the values the CREATE stores, one node each.
"""

from __future__ import annotations

import contextlib
import uuid

import pytest

pytestmark = [pytest.mark.e2e]


@pytest.fixture
def engine(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    eng = IRISGraphEngine(iris_connection)
    labels = []
    yield eng, labels
    for label in labels:
        with contextlib.suppress(Exception):
            eng.execute_cypher(f"MATCH (n:{label}) DETACH DELETE n")


def _label(labels):
    label = f"R120_{uuid.uuid4().hex[:8]}"
    labels.append(label)
    return label


def _values(eng, label, prop):
    r = eng.execute_cypher(f"MATCH (n:{label}) RETURN n.{prop} ORDER BY n.{prop}")
    assert not getattr(r, "error", None), r.error
    return [row[0] for row in r.rows]


def test_unwind_with_limit_create_makes_a_node_per_row(engine):
    eng, labels = engine
    x = _label(labels)
    r = eng.execute_cypher(f"UNWIND range(1,500) AS i WITH i LIMIT 3 CREATE (:{x} {{i: i}})")
    assert not getattr(r, "error", None), r.error
    assert _values(eng, x, "i") == [1, 2, 3]


def test_unwind_with_where_create(engine):
    eng, labels = engine
    x = _label(labels)
    r = eng.execute_cypher(f"UNWIND range(1,500) AS i WITH i WHERE i < 3 CREATE (:{x} {{i: i}})")
    assert not getattr(r, "error", None), r.error
    assert _values(eng, x, "i") == [1, 2]


def test_match_with_values_create(engine):
    eng, labels = engine
    src, dst = _label(labels), _label(labels)
    for v in ("a", "b"):
        eng.execute_cypher(f"CREATE (:{src} {{v: '{v}'}})")
    r = eng.execute_cypher(f"MATCH (n:{src}) WITH n.v AS v CREATE (:{dst} {{v: v, k: 'c'}})")
    assert not getattr(r, "error", None), r.error
    assert _values(eng, dst, "v") == ["a", "b"]
    assert _values(eng, dst, "k") == ["c", "c"]


def test_with_stage_create_returns_each_row(engine):
    eng, labels = engine
    x = _label(labels)
    r = eng.execute_cypher(f"UNWIND [5, 6] AS i WITH i * 10 AS j CREATE (n:{x} {{j: j}}) RETURN n.j")
    assert not getattr(r, "error", None), r.error
    assert sorted(row[0] for row in r.rows) == [50, 60]
    assert _values(eng, x, "j") == [50, 60]
