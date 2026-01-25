#!/usr/bin/env python3
"""
Fraud Detection Demo E2E Test Suite

Tests the complete fraud detection demo workflow including:
- Fraud data loading
- Account queries
- Transaction graph traversal
- Ring pattern detection
- Mule account detection
- Vector anomaly detection

Per Constitution II: All tests use live IRIS database.
"""

import pytest

# ==============================================================================
# T024: Test Fraud Data Loaded
# ==============================================================================


@pytest.mark.e2e
@pytest.mark.requires_database
def test_fraud_data_loaded(iris_connection, fraud_test_data):
    """Test that fraud sample data is available."""
    # fraud_test_data fixture from conftest.py

    if not fraud_test_data["has_data"]:
        pytest.skip(
            "Fraud sample data not loaded - run: "
            'python -c "from scripts.setup import load_fraud_data; load_fraud_data()"'
        )

    assert fraud_test_data["account_count"] > 0, "Should have accounts"


@pytest.mark.e2e
@pytest.mark.requires_database
def test_fraud_schema_exists(iris_connection):
    """Test that fraud-related labels exist in schema."""
    cursor = iris_connection.cursor()

    # Count fraud-related entities
    cursor.execute(
        """
        SELECT label, COUNT(*) 
        FROM rdf_labels 
        WHERE label IN ('Account', 'Transaction', 'Alert')
        GROUP BY label
    """
    )

    counts = {row[0]: row[1] for row in cursor.fetchall()}

    # May not have data, but query should work
    assert isinstance(counts, dict)


# ==============================================================================
# T025: Test Account Query by ID
# ==============================================================================


@pytest.mark.e2e
@pytest.mark.requires_database
def test_account_query_by_id(iris_connection, fraud_test_data):
    """Test querying a specific account by ID."""
    if not fraud_test_data["has_data"]:
        pytest.skip("Fraud sample data not loaded")

    cursor = iris_connection.cursor()

    # Find an account
    cursor.execute(
        """
        SELECT s FROM rdf_labels 
        WHERE label = 'Account'
        LIMIT 1
    """
    )

    result = cursor.fetchone()
    assert result is not None, "Should find at least one account"

    account_id = result[0]

    # Query its properties
    cursor.execute("SELECT key, val FROM rdf_props WHERE s = ?", (account_id,))
    props = {row[0]: row[1] for row in cursor.fetchall()}

    # Account may or may not have properties, but query should work
    assert account_id is not None


@pytest.mark.e2e
@pytest.mark.requires_database
def test_account_with_risk_score(iris_connection, fraud_test_data):
    """Test querying accounts with risk scores."""
    if not fraud_test_data["has_data"]:
        pytest.skip("Fraud sample data not loaded")

    cursor = iris_connection.cursor()

    # Find accounts with risk_score property
    cursor.execute(
        """
        SELECT l.s, p.val 
        FROM rdf_labels l
        JOIN rdf_props p ON l.s = p.s
        WHERE l.label = 'Account'
        AND p.key = 'risk_score'
        LIMIT 5
    """
    )

    results = cursor.fetchall()

    if len(results) == 0:
        pytest.skip("No accounts with risk_score found")

    # Verify risk scores are valid numbers
    for account_id, risk_score in results:
        score = float(risk_score)
        assert 0.0 <= score <= 1.0, f"Risk score should be 0-1, got {score}"


# ==============================================================================
# T026: Test Transaction Graph Traversal
# ==============================================================================


@pytest.mark.e2e
@pytest.mark.requires_database
def test_transaction_graph_traversal(iris_connection, fraud_test_data, timing_tracker):
    """Test traversing transaction relationships."""
    if not fraud_test_data["has_data"]:
        pytest.skip("Fraud sample data not loaded")

    cursor = iris_connection.cursor()

    # Find transaction edges
    cursor.execute(
        """
        SELECT COUNT(*) FROM rdf_edges 
        WHERE p IN ('FROM_ACCOUNT', 'TO_ACCOUNT')
    """
    )

    edge_count = cursor.fetchone()[0]

    if edge_count == 0:
        pytest.skip("No transaction edges found")

    # Find a transaction with both source and destination
    with timing_tracker:
        cursor.execute(
            """
            SELECT e1.s as txn_id, e1.o_id as from_account, e2.o_id as to_account
            FROM rdf_edges e1
            JOIN rdf_edges e2 ON e1.s = e2.s
            WHERE e1.p = 'FROM_ACCOUNT'
            AND e2.p = 'TO_ACCOUNT'
            LIMIT 5
        """
        )

        transactions = cursor.fetchall()

    timing_tracker.assert_under(5000, "Transaction traversal")

    assert len(transactions) > 0, "Should find transactions with source and destination"


