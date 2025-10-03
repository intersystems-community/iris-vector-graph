#!/usr/bin/env python3
"""
Load sample fraud events and labels into IRIS database

Generates:
- 100 entities (accounts)
- 1000 events (transactions)
- 10 fraud labels (for testing)
- 1 fraud centroid (precomputed from labeled fraud nodes)

Constitutional Compliance: Test-First with Live Database
Run with: python scripts/fraud/load_sample_events.py
"""

import iris
import os
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
import random
import logging

logging.basicConfig(level=logging.INFO)
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


def generate_entities(cursor, num_entities=100):
    """Generate sample entities (accounts) and insert into nodes table"""
    logger.info(f"Generating {num_entities} entities...")

    entity_ids = []

    for i in range(num_entities):
        entity_id = f"acct:sample_user{i:03d}"
        entity_ids.append(entity_id)

        # Insert into nodes table (if exists)
        try:
            cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", (entity_id,))
        except Exception:
            # Node may already exist, skip
            pass

    logger.info(f"✅ Generated {num_entities} entities")
    return entity_ids


def generate_events(cursor, entity_ids, num_events=1000):
    """Generate sample transaction events"""
    logger.info(f"Generating {num_events} events...")

    now = datetime.utcnow()
    event_kinds = ["tx", "login", "device_change"]
    device_ids = [f"dev:laptop{i}" for i in range(5)] + [f"dev:mobile{i}" for i in range(5)]
    ips = [f"ip:192.168.1.{i}" for i in range(1, 256, 10)]

    for i in range(num_events):
        entity_id = random.choice(entity_ids)
        kind = random.choice(event_kinds)

        # Generate timestamp (distributed over last 30 days)
        days_ago = random.randint(0, 30)
        hours_ago = random.randint(0, 23)
        ts = now - timedelta(days=days_ago, hours=hours_ago)

        # Generate event attributes
        amount = random.uniform(10.0, 1000.0) if kind == "tx" else None
        device_id = random.choice(device_ids) if random.random() > 0.1 else None
        ip = random.choice(ips) if random.random() > 0.1 else None

        cursor.execute("""
            INSERT INTO gs_events (entity_id, kind, ts, amount, device_id, ip)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (entity_id, kind, ts, amount, device_id, ip))

    logger.info(f"✅ Generated {num_events} events")


def generate_labels(cursor, entity_ids, num_fraud=10):
    """Generate sample fraud labels"""
    logger.info(f"Generating {num_fraud} fraud labels...")

    # Randomly select fraud entities
    fraud_entities = random.sample(entity_ids, num_fraud)
    now = datetime.utcnow()

    for entity_id in fraud_entities:
        label_ts = now - timedelta(days=random.randint(1, 60))

        cursor.execute("""
            INSERT INTO gs_labels (entity_id, label, label_ts, source)
            VALUES (?, ?, ?, ?)
        """, (entity_id, 1, label_ts, "manual_review"))

    logger.info(f"✅ Generated {num_fraud} fraud labels")
    return fraud_entities


def compute_fraud_centroid(cursor, fraud_entities):
    """
    Compute fraud centroid from labeled fraud entities

    Averages embeddings of fraud nodes and inserts into gs_fraud_centroid table.
    """
    logger.info(f"Computing fraud centroid from {len(fraud_entities)} fraud entities...")

    # Retrieve embeddings for fraud entities
    fraud_embeddings = []

    for entity_id in fraud_entities:
        cursor.execute("""
            SELECT embedding
            FROM kg_NodeEmbeddings
            WHERE node_id = ?
        """, (entity_id,))

        result = cursor.fetchone()

        if result is not None:
            embedding = result[0]

            # Convert to numpy array
            if isinstance(embedding, str):
                embedding = np.fromstring(embedding.strip("[]"), sep=",")
            elif not isinstance(embedding, np.ndarray):
                embedding = np.array(embedding)

            fraud_embeddings.append(embedding)

    if len(fraud_embeddings) == 0:
        logger.warning("No embeddings found for fraud entities. Using zero-vector centroid.")
        centroid = np.zeros(768)
        num_fraud_nodes = 0
    else:
        # Compute average (centroid)
        centroid = np.mean(fraud_embeddings, axis=0)
        num_fraud_nodes = len(fraud_embeddings)

    # Convert centroid to IRIS VECTOR format (string representation for SQL)
    centroid_str = f"[{','.join(map(str, centroid))}]"

    # Insert into gs_fraud_centroid (singleton table)
    try:
        cursor.execute("""
            INSERT INTO gs_fraud_centroid (centroid_id, embedding, num_fraud_nodes)
            VALUES (1, ?, ?)
        """, (centroid_str, num_fraud_nodes))
    except Exception:
        # Centroid may already exist, update instead
        cursor.execute("""
            UPDATE gs_fraud_centroid
            SET embedding = ?, num_fraud_nodes = ?, last_updated = CURRENT_TIMESTAMP
            WHERE centroid_id = 1
        """, (centroid_str, num_fraud_nodes))

    logger.info(f"✅ Fraud centroid computed ({num_fraud_nodes} nodes)")


def main():
    """Load sample fraud events and labels into IRIS"""
    logger.info("="*60)
    logger.info("Loading Sample Fraud Events and Labels")
    logger.info("="*60)

    # Connect to IRIS
    logger.info("Connecting to IRIS...")
    conn = get_iris_connection()
    cursor = conn.cursor()
    logger.info("✅ Connected to IRIS")

    try:
        # Generate sample data
        entity_ids = generate_entities(cursor, num_entities=100)
        conn.commit()

        generate_events(cursor, entity_ids, num_events=1000)
        conn.commit()

        fraud_entities = generate_labels(cursor, entity_ids, num_fraud=10)
        conn.commit()

        compute_fraud_centroid(cursor, fraud_entities)
        conn.commit()

        logger.info("="*60)
        logger.info("✅ Sample data loaded successfully")
        logger.info("="*60)
        logger.info("\nData summary:")
        logger.info("  - 100 entities (accounts)")
        logger.info("  - 1000 events (transactions, logins, device changes)")
        logger.info("  - 10 fraud labels")
        logger.info("  - 1 fraud centroid")
        logger.info("\nNext steps:")
        logger.info("  1. python scripts/fraud/quickstart.py")
        logger.info("  2. pytest tests/contract/test_fraud_score_contract.py -v")

    except Exception as e:
        logger.error(f"❌ Sample data loading failed: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
