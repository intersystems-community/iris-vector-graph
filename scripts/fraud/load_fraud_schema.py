#!/usr/bin/env python3
"""
Load fraud scoring schema into IRIS database

Executes:
1. sql/fraud/schema.sql - Table creation (gs_events, gs_labels, gs_fraud_centroid)
2. sql/fraud/procedures.sql - LANGUAGE PYTHON procedures (gs_ComputeFeatures, gs_SubgraphSample)

Constitutional Compliance: IRIS-Native Development
Run with: python scripts/fraud/load_fraud_schema.py
"""

import iris
import os
from pathlib import Path
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


def execute_sql_file(conn, file_path: Path):
    """
    Execute SQL file line by line

    IRIS SQL doesn't support full batch execution, so we execute
    statement by statement (separated by semicolons).
    """
    logger.info(f"Executing SQL file: {file_path}")

    with open(file_path, 'r') as f:
        sql_content = f.read()

    # Split by semicolon (simple parser, doesn't handle strings with ';')
    statements = [s.strip() for s in sql_content.split(';') if s.strip()]

    cursor = conn.cursor()

    for i, statement in enumerate(statements, start=1):
        # Skip comments and empty lines
        if statement.startswith('--') or not statement:
            continue

        try:
            logger.info(f"  [{i}/{len(statements)}] Executing statement...")
            cursor.execute(statement)
            conn.commit()
        except Exception as e:
            logger.error(f"  Error executing statement {i}: {e}")
            logger.error(f"  Statement: {statement[:200]}...")
            raise

    logger.info(f"✅ Successfully executed {file_path.name}")


def main():
    """Load fraud scoring schema into IRIS"""
    logger.info("="*60)
    logger.info("Loading Fraud Scoring Schema into IRIS")
    logger.info("="*60)

    # Get project root
    project_root = Path(__file__).parent.parent.parent

    # SQL files to execute
    sql_files = [
        project_root / "sql" / "fraud" / "schema.sql",
        project_root / "sql" / "fraud" / "procedures.sql"
    ]

    # Validate files exist
    for sql_file in sql_files:
        if not sql_file.exists():
            raise FileNotFoundError(f"SQL file not found: {sql_file}")

    # Connect to IRIS
    logger.info("Connecting to IRIS...")
    conn = get_iris_connection()
    logger.info(f"✅ Connected to IRIS")

    try:
        # Execute each SQL file
        for sql_file in sql_files:
            execute_sql_file(conn, sql_file)

        logger.info("="*60)
        logger.info("✅ Fraud scoring schema loaded successfully")
        logger.info("="*60)
        logger.info("\nCreated tables:")
        logger.info("  - gs_events (event log)")
        logger.info("  - gs_labels (fraud labels)")
        logger.info("  - gs_fraud_centroid (fraud embedding centroid)")
        logger.info("\nCreated procedures:")
        logger.info("  - gs_ComputeFeatures (on-demand feature computation)")
        logger.info("  - gs_SubgraphSample (k-hop subgraph sampling)")
        logger.info("\nNext steps:")
        logger.info("  1. python scripts/fraud/load_sample_events.py")
        logger.info("  2. python scripts/fraud/quickstart.py")

    except Exception as e:
        logger.error(f"❌ Schema loading failed: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