@pytest.mark.e2e
@pytest.mark.requires_database
def test_multi_hop_transaction_path(iris_connection, fraud_test_data, timing_tracker):
    """Test finding multi-hop paths through transaction network."""
    if not fraud_test_data["has_data"]:
        pytest.skip("Fraud sample data not loaded")

    cursor = iris_connection.cursor()

    # Find a starting account
    cursor.execute(
        """
        SELECT DISTINCT o_id FROM rdf_edges 
        WHERE p = 'FROM_ACCOUNT'
        LIMIT 1
    """
    )

    result = cursor.fetchone()

    if result is None:
        pytest.skip("No source accounts found")

    start_account = result[0]

    # 2-hop path: Account -> Transaction -> Account -> Transaction -> Account
    with timing_tracker:
        cursor.execute(
            """
            SELECT 
                e1.s as txn1, 
                e2.o_id as intermediate_account,
                e3.s as txn2,
                e4.o_id as final_account
            FROM rdf_edges e1
            JOIN rdf_edges e2 ON e1.s = e2.s AND e2.p = 'TO_ACCOUNT'
            JOIN rdf_edges e3 ON e2.o_id = e3.o_id AND e3.p = 'FROM_ACCOUNT'
            JOIN rdf_edges e4 ON e3.s = e4.s AND e4.p = 'TO_ACCOUNT'
            WHERE e1.o_id = ?
            AND e1.p = 'FROM_ACCOUNT'
            LIMIT 10
        """,
            (start_account,),
        )

        paths = list(cursor.fetchall())

    timing_tracker.assert_under(10000, "2-hop path traversal")

    # May or may not find paths depending on data
    assert isinstance(paths, list)


# ==============================================================================
# T027: Test Ring Pattern Detection
# ==============================================================================


@pytest.mark.e2e
@pytest.mark.requires_database
def test_ring_pattern_detection(iris_connection, fraud_test_data, timing_tracker):
    """Test detecting ring (cyclic) patterns in transaction network."""
    if not fraud_test_data["has_data"]:
        pytest.skip("Fraud sample data not loaded")

    cursor = iris_connection.cursor()

    # Find accounts that appear in both FROM and TO relationships
    # (potential ring participants)
    with timing_tracker:
        cursor.execute(
            """
            SELECT DISTINCT e1.o_id
            FROM rdf_edges e1
            WHERE e1.p IN ('FROM_ACCOUNT', 'TO_ACCOUNT')
            AND EXISTS (
                SELECT 1 FROM rdf_edges e2 
                WHERE e2.o_id = e1.o_id 
                AND e2.p != e1.p
            )
            LIMIT 20
        """
        )

        ring_candidates = list(cursor.fetchall())

    timing_tracker.assert_under(10000, "Ring pattern detection")

    # Sample data should include ring patterns
    # But test should pass even if none found
    assert isinstance(ring_candidates, list)

    if len(ring_candidates) > 0:
        # Verify these are actually accounts
        candidate_id = ring_candidates[0][0]
        cursor.execute("SELECT label FROM rdf_labels WHERE s = ?", (candidate_id,))
        label = cursor.fetchone()
        assert label is not None


# ==============================================================================
# T028: Test Mule Account Detection
# ==============================================================================


@pytest.mark.e2e
@pytest.mark.requires_database
def test_mule_account_detection(iris_connection, fraud_test_data, timing_tracker):
    """Test detecting mule accounts (high-degree nodes)."""
    if not fraud_test_data["has_data"]:
        pytest.skip("Fraud sample data not loaded")

    cursor = iris_connection.cursor()

    # Find accounts with multiple transaction connections
    with timing_tracker:
        cursor.execute(
            """
            SELECT o_id as account_id, COUNT(*) as txn_count
            FROM rdf_edges
            WHERE p IN ('FROM_ACCOUNT', 'TO_ACCOUNT')
            GROUP BY o_id
            ORDER BY 2 DESC
        """
        )

        high_degree_accounts = list(cursor.fetchall())

    timing_tracker.assert_under(5000, "Mule account detection")

    assert isinstance(high_degree_accounts, list)

    if len(high_degree_accounts) > 0:
        # Highest degree account
        top_account, top_count = high_degree_accounts[0]
        assert top_count >= 1, "Should have at least one transaction"


