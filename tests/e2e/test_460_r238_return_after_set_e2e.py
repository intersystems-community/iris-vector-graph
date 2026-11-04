"""4.6.0 R238: a RETURN after a SET answers the rows the MATCH bound, when the SET changes a key the MATCH filters on.

`MATCH (n:QX {name: 'A'}) SET n.name = 'C' RETURN n.id` answered both QX nodes: the RETURN reads the MATCH again and
drops its test on the key the query sets. Each case builds, with a Cypher CREATE, two nodes labelled L<run>:
A `{name: 'A', k: 1}` and B `{name: 'B', k: 2}`, and the relationships A-[:T {w: 1}]->B and B-[:T {w: 2}]->A.
Worked by hand (the MATCH binds A alone, or A->B alone; the RETURN answers that row with the values written):

- `{name: 'A'} SET n.name = 'C' RETURN n.name`: `[['C']]`; stored names C, B
- the same `RETURN count(*)`: `[[1]]`
- `{name: 'A'} SET n.name = 'B' RETURN n.k`: `[[1]]` (B has that name now too; it was not matched)
- `WHERE n.name = 'A'`, `WHERE n.name STARTS WITH 'A'`, `SET n += {name: 'C'}`: `[['C']]`
- `{name: 'A'} SET n.k = 9 RETURN n.name, n.k` (a key the MATCH does not test): `[['A', 9]]`; B keeps k 2
- `(a {name: 'A'}), (b) SET b.name = 'Z' RETURN a.k, b.k`: `[[1, 1], [1, 2]]`; both named Z
- `(x)-[r:T {w: 1}]->(y) SET r.w = 5 RETURN x.name, r.w, y.name`: `[['A', 5, 'B']]`; stored w: A->B 5, B->A 2
- `()-[r:T {w: 1}]->() SET r.w = 2 RETURN count(*)`: `[[1]]` (both have w 2 now; one was matched)
- `(x)-[r:T]->() WHERE r.w = 1 SET r.w = 3 RETURN x.name`: `[['A']]`
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
    label = f"L{uuid.uuid4().hex[:8]}"

    def run_q(query, params=None):
        r = e.execute_cypher(query, dict(params or {}))
        assert r.error is None, (query, r.error)
        return r.rows

    with contextlib.suppress(Exception):
        iris_connection.commit()
    run_q(f"CREATE (a:{label} {{name: 'A', k: 1}}), (b:{label} {{name: 'B', k: 2}}), "
          f"(a)-[:T {{w: 1}}]->(b), (b)-[:T {{w: 2}}]->(a)")
    yield run_q, label
    run_q(f"MATCH (n:{label}) DETACH DELETE n")


def _nodes(run_q, label):
    return sorted(run_q(f"MATCH (n:{label}) RETURN n.name, n.k"), key=lambda r: (r[1], r[0]))


def _rels(run_q, label):
    return sorted(run_q(f"MATCH (x:{label})-[r:T]->(y:{label}) RETURN x.k, r.w, y.k"))


@pytest.mark.parametrize(
    "q, answer, names",
    [
        ("MATCH (n:{L} {{name: 'A'}}) SET n.name = 'C' RETURN n.name", [["C"]], [["C", 1], ["B", 2]]),
        ("MATCH (n:{L} {{name: 'A'}}) SET n.name = 'C' RETURN count(*)", [[1]], [["C", 1], ["B", 2]]),
        ("MATCH (n:{L} {{name: 'A'}}) SET n.name = 'B' RETURN n.k", [[1]], [["B", 1], ["B", 2]]),
        ("MATCH (n:{L}) WHERE n.name = 'A' SET n.name = 'C' RETURN n.name", [["C"]], [["C", 1], ["B", 2]]),
        ("MATCH (n:{L}) WHERE n.name STARTS WITH 'A' SET n.name = 'C' RETURN n.name", [["C"]], [["C", 1], ["B", 2]]),
        ("MATCH (n:{L} {{name: 'A'}}) SET n += {{name: 'C'}} RETURN n.name", [["C"]], [["C", 1], ["B", 2]]),
        ("MATCH (n:{L} {{name: 'A'}}) SET n.k = 9 RETURN n.name, n.k", [["A", 9]], [["B", 2], ["A", 9]]),
    ],
    ids=["inline", "count", "collides", "where_eq", "starts_with", "merge_map", "other_key"],
)
def test_node_return_after_set(g, q, answer, names):
    run_q, label = g
    assert run_q(q.format(L=label)) == answer
    assert _nodes(run_q, label) == names


def test_a_variable_the_set_does_not_write(g):
    """`(a {name: 'A'}), (b)`: rows (A, A) and (A, B); b's SET renames both, a is still A's row."""
    run_q, label = g
    q = f"MATCH (a:{label} {{name: 'A'}}), (b:{label}) SET b.name = 'Z' RETURN a.k, b.k ORDER BY b.k"
    assert run_q(q) == [[1, 1], [1, 2]]
    assert _nodes(run_q, label) == [["Z", 1], ["Z", 2]]


@pytest.mark.parametrize(
    "q, answer, rels",
    [
        ("MATCH (x:{L})-[r:T {{w: 1}}]->(y) SET r.w = 5 RETURN x.name, r.w, y.name", [["A", 5, "B"]],
         [[1, 5, 2], [2, 2, 1]]),
        ("MATCH (:{L})-[r:T {{w: 1}}]->() SET r.w = 2 RETURN count(*)", [[1]], [[1, 2, 2], [2, 2, 1]]),
        ("MATCH (x:{L})-[r:T]->() WHERE r.w = 1 SET r.w = 3 RETURN x.name", [["A"]], [[1, 3, 2], [2, 2, 1]]),
    ],
    ids=["inline", "count_collides", "where"],
)
def test_relationship_return_after_set(g, q, answer, rels):
    run_q, label = g
    assert run_q(q.format(L=label)) == answer
    assert _rels(run_q, label) == rels
