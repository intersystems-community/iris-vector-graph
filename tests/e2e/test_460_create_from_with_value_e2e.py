"""4.6.0 R189: `WITH $v AS v CREATE (n:X {id: v, name: 'nm'})` creates the node with its label and properties.

It raised IntegrityError SQLCODE -121 (fk_labels_node): the label insert bound the label where the node id goes and
the id where the label goes, because the WITH stage's own parameter binds first and the statement put the label's
ahead of it. The properties were never written at all. The UNWIND form runs once per element and was right.

By hand: each case makes node `<run>_<case>` labelled `LC<run>` with `{id: <that id>, name: 'nm'}`.
"""

from __future__ import annotations

import json
import uuid

import pytest

pytestmark = [pytest.mark.e2e]


@pytest.fixture
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    e = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    label = f"LC{run}"
    yield e, run, label
    e.execute_cypher(f"MATCH (n:{label}) DETACH DELETE n")


def _run(e, q, params=None):
    r = e.execute_cypher(q, dict(params or {}))
    assert r.error is None, (q, r.error)
    return r.rows


def _node(e, label, nid):
    rows = _run(e, f"MATCH (n:{label}) WHERE n.id = $id RETURN labels(n), properties(n)", {"id": nid})
    return [[json.loads(a), json.loads(b)] for a, b in rows]


def test_with_parameter_create(g):
    e, run, label = g
    v = f"{run}_with"
    _run(e, f"WITH $v AS v CREATE (n:{label} {{id: v, name: 'nm'}})", {"v": v})
    assert _node(e, label, v) == [[[label], {"id": v, "name": "nm"}]]


def test_with_parameter_create_returns_the_node(g):
    e, run, label = g
    v = f"{run}_ret"
    rows = _run(e, f"WITH $v AS v CREATE (n:{label} {{id: v, name: 'nm'}}) RETURN n.id, n.name", {"v": v})
    assert rows == [[v, "nm"]]
    assert _node(e, label, v) == [[[label], {"id": v, "name": "nm"}]]


def test_with_two_values_create(g):
    e, run, label = g
    v = f"{run}_two"
    _run(e, f"WITH $v AS v, $w AS w CREATE (n:{label} {{id: v, name: w}})", {"v": v, "w": "nm"})
    assert _node(e, label, v) == [[[label], {"id": v, "name": "nm"}]]


def test_unwind_parameter_create(g):
    e, run, label = g
    vs = [f"{run}_u1", f"{run}_u2"]
    _run(e, f"UNWIND $list AS v CREATE (n:{label} {{id: v, name: 'nm'}})", {"list": vs})
    for v in vs:
        assert _node(e, label, v) == [[[label], {"id": v, "name": "nm"}]]