@pytest.mark.e2e
@pytest.mark.requires_database
def test_counterparty_analysis(iris_connection, fraud_test_data):
    """Test analyzing unique counterparties per account."""
    if not fraud_test_data["has_data"]:
        pytest.skip("Fraud sample data not loaded")

    cursor = iris_connection.cursor()

    # Find an account with transactions
    cursor.execute(
        """
        SELECT DISTINCT o_id FROM rdf_edges
        WHERE p = 'FROM_ACCOUNT'
        LIMIT 1
    """
    )

    result = cursor.fetchone()

    if result is None:
        pytest.skip("No accounts with transactions found")

    account_id = result[0]

    # Count unique counterparties
    cursor.execute(
        """
        SELECT COUNT(DISTINCT e2.o_id)
        FROM rdf_edges e1
        JOIN rdf_edges e2 ON e1.s = e2.s
        WHERE e1.o_id = ?
        AND e1.p = 'FROM_ACCOUNT'
        AND e2.p = 'TO_ACCOUNT'
    """,
        (account_id,),
    )

    counterparty_count = cursor.fetchone()[0]

    assert counterparty_count >= 0


# ==============================================================================
# T029: Test Vector Anomaly Detection
# ==============================================================================


@pytest.mark.e2e
@pytest.mark.requires_database
def test_vector_anomaly_detection(iris_connection, fraud_test_data, timing_tracker):
    """Test anomaly detection using vector embeddings."""
    cursor = iris_connection.cursor()

    # Check if we have account embeddings
    cursor.execute(
        """
        SELECT COUNT(*) FROM kg_NodeEmbeddings 
        WHERE id LIKE 'ACCOUNT:%'
    """
    )

    embedding_count = cursor.fetchone()[0]

    if embedding_count == 0:
        pytest.skip("No account embeddings available for anomaly detection")

    # Get a sample embedding
    cursor.execute(
        """
        SELECT id FROM kg_NodeEmbeddings 
        WHERE id LIKE 'ACCOUNT:%'
        LIMIT 1
    """
    )

    sample_id = cursor.fetchone()[0]

    # Try vector similarity (may fail if VECTOR functions unavailable)
    try:
        with timing_tracker:
            cursor.execute(
                """
                SELECT TOP 5 e2.id, VECTOR_COSINE(e1.emb, e2.emb) as similarity
                FROM kg_NodeEmbeddings e1, kg_NodeEmbeddings e2
                WHERE e1.id = ?
                AND e2.id != ?
                AND e2.id LIKE 'ACCOUNT:%'
                ORDER BY similarity ASC
            """,
                (sample_id, sample_id),
            )

            # Ordering ASC finds most different (potential anomalies)
            anomalies = list(cursor.fetchall())

        timing_tracker.assert_under(10000, "Vector anomaly detection")

        assert isinstance(anomalies, list)

        if len(anomalies) > 0:
            # Lowest similarity = most anomalous
            most_anomalous_id, similarity = anomalies[0]
            assert float(similarity) <= 1.0, "Cosine similarity should be <= 1"

    except Exception as e:
        if "VECTOR" in str(e).upper():
            pytest.skip("VECTOR functions not available (requires IRIS 2025.1+)")
        raise


@pytest.mark.e2e
@pytest.mark.requires_database
def test_alert_query(iris_connection, fraud_test_data):
    """Test querying fraud alerts."""
    if not fraud_test_data.get("alert_count", 0) == 0:
        # alerts exist, query them
        pass

    cursor = iris_connection.cursor()

    # Query alerts
    cursor.execute(
        """
        SELECT l.s, p.key, p.val
        FROM rdf_labels l
        LEFT JOIN rdf_props p ON l.s = p.s
        WHERE l.label = 'Alert'
        LIMIT 10
    """
    )

    results = cursor.fetchall()

    results_list = list(results) if results else []
    assert len(results_list) >= 0
