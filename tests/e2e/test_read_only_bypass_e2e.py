"""execute_cypher(read_only=True) writes nothing to the database, however the query is spelled.

4.5.1 refused `CREATE (x:Probe ...)` but committed the same CREATE written after `CALL db.labels();`,
and a CREATE in a UNION branch, a CALL { } body or beside approx_count_distinct. Each case asserts
the refusal (PermissionError) and the database after the call: no node with the probe's id in
Graph_KG.nodes, no row with its label in Graph_KG.rdf_labels. The `writes_without_read_only`
control runs the same query with read_only=False and finds the node, so the refusal is of a query
that does write, not of one that could not.

Run against a scratch namespace (`IVG_NAMESPACE=IVGTEST`); `iris_master_cleanup` empties it.
"""

from __future__ import annotations

import os
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

# {label} and {nid} are filled per test; every query writes one node with that label and node_id
CASES = {
    "reporter": "CALL db.labels(); CREATE (x:{label} {{node_id: '{nid}'}}) RETURN x",
    "lower_case": "call db.labels(); create (x:{label} {{node_id: '{nid}'}}) return x",
    "write_first": "CREATE (x:{label} {{node_id: '{nid}'}}) RETURN x; CALL db.labels()",
    "use_graph": "USE GRAPH 'ro_g' CALL db.labels(); CREATE (x:{label} {{node_id: '{nid}'}}) RETURN x",
    "union_branch": (
        "MATCH (n:NoSuchLabelRO) RETURN n.node_id AS id "
        "UNION CREATE (x:{label} {{node_id: '{nid}'}}) RETURN x.node_id AS id"
    ),
    "call_subquery": "CALL {{ CREATE (x:{label} {{node_id: '{nid}'}}) RETURN x }} RETURN x.node_id AS id",
    "approx_count_distinct": (
        "CREATE (x:{label} {{node_id: '{nid}'}}) RETURN approx_count_distinct(x) AS c"
    ),
}


@pytest.fixture
def eng(iris_connection, iris_master_cleanup):
    return IRISGraphEngine(iris_connection)


def _probe(case):
    tag = uuid.uuid4().hex[:8]
    return f"RoProbe{tag}", f"ro-probe-{case}-{tag}"


def _stored(conn, label, nid):
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id = ?", [nid])
    nodes = int(cur.fetchone()[0])
    cur.execute("SELECT COUNT(*) FROM Graph_KG.rdf_labels WHERE label = ?", [label])
    labels = int(cur.fetchone()[0])
    return nodes, labels


@pytest.mark.parametrize("case", list(CASES))
def test_read_only_refuses_and_writes_nothing(eng, iris_connection, case):
    label, nid = _probe(case)
    query = CASES[case].format(label=label, nid=nid)
    with pytest.raises(PermissionError, match="Read-only mode"):
        eng.execute_cypher(query, {}, read_only=True)
    iris_connection.rollback()  # a write left uncommitted must not count as refused
    assert _stored(iris_connection, label, nid) == (0, 0)
    assert eng.execute_cypher(f"MATCH (x:{label}) RETURN x.node_id AS id", {}).rows == []


@pytest.mark.parametrize("case", ["reporter", "lower_case", "write_first", "use_graph"])
def test_writes_without_read_only(eng, iris_connection, case):
    label, nid = _probe(case)
    eng.execute_cypher(CASES[case].format(label=label, nid=nid), {}, raise_on_error=True)
    assert _stored(iris_connection, label, nid)[0] == 1


def test_use_graph_scopes_the_part_after_the_semicolon(eng, iris_connection):
    label, nid = _probe("scope")
    eng.execute_cypher(CASES["use_graph"].format(label=label, nid=nid), {}, raise_on_error=True)
    cur = iris_connection.cursor()
    cur.execute("SELECT graph_id FROM Graph_KG.nodes WHERE node_id = ?", [nid])
    assert [r[0] for r in cur.fetchall()] == ["ro_g"]


def test_a_semicolon_inside_a_string_is_not_a_statement_break(eng):
    label, nid = _probe("literal")
    eng.execute_cypher(f"CREATE (x:{label} {{node_id: '{nid}'}})", {}, raise_on_error=True)
    query = f"CALL db.labels() YIELD label WITH label WHERE label = '{label}' OR label = 'a;b' RETURN label"
    result = eng.execute_cypher(query, {}, read_only=True, raise_on_error=True)
    assert [list(r) for r in result.rows] == [[label]]


def test_a_failing_part_raises(eng):
    with pytest.raises(Exception) as caught:
        eng.execute_cypher("CALL db.labels(); MATCH (n RETURN n", {})
    assert not isinstance(caught.value, PermissionError)
