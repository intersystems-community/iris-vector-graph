#!/usr/bin/env python3
"""
Fraud Scoring Quickstart Validation Script

Validates complete fraud scoring workflow:
1. IRIS database health check
2. Fraud schema loaded (tables + procedures)
3. Sample data present (events, labels, centroid)
4. Insert demo transaction event
5. Score transaction via REST API
6. Validate explainability (min 3 reason codes)

Constitutional Compliance: Test-First with Live Database, Observability
Run with: python scripts/fraud/quickstart.py
"""

import iris
import os
import requests
import time
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def get_iris_connection():
    """Get IRIS database connection from environment"""
    return iris.connect(
        host=os.getenv("IRIS_HOST", "localhost"),
        port=int(os.getenv("IRIS_PORT", "1972")),
        namespace=os.getenv("IRIS_NAMESPACE", "USER"),
        username=os.getenv("IRIS_USER", "_SYSTEM"),
        password=os.getenv("IRIS_PASSWORD", "SYS")
    )


def validate_database_health(conn):
    """Step 1: Verify IRIS database health"""
    logger.info("="*60)
    logger.info("Step 1: Verifying IRIS Database Health")
    logger.info("="*60)

    cursor = conn.cursor()

    # Test basic query
    cursor.execute("SELECT 1")
    result = cursor.fetchone()

    assert result[0] == 1, "Basic SELECT query failed"

    logger.info("✅ IRIS database connection healthy")


def validate_schema_loaded(conn):
    """Step 2: Verify fraud schema loaded (tables + procedures)"""
    logger.info("="*60)
    logger.info("Step 2: Verifying Fraud Schema Loaded")
    logger.info("="*60)

    cursor = conn.cursor()

    # Check tables exist
    tables = ["gs_events", "gs_labels", "gs_fraud_centroid"]

    for table in tables:
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        count = cursor.fetchone()[0]
        logger.info(f"  ✅ Table {table}: {count} rows")

    # Check procedures exist (try calling with test input)
    try:
        cursor.execute("SELECT * FROM TABLE(gs_ComputeFeatures(?))", ("acct:test_user",))
        logger.info("  ✅ Procedure gs_ComputeFeatures exists")
    except Exception as e:
        logger.warning(f"  ⚠️  Procedure gs_ComputeFeatures check: {e}")

    try:
        cursor.execute("SELECT * FROM TABLE(gs_SubgraphSample(?, ?, ?))", ("acct:test_user", 10, 5))
        logger.info("  ✅ Procedure gs_SubgraphSample exists")
    except Exception as e:
        logger.warning(f"  ⚠️  Procedure gs_SubgraphSample check: {e}")

    logger.info("✅ Fraud schema validation complete")


def validate_sample_data(conn):
    """Step 3: Verify sample data present"""
    logger.info("="*60)
    logger.info("Step 3: Verifying Sample Data Present")
    logger.info("="*60)

    cursor = conn.cursor()

    # Check events
    cursor.execute("SELECT COUNT(*) FROM gs_events")
    num_events = cursor.fetchone()[0]
    assert num_events > 0, "No events found in gs_events table"
    logger.info(f"  ✅ gs_events: {num_events} events")

    # Check labels
    cursor.execute("SELECT COUNT(*) FROM gs_labels WHERE label = 1")
    num_fraud_labels = cursor.fetchone()[0]
    logger.info(f"  ✅ gs_labels: {num_fraud_labels} fraud labels")

    # Check centroid
    cursor.execute("SELECT num_fraud_nodes FROM gs_fraud_centroid WHERE centroid_id = 1")
    result = cursor.fetchone()
    if result:
        num_fraud_nodes = result[0]
        logger.info(f"  ✅ gs_fraud_centroid: computed from {num_fraud_nodes} fraud nodes")
    else:
        logger.warning("  ⚠️  gs_fraud_centroid: no centroid found")

    logger.info("✅ Sample data validation complete")


def insert_demo_transaction(conn):
    """Step 4: Insert demo transaction event"""
    logger.info("="*60)
    logger.info("Step 4: Inserting Demo Transaction Event")
    logger.info("="*60)

    cursor = conn.cursor()

    # Insert demo transaction
    demo_entity = "acct:demo_user_quickstart"
    demo_amount = 150.00
    demo_device = "dev:laptop_demo"
    demo_ip = "ip:192.168.1.100"

    cursor.execute("""
        INSERT INTO gs_events (entity_id, kind, ts, amount, device_id, ip)
        VALUES (?, ?, CURRENT_TIMESTAMP, ?, ?, ?)
    """, (demo_entity, "tx", demo_amount, demo_device, demo_ip))

    conn.commit()

    logger.info(f"  ✅ Inserted demo transaction:")
    logger.info(f"     Entity: {demo_entity}")
    logger.info(f"     Amount: ${demo_amount:.2f}")
    logger.info(f"     Device: {demo_device}")
    logger.info(f"     IP: {demo_ip}")

    return demo_entity, demo_amount


