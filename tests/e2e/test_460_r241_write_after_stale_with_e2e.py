"""4.6.0 R241: a MERGE or CREATE after a WITH that reads a key an earlier SET changed writes what openCypher writes.

`MATCH (n:L {k: 1}) SET n.k = 5 WITH n WHERE n.k = 5 CREATE ...` wrote nothing and raised nothing: the stage re-ran
its MATCH, which the SET had moved off the row (a SET there raised SyntaxError since R208). Each case builds, with a
Cypher CREATE, two nodes labelled L<run>: A `{k: 1}` and B `{k: 2}`; created nodes are labelled M<run>. Worked by
hand: the MATCH binds A alone, the SET makes its k 5, and the WITH reads A after the SET:

- `WITH n WHERE n.k = 5 CREATE (n)-[:R]->(:M {id: 'c1'}) RETURN n.k`: `[[5]]`; A-[:R]->c1, nothing from B
- `WITH n WHERE n.k = 5 MERGE (n)-[:R]->(m:M {id: 'm1'}) RETURN m.id`: `[['m1']]`; A-[:R]->m1
- `WITH n WHERE n.k = 5 MERGE (m:M {id: 'm2'}) RETURN m.id`: `[['m2']]`; one M, m2
- `WITH n WHERE n.k = 5 CREATE (m:M {id: 'm3'}) RETURN count(*)`: `[[1]]`; one M, m3
- `WITH n, n.k AS k CREATE (:M {id: 'm4', v: k})`: one M, m4 with v 5 (the value after the SET)
- `WITH n WHERE n.k = 1 CREATE (:M {id: 'm5'})`: nothing (the WHERE reads 5)
- `WITH n WHERE n.k = 5 SET n.q = 3`: A has q 3 (refused since R208)
- `REMOVE n:L WITH n WHERE n:L CREATE ...`: still refused (a label the WITH reads), with R208's message
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
    run = uuid.uuid4().hex[:8]
    label, made = f"L{run}", f"M{run}"

    def run_q(query, params=None):
        r = e.execute_cypher(query, dict(params or {}))
        assert r.error is None, (query, r.error)
        return r.rows

    with contextlib.suppress(Exception):
        iris_connection.commit()
    run_q(f"CREATE (:{label} {{k: 1}}), (:{label} {{k: 2}})")
    yield run_q, label, made, e
    run_q(f"MATCH (n:{made}) DETACH DELETE n")
    run_q(f"MATCH (n:{label}) DETACH DELETE n")


def _head(label):
    return f"MATCH (n:{label} {{k: 1}}) SET n.k = 5 "


def _made(run_q, made):
    return sorted(run_q(f"MATCH (m:{made}) RETURN m.id, m.v"))


def _rels(run_q, label, made):
    return sorted(run_q(f"MATCH (n:{label})-[:R]->(m:{made}) RETURN n.k, m.id"))


def test_create_a_relationship(g):
    run_q, label, made, _ = g
    q = _head(label) + f"WITH n WHERE n.k = 5 CREATE (n)-[:R]->(:{made} {{id: 'c1'}}) RETURN n.k"
    assert run_q(q) == [[5]]
    assert _rels(run_q, label, made) == [[5, "c1"]]


def test_merge_a_relationship(g):
    run_q, label, made, _ = g
    q = _head(label) + f"WITH n WHERE n.k = 5 MERGE (n)-[:R]->(m:{made} {{id: 'm1'}}) RETURN m.id"
    assert run_q(q) == [["m1"]]
    assert _rels(run_q, label, made) == [[5, "m1"]]


def test_merge_a_node(g):
    run_q, label, made, _ = g
    assert run_q(_head(label) + f"WITH n WHERE n.k = 5 MERGE (m:{made} {{id: 'm2'}}) RETURN m.id") == [["m2"]]
    assert _made(run_q, made) == [["m2", None]]


def test_create_a_node(g):
    run_q, label, made, _ = g
    assert run_q(_head(label) + f"WITH n WHERE n.k = 5 CREATE (m:{made} {{id: 'm3'}}) RETURN count(*)") == [[1]]
    assert _made(run_q, made) == [["m3", None]]


def test_create_reads_the_value_after_the_set(g):
    run_q, label, made, _ = g
    run_q(_head(label) + f"WITH n, n.k AS k CREATE (:{made} {{id: 'm4', v: k}})")
    # read as text: a CREATE writes a WITH column as CAST(<column> AS VARCHAR), which here stores the text '5'
    # (not R241's: the same CREATE from a committed 5 stores the integer)
    assert run_q(f"MATCH (m:{made}) RETURN m.id, toString(m.v)") == [["m4", "5"]]


def test_a_where_the_set_made_false_creates_nothing(g):
    run_q, label, made, _ = g
    run_q(_head(label) + f"WITH n WHERE n.k = 1 CREATE (:{made} {{id: 'm5'}})")
    assert _made(run_q, made) == []


def test_set_after_the_with(g):
    run_q, label, _, _ = g
    run_q(_head(label) + "WITH n WHERE n.k = 5 SET n.q = 3")
    assert sorted(run_q(f"MATCH (n:{label}) RETURN n.k, n.q")) == [[2, None], [5, 3]]


def test_a_label_the_with_reads_is_still_refused(g):
    run_q, label, made, e = g
    q = f"MATCH (n:{label} {{k: 1}}) REMOVE n:{label} WITH n WHERE n:{label} CREATE (:{made} {{id: 'm6'}})"
    with pytest.raises(Exception, match="split the query"):
        r = e.execute_cypher(q, {})
        if r.error is not None:
            raise RuntimeError(r.error)
    assert _made(run_q, made) == []
    assert sorted(run_q(f"MATCH (n:{label}) RETURN n.k")) == [[1], [2]]
