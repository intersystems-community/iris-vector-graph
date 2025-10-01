#!/usr/bin/env python3
"""
Migration utility for NodePK feature - adds explicit nodes table with foreign key constraints.

This script discovers existing nodes from all graph tables, creates the nodes table,
populates it with discovered nodes, and adds foreign key constraints to enforce
referential integrity.

Usage:
    python migrate_to_nodepk.py --validate-only  # Dry run, report only
    python migrate_to_nodepk.py --execute        # Apply migration
    python migrate_to_nodepk.py --execute --verbose  # Detailed logging

Constitutional Compliance:
- Principle I: IRIS-native SQL with iris.connect()
- Principle II: Designed for live IRIS database testing
- Principle VII: Explicit error handling with actionable messages
"""

import argparse
import logging
import os
import sys
from typing import List, Dict, Optional, Tuple
from datetime import datetime
import iris
from dotenv import load_dotenv


# Configure logging
def setup_logging(verbose: bool = False):
    """Configure logging with appropriate level and format."""
    level = logging.DEBUG if verbose else logging.INFO
    format_str = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    logging.basicConfig(level=level, format=format_str)
    return logging.getLogger(__name__)


# Database connection
def get_connection():
    """
    Get IRIS database connection from environment variables.

    Returns:
        iris.connection: Active database connection

    Raises:
        ValueError: If connection parameters are missing
        Exception: If connection fails
    """
    load_dotenv()

    # Get connection parameters
    host = os.getenv('IRIS_HOST', 'localhost')
    port = int(os.getenv('IRIS_PORT', '1972'))
    namespace = os.getenv('IRIS_NAMESPACE', 'USER')
    username = os.getenv('IRIS_USER', '_SYSTEM')
    password = os.getenv('IRIS_PASSWORD', 'SYS')

    if not all([host, port, namespace, username, password]):
        raise ValueError(
            "Missing required IRIS connection parameters. "
            "Please check your .env file has IRIS_HOST, IRIS_PORT, "
            "IRIS_NAMESPACE, IRIS_USER, and IRIS_PASSWORD defined."
        )

    try:
        conn = iris.connect(host, port, namespace, username, password)
        return conn
    except Exception as e:
        raise Exception(f"Failed to connect to IRIS database: {e}")


# Migration functions to be implemented in later tasks
def discover_nodes(connection) -> List[str]:
    """
    Discover all unique node IDs from existing graph tables.

    Implements Contract 7: Node Discovery from specs/001-add-explicit-nodepk/contracts/sql_contracts.md

    Args:
        connection: IRIS database connection

    Returns:
        List of unique node IDs discovered across all tables (sorted)

    Strategy:
        UNION query collecting node IDs from:
        - rdf_labels.s
        - rdf_props.s
        - rdf_edges.s (source nodes)
        - rdf_edges.o_id (destination nodes)
        - kg_NodeEmbeddings.id (if table exists)
    """
    logger = logging.getLogger(__name__)
    cursor = connection.cursor()

    # Base query for tables that definitely exist
    query = """
    SELECT DISTINCT node_id FROM (
        SELECT s AS node_id FROM rdf_labels
        UNION SELECT s FROM rdf_props
        UNION SELECT s FROM rdf_edges
        UNION SELECT o_id FROM rdf_edges
    ) all_nodes
    ORDER BY node_id
    """

    logger.info("Discovering unique node IDs from graph tables...")
    cursor.execute(query)
    nodes = [row[0] for row in cursor.fetchall()]

    # Try to add kg_NodeEmbeddings if it exists
    try:
        cursor.execute("SELECT DISTINCT id FROM kg_NodeEmbeddings")
        embedding_nodes = [row[0] for row in cursor.fetchall()]
        # Add any new nodes from embeddings
        nodes_set = set(nodes)
        for node in embedding_nodes:
            if node not in nodes_set:
                nodes.append(node)
        logger.info(f"  + kg_NodeEmbeddings: {len(embedding_nodes)} node IDs")
    except Exception as e:
        if 'not found' in str(e).lower() or 'does not exist' in str(e).lower():
            logger.debug("  kg_NodeEmbeddings table not found (OK - optional)")
        else:
            logger.warning(f"  Could not query kg_NodeEmbeddings: {e}")

    # Log breakdown by table
    cursor.execute("SELECT COUNT(DISTINCT s) FROM rdf_labels")
    label_count = cursor.fetchone()[0]
    logger.info(f"  rdf_labels: {label_count} unique node IDs")

    cursor.execute("SELECT COUNT(DISTINCT s) FROM rdf_props")
    props_count = cursor.fetchone()[0]
    logger.info(f"  rdf_props: {props_count} unique node IDs")

    cursor.execute("SELECT COUNT(DISTINCT s) FROM rdf_edges")
    edges_s_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(DISTINCT o_id) FROM rdf_edges")
    edges_o_count = cursor.fetchone()[0]
    logger.info(f"  rdf_edges (source): {edges_s_count} unique node IDs")
    logger.info(f"  rdf_edges (dest): {edges_o_count} unique node IDs")

    logger.info(f"✅ Total unique nodes discovered: {len(nodes)}")
    return nodes