def score_transaction_via_api(demo_entity, demo_amount):
    """Step 5: Score transaction via REST API"""
    logger.info("="*60)
    logger.info("Step 5: Scoring Transaction via REST API")
    logger.info("="*60)

    # API endpoint
    api_url = "http://localhost:8000/fraud/score"

    # Request payload
    payload = {
        "mode": "MLP",
        "payer": demo_entity,
        "device": "dev:laptop_demo",
        "ip": "ip:192.168.1.100",
        "merchant": "merchant:demo_merchant",
        "amount": demo_amount,
        "country": "US"
    }

    logger.info(f"  Sending POST {api_url}")
    logger.info(f"  Payload: {payload}")

    # Measure latency
    start_time = time.perf_counter()

    try:
        response = requests.post(api_url, json=payload, timeout=5.0)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error(f"  ❌ API request failed: {e}")
        logger.error(f"     Make sure FastAPI is running: uvicorn api.main:app --reload")
        raise

    end_time = time.perf_counter()

    latency_ms = (end_time - start_time) * 1000

    logger.info(f"  ✅ Response status: {response.status_code}")
    logger.info(f"  ✅ Latency: {latency_ms:.2f}ms")

    # Validate latency SLO
    if latency_ms < 20.0:
        logger.info(f"  ✅ Latency meets <20ms SLO")
    else:
        logger.warning(f"  ⚠️  Latency {latency_ms:.2f}ms exceeds 20ms SLO")

    return response.json(), latency_ms


def validate_explainability(response_data):
    """Step 6: Validate explainability (min 3 reason codes)"""
    logger.info("="*60)
    logger.info("Step 6: Validating Explainability")
    logger.info("="*60)

    fraud_prob = response_data.get("prob")
    reasons = response_data.get("reasons", [])
    trace_id = response_data.get("trace_id")
    mode = response_data.get("mode")

    logger.info(f"  Fraud Probability: {fraud_prob:.4f}")
    logger.info(f"  Trace ID: {trace_id}")
    logger.info(f"  Mode: {mode}")

    # Validate fraud probability in valid range
    assert 0.0 <= fraud_prob <= 1.0, f"Fraud prob {fraud_prob} not in [0.0, 1.0]"
    logger.info(f"  ✅ Fraud probability in valid range [0.0, 1.0]")

    # Validate min 3 reason codes (FR-014)
    assert len(reasons) >= 3, f"Expected min 3 reason codes, got {len(reasons)}"
    logger.info(f"  ✅ {len(reasons)} reason codes returned (min 3 required)")

    # Print reason codes
    logger.info(f"\n  Reason Codes:")
    for i, reason in enumerate(reasons, start=1):
        logger.info(f"    {i}. {reason}")

    logger.info("\n✅ Explainability validation complete")


def main():
    """Run fraud scoring quickstart validation workflow"""
    logger.info("="*70)
    logger.info(" Fraud Scoring Quickstart Validation")
    logger.info("="*70)

    try:
        # Step 1: Database health
        conn = get_iris_connection()
        validate_database_health(conn)

        # Step 2: Schema loaded
        validate_schema_loaded(conn)

        # Step 3: Sample data
        validate_sample_data(conn)

        # Step 4: Insert demo transaction
        demo_entity, demo_amount = insert_demo_transaction(conn)

        conn.close()

        # Step 5: Score via REST API
        response_data, latency_ms = score_transaction_via_api(demo_entity, demo_amount)

        # Step 6: Validate explainability
        validate_explainability(response_data)

        logger.info("="*70)
        logger.info("✅ Fraud Scoring Quickstart Validation: PASSED")
        logger.info("="*70)
        logger.info("\nNext steps:")
        logger.info("  1. Run contract tests: pytest tests/contract/test_fraud_score_contract.py -v")
        logger.info("  2. Run integration tests: pytest tests/integration/ -v")
        logger.info("  3. Run load tests: pytest tests/e2e/test_fraud_load.py -v")
        logger.info(f"\nAPI documentation: http://localhost:8000/docs")

    except Exception as e:
        logger.error("="*70)
        logger.error(f"❌ Fraud Scoring Quickstart Validation: FAILED")
        logger.error(f"   Error: {e}")
        logger.error("="*70)
        raise


if __name__ == "__main__":
    main()
