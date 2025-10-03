#!/usr/bin/env python3
"""
Load sample fraud events and labels into IRIS database (Embedded Python Version)

This version uses iris.sql.exec() directly for embedded Python execution.
Must be run via: /usr/irissys/bin/irispython

Generates:
- 100 entities (accounts)
- 1000 events (transactions)
- 10 fraud labels (for testing)
- 1 fraud centroid (precomputed from labeled fraud nodes)

Constitutional Compliance: Test-First with Live Database (Embedded Python)
"""

import os
import sys
import numpy as np
from datetime import datetime, timedelta
import random
import logging

# Check if running in embedded Python
try:
    import iris
except ImportError:
    print("ERROR: iris module not found. This must run via /usr/irissys/bin/irispython")
    sys.exit(1)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def generate_entities(num_entities=100):
    """Generate sample entities (accounts) and insert into nodes table"""
    logger.info(f"Generating {num_entities} entities...")

    entity_ids = []
    success_count = 0

    for i in range(num_entities):
        entity_id = f"acct:sample_user{i:03d}"
        entity_ids.append(entity_id)

        # Insert into nodes table (if exists)
        try:
            iris.sql.exec("INSERT INTO nodes (node_id) VALUES (?)", entity_id)
            success_count += 1
        except Exception:
            # Node may already exist, skip
            pass

    logger.info(f"✅ Generated {num_entities} entities ({success_count} new)")
    return entity_ids


def generate_events(entity_ids, num_events=1000):
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

        iris.sql.exec("""
            INSERT INTO gs_events (entity_id, kind, ts, amount, device_id, ip)
            VALUES (?, ?, ?, ?, ?, ?)
        """, entity_id, kind, ts, amount, device_id, ip)

    logger.info(f"✅ Generated {num_events} events")


def generate_labels(entity_ids, num_fraud=10):
    """Generate sample fraud labels"""
    logger.info(f"Generating {num_fraud} fraud labels...")

    # Randomly select fraud entities
    fraud_entities = random.sample(entity_ids, num_fraud)
    now = datetime.utcnow()

    for entity_id in fraud_entities:
        label_ts = now - timedelta(days=random.randint(1, 60))

        iris.sql.exec("""
            INSERT INTO gs_labels (entity_id, label, label_ts, source)
            VALUES (?, ?, ?, ?)
        """, entity_id, 1, label_ts, "manual_review")

    logger.info(f"✅ Generated {num_fraud} fraud labels")
    return fraud_entities


def compute_fraud_centroid(fraud_entities):
    """
    Compute fraud centroid from labeled fraud entities

    Averages embeddings of fraud nodes and inserts into gs_fraud_centroid table.
    """
    logger.info(f"Computing fraud centroid from {len(fraud_entities)} fraud entities...")

    # Retrieve embeddings for fraud entities
    fraud_embeddings = []

    for entity_id in fraud_entities:
        result = iris.sql.exec("""
            SELECT embedding
            FROM kg_NodeEmbeddings
            WHERE node_id = ?
        """, entity_id)

        if result.next():
            embedding_str = result.get(1)

            if embedding_str:
                # Convert string representation to numpy array
                try:
                    # Remove brackets and split by comma
                    embedding_str = embedding_str.strip("[]")
                    embedding = np.fromstring(embedding_str, sep=",")
                    fraud_embeddings.append(embedding)
                except Exception as e:
                    logger.warning(f"Failed to parse embedding for {entity_id}: {e}")

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
        iris.sql.exec("""
            INSERT INTO gs_fraud_centroid (centroid_id, embedding, num_fraud_nodes)
            VALUES (1, ?, ?)
        """, centroid_str, num_fraud_nodes)
    except Exception:
        # Centroid may already exist, update instead
        iris.sql.exec("""
            UPDATE gs_fraud_centroid
            SET embedding = ?, num_fraud_nodes = ?, last_updated = CURRENT_TIMESTAMP
            WHERE centroid_id = 1
        """, centroid_str, num_fraud_nodes)

    logger.info(f"✅ Fraud centroid computed ({num_fraud_nodes} nodes)")


def verify_sample_data():
    """Verify sample data was loaded correctly"""
    try:
        # Count entities
        result = iris.sql.exec("SELECT COUNT(*) FROM nodes WHERE node_id LIKE 'acct:sample_user%'")
        result.next()
        entity_count = result.get(1)
        logger.info(f"  - {entity_count} entities in nodes table")

        # Count events
        result = iris.sql.exec("SELECT COUNT(*) FROM gs_events")
        result.next()
        event_count = result.get(1)
        logger.info(f"  - {event_count} events in gs_events table")

        # Count labels
        result = iris.sql.exec("SELECT COUNT(*) FROM gs_labels")
        result.next()
        label_count = result.get(1)
        logger.info(f"  - {label_count} fraud labels in gs_labels table")

        # Check centroid
        result = iris.sql.exec("SELECT num_fraud_nodes FROM gs_fraud_centroid WHERE centroid_id = 1")
        if result.next():
            num_fraud_nodes = result.get(1)
            logger.info(f"  - 1 fraud centroid (computed from {num_fraud_nodes} nodes)")
        else:
            logger.info(f"  - 0 fraud centroids (not yet computed)")

    except Exception as e:
        logger.error(f"Failed to verify sample data: {e}")


def main():
    """Load sample fraud events and labels into IRIS"""
    logger.info("="*60)
    logger.info("Loading Sample Fraud Events and Labels (Embedded Python)")
    logger.info("="*60)

    try:
        # Generate sample data
        entity_ids = generate_entities(num_entities=100)
        generate_events(entity_ids, num_events=1000)
        fraud_entities = generate_labels(entity_ids, num_fraud=10)
        compute_fraud_centroid(fraud_entities)

        logger.info("="*60)
        logger.info("✅ Sample data loaded successfully")
        logger.info("="*60)
        logger.info("\nData summary:")
        verify_sample_data()
        logger.info("\nNext steps:")
        logger.info("  1. /usr/irissys/bin/irispython scripts/fraud/quickstart.py")
        logger.info("  2. curl -X POST http://localhost:8000/fraud/score -H 'Content-Type: application/json' \\")
        logger.info("     -d '{\"mode\": \"MLP\", \"payer\": \"acct:sample_user001\", \"amount\": 100.0}'")

    except Exception as e:
        logger.error(f"❌ Sample data loading failed: {e}")
        raise


if __name__ == "__main__":
    sys.exit(main())