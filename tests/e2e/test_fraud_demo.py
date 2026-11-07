"""The fraud demo's reads, over the repo's fraud sample data, which this module loads itself.

Its namespace is `IVG_FRAUDDEMO_NAMESPACE`, a scratch namespace with IVG installed that the E2E gate makes per run
(tests/e2e/gate.py GROUPS), loaded by the documented `scripts.setup.load_fraud_data` (sql/fraud_sample_data.sql). The
tests skipped on a fresh container ("No nodes in database", "No edges available") and so never ran in the gate.

By hand, from sql/fraud_sample_data.sql: 75 Account nodes; ACCOUNT:A001 is a checking account, active, risk_score 0.1;
TXN:T001 moves money FROM_ACCOUNT A001 TO_ACCOUNT A002; ring 1 is five transactions RING1_1..5 moving money A -> B ->
C -> D -> E -> A; ACCOUNT:A001 and ACCOUNT:MULE1 have 768-wide vectors.
"""

import time

import pytest

from tests.e2e import own_data_ns

NS_VAR = "IVG_FRAUDDEMO_NAMESPACE"
SQL_FILE = own_data_ns.REPO / "sql" / "fraud_sample_data.sql"
RING1 = ["ACCOUNT:RING1_A", "ACCOUNT:RING1_B", "ACCOUNT:RING1_C", "ACCOUNT:RING1_D", "ACCOUNT:RING1_E"]


@pytest.fixture(scope="module")
def engine():
    from iris_vector_graph.engine import IRISGraphEngine
    from scripts.setup import load_fraud_data

    conn, _ns = own_data_ns.connect(NS_VAR)
    load_fraud_data(conn)
    e = IRISGraphEngine(conn, embedding_dimension=768)
    assert e.sync()
    yield e
    e.erase_all()
    conn.close()


@pytest.mark.e2e
def test_fraud_data_loaded(engine):
    result = engine.execute_cypher("MATCH (n) RETURN count(n) AS c")
    if result.error and 'Table' in (result.error or ''):
        pytest.skip("Schema not initialized")
    assert result.error is None
    assert result.rows and result.rows[0][0] >= 0


@pytest.mark.e2e
def test_fraud_schema_exists(engine):
    result = engine.execute_cypher(
        "MATCH (n) WHERE labels(n) <> '[]' RETURN DISTINCT labels(n) LIMIT 10"
    )
    assert isinstance(result.rows, list)


@pytest.mark.e2e
def test_account_query_by_id(engine):
    result = engine.execute_cypher(
        "MATCH (n {node_id: $id}) RETURN n.node_id, labels(n), n.account_type, n.status, n.risk_score",
        {"id": "ACCOUNT:A001"}, raise_on_error=True,
    )
    [[node_id, labels, account_type, status, risk]] = result.rows
    assert (node_id, labels, account_type, status, float(risk)) == ("ACCOUNT:A001", ["Account"], "checking", "active", 0.1)
    assert engine.execute_cypher("MATCH (n:Account) RETURN count(n)", raise_on_error=True).rows == [[75]]


@pytest.mark.e2e
def test_account_with_risk_score(engine, iris_connection):
    import uuid
    pfx = f"risk_{uuid.uuid4().hex[:8]}"
    created = engine.create_node(pfx, properties={"risk_score": "0.85", "type": "Account"})
    if not created:
        pytest.skip("Schema not initialized — cannot create test node")
    result = engine.execute_cypher(
        "MATCH (n) WHERE n.risk_score IS NOT NULL RETURN n.node_id, n.risk_score LIMIT 5"
    )
    engine.delete_node(pfx)
    # `delete_node` removes edge rows from SQL without maintaining `^KG`/`^NKG`, so it
    # leaves the engine flagged dirty on purpose. The engine fixture is module-scoped:
    # without this rebuild every later variable-length query in the file raises
    # IndexNotSyncedError, which is the guard working, not a traversal bug.
    engine.sync()
    assert len(result.rows) >= 1
    for _, score in result.rows:
        assert 0.0 <= float(score) <= 1.0


