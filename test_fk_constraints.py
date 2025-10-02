#!/usr/bin/env python3
"""Test FK constraints (T016-T020)"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from scripts.migrations.migrate_to_nodepk import get_connection, execute_sql_migration
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_fk_constraints():
    """Test adding FK constraints."""
    conn = None
    try:
        logger.info("Connecting to IRIS...")
        conn = get_connection()

        logger.info("Adding FK constraints...")
        execute_sql_migration(conn, 'sql/migrations/002_add_fk_constraints.sql')

        logger.info("✅ FK constraints added successfully!")
        logger.info("\n=== T016-T020 COMPLETE ===")
        logger.info("Foreign key constraints are now enforced!")

    except Exception as e:
        logger.error(f"Error: {e}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()

if __name__ == '__main__':
    test_fk_constraints()