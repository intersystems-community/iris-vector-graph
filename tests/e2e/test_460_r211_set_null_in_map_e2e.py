"""4.6.0 R211: TCK Set5 [4] "Explicit null values in a map remove old values", and its REMOVE twin.

`MATCH (n:X {name: 'A'}) SET n += {name: null} RETURN n` removed `name` and answered no row. Each case builds three
nodes labelled X<run> with a Cypher CREATE: A `{name: 'A', name2: 'B'}`, B `{name: 'B', name2: 'C'}`, C
`{name2: 'D'}`. Worked by hand:

- `SET n += {name: null} RETURN n.name2, n.name`: `[['B', None]]`; A stored `{name2: 'B'}`, B and C unchanged
- `REMOVE n.name RETURN n.name2`: `[['B']]`; A stored `{name2: 'B'}`
- `SET n += {name: null, name2: 'Z'} RETURN n.name2`: `[['Z']]`; A stored `{name2: 'Z'}`
- `SET n += {name: null} RETURN n`: one node, with `{name2: 'B'}`
"""

from __future__ import annotations

import contextlib
import uuid

import pytest

pytestmark = [pytest.mark.e2e]


@pytest.fixture
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    e = IRISGraphEngine(iris_connection)
    label = f"X{uuid.uuid4().hex[:8]}"

    def run_q(query, params=None):
        r = e.execute_cypher(query, dict(params or {}))
        assert r.error is None, (query, r.error)
        return r.rows

    with contextlib.suppress(Exception):
        iris_connection.commit()
    run_q(
        f"CREATE (:{label} {{name: 'A', name2: 'B'}}), (:{label} {{name: 'B', name2: 'C'}}), (:{label} {{name2: 'D'}})"
    )
    yield run_q, label
    run_q(f"MATCH (n:{label}) DETACH DELETE n")


def _stored(run_q, label):
    rows = run_q(f"MATCH (n:{label}) RETURN properties(n)")
    return sorted((r[0] for r in rows), key=lambda p: p["name2"])  # 4.6.0 R311: properties(n) is the dict


_B = {"name": "B", "name2": "C"}
_C = {"name2": "D"}


@pytest.mark.parametrize(
    "tail, answer, a_after",
    [
        ("SET n += {name: null} RETURN n.name2, n.name", [["B", None]], {"name2": "B"}),
        ("REMOVE n.name RETURN n.name2", [["B"]], {"name2": "B"}),
        ("SET n += {name: null, name2: 'Z'} RETURN n.name2", [["Z"]], {"name2": "Z"}),
    ],
    ids=["null_in_map", "remove", "null_and_value"],
)
def test_return_after_removing_the_filtered_key(g, tail, answer, a_after):
    run_q, label = g
    assert run_q(f"MATCH (n:{label} {{name: 'A'}}) {tail}") == answer
    assert _stored(run_q, label) == sorted([a_after, _B, _C], key=lambda p: p["name2"])


def test_return_the_node(g):
    """Set5 [4]'s own RETURN n."""
    run_q, label = g
    rows = run_q(f"MATCH (n:{label} {{name: 'A'}}) SET n += {{name: null}} RETURN properties(n)")
    assert [r[0] for r in rows] == [{"name2": "B"}]