def bulk_insert_nodes(connection, node_ids: List[str]) -> int:
    """
    Bulk insert nodes with deduplication.
    Implementation in T022.
    """
    raise NotImplementedError("bulk_insert_nodes() - implement in T022")


def detect_orphans(connection) -> Dict[str, List[str]]:
    """
    Detect orphaned references (nodes that don't exist).
    Implementation in T023.
    """
    raise NotImplementedError("detect_orphans() - implement in T023")


def validate_migration(connection) -> Dict:
    """
    Validate migration without making changes.
    Implementation in T024.
    """
    raise NotImplementedError("validate_migration() - implement in T024")


def execute_migration(connection) -> bool:
    """
    Execute the full migration.
    Implementation in T025.
    """
    raise NotImplementedError("execute_migration() - implement in T025")


def execute_sql_migration(connection, sql_file_path: str):
    """
    Execute SQL migration file.
    Used in T015 to create nodes table.

    Args:
        connection: IRIS database connection
        sql_file_path: Path to SQL migration file

    Raises:
        Exception: If SQL execution fails
    """
    logger = logging.getLogger(__name__)

    try:
        with open(sql_file_path, 'r') as f:
            sql_content = f.read()

        cursor = connection.cursor()

        # Process SQL content to handle comments properly
        # Split into lines to preserve comment structure
        lines = sql_content.split('\n')
        current_statement = []

        for line in lines:
            # Skip comment-only lines
            if line.strip().startswith('--') or not line.strip():
                continue

            # Add line to current statement
            current_statement.append(line)

            # If line ends with semicolon, execute the statement
            if line.strip().endswith(';'):
                statement = '\n'.join(current_statement).strip()
                if statement and not statement.startswith('--'):
                    logger.debug(f"Executing SQL: {statement[:100]}...")
                    cursor.execute(statement)
                current_statement = []

        # Execute any remaining statement
        if current_statement:
            statement = '\n'.join(current_statement).strip()
            if statement and not statement.startswith('--'):
                logger.debug(f"Executing SQL: {statement[:100]}...")
                cursor.execute(statement)

        connection.commit()
        logger.info(f"Successfully executed migration: {sql_file_path}")

        # Verify table was created
        cursor.execute("SELECT COUNT(*) FROM nodes WHERE 1=0")
        logger.info("Verified: nodes table exists")

    except Exception as e:
        logger.error(f"Failed to execute migration {sql_file_path}: {e}")
        connection.rollback()
        raise


def main():
    """
    Main CLI entry point.
    Parse arguments and route to appropriate function.
    """
    parser = argparse.ArgumentParser(
        description="Migrate IRIS Vector Graph to use explicit NodePK table",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Validate migration (dry run)
  python migrate_to_nodepk.py --validate-only

  # Execute migration
  python migrate_to_nodepk.py --execute

  # Execute with detailed logging
  python migrate_to_nodepk.py --execute --verbose
        """
    )

    # Mode selection (mutually exclusive)
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        '--validate-only',
        action='store_true',
        help='Validate migration without making changes (dry run)'
    )
    mode_group.add_argument(
        '--execute',
        action='store_true',
        help='Execute migration (creates nodes table, adds FK constraints)'
    )

    # Optional arguments
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )

    args = parser.parse_args()

    # Setup logging
    logger = setup_logging(args.verbose)

    # Get database connection
    try:
        logger.info("Connecting to IRIS database...")
        connection = get_connection()
        logger.info("Successfully connected to IRIS database")
    except Exception as e:
        logger.error(f"Failed to connect to database: {e}")
        sys.exit(1)

    try:
        if args.validate_only:
            logger.info("Running migration validation (dry run)...")
            report = validate_migration(connection)
            # TODO: Pretty print report (implement in T024)
            print(report)

        elif args.execute:
            logger.info("Executing migration...")
            success = execute_migration(connection)
            if success:
                logger.info("Migration completed successfully!")
                sys.exit(0)
            else:
                logger.error("Migration failed!")
                sys.exit(1)

    except NotImplementedError as e:
        logger.error(f"Function not yet implemented: {e}")
        logger.info("This is expected - implement tasks T021-T026 to complete migration utility")
        sys.exit(1)

    except Exception as e:
        logger.error(f"Migration error: {e}")
        sys.exit(1)

    finally:
        if connection:
            connection.close()
            logger.info("Database connection closed")


if __name__ == '__main__':
    main()