@pytest.mark.e2e
def test_transaction_graph_traversal(engine):
    t0 = time.perf_counter()
    result = engine.execute_cypher(
        "MATCH (t {node_id: $id})-[r]->(a) RETURN type(r), a.node_id", {"id": "TXN:T001"}, raise_on_error=True
    )
    assert (time.perf_counter() - t0) * 1000 < 5000
    assert sorted(map(tuple, result.rows)) == [("FROM_ACCOUNT", "ACCOUNT:A001"), ("TO_ACCOUNT", "ACCOUNT:A002")]


@pytest.mark.e2e
def test_multi_hop_transaction_path(engine):
    """Follow the money around ring 1: from each account, the transaction it pays out of and the account it pays."""
    t0 = time.perf_counter()
    walked, at = [], RING1[0]
    for _ in RING1:
        result = engine.execute_cypher(
            "MATCH (a {node_id: $id})<-[:FROM_ACCOUNT]-(t)-[:TO_ACCOUNT]->(b) RETURN b.node_id",
            {"id": at}, raise_on_error=True,
        )
        [[at]] = result.rows
        walked.append(at)
    assert (time.perf_counter() - t0) * 1000 < 10000
    assert walked == RING1[1:] + RING1[:1]  # B, C, D, E and back to A
    # two hops from a ring transaction over any type: its two accounts, then what points on from them (nothing: the
    # accounts have no out-edges)
    result = engine.execute_cypher(
        "MATCH (n {node_id: $id})-[*1..2]->(m) RETURN m.node_id", {"id": "TXN:RING1_1"}, raise_on_error=True
    )
    assert sorted(r[0] for r in result.rows) == ["ACCOUNT:RING1_A", "ACCOUNT:RING1_B"]


@pytest.mark.e2e
def test_ring_pattern_detection(engine):
    t0 = time.perf_counter()
    result = engine.execute_cypher(
        "MATCH (n)-[r1]->(m)-[r2]->(n) RETURN n.id, count(r1) AS ring_count LIMIT 20"
    )
    assert (time.perf_counter() - t0) * 1000 < 10000
    assert isinstance(result.rows, list)


@pytest.mark.e2e
def test_mule_account_detection(engine):
    t0 = time.perf_counter()
    result = engine.execute_cypher(
        "MATCH (n)-[r]->(m) RETURN m.id, count(r) AS degree ORDER BY degree DESC LIMIT 10"
    )
    assert (time.perf_counter() - t0) * 1000 < 5000
    assert isinstance(result.rows, list)


@pytest.mark.e2e
def test_counterparty_analysis(engine):
    result = engine.execute_cypher(
        "MATCH (a)-[r1]->(b)-[r2]->(c) WHERE a.id <> c.id RETURN a.id, count(DISTINCT c.id) AS counterparties LIMIT 5"
    )
    assert isinstance(result.rows, list)


@pytest.mark.e2e
def test_vector_anomaly_detection(engine):
    t0 = time.perf_counter()
    for node_id in ("ACCOUNT:A001", "ACCOUNT:MULE1"):
        results = engine.search_nodes_by_vector(own_data_ns.sql_vector(SQL_FILE, node_id), k=2)
        # each account's own vector finds it first, at cosine 1, then the other one
        assert results[0][0] == node_id and results[0][1] == pytest.approx(1.0, abs=1e-6), results
        assert {r[0] for r in results} == {"ACCOUNT:A001", "ACCOUNT:MULE1"}, results
    assert (time.perf_counter() - t0) * 1000 < 10000


@pytest.mark.e2e
def test_alert_query(engine):
    result = engine.execute_cypher(
        "MATCH (n:Alert) RETURN n.id, n.severity LIMIT 10"
    )
    assert isinstance(result.rows, list)
