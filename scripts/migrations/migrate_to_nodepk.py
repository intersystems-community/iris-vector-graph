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
import subprocess
import sys
from typing import List, Dict, Optional, Tuple
from datetime import datetime
from dotenv import load_dotenv

# Try iris-devtester first, fall back to direct iris module
try:
    from iris_devtester.utils.dbapi_compat import get_connection as devtester_connect
    DEVTESTER_AVAILABLE = True
except ImportError:
    DEVTESTER_AVAILABLE = False

try:
    import iris
    IRIS_AVAILABLE = True
except ImportError:
    IRIS_AVAILABLE = False


# Configure logging
def setup_logging(verbose: bool = False):
    """Configure logging with appropriate level and format."""
    level = logging.DEBUG if verbose else logging.INFO
    format_str = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    logging.basicConfig(level=level, format=format_str)
    return logging.getLogger(__name__)


# The dedicated test container name
TEST_CONTAINER_NAME = 'iris_test_vector_graph_ai'


def get_container_port(container_name: str, internal_port: int = 1972) -> int:
    """Get the host port for a specific Docker container."""
    try:
        result = subprocess.run(
            ['docker', 'port', container_name, str(internal_port)],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            port_line = result.stdout.strip().split('\n')[0]
            port = int(port_line.split(':')[-1])
            return port
    except (subprocess.TimeoutExpired, ValueError, IndexError):
        pass
    return None


# Database connection
def get_connection():
    """
    Get IRIS database connection.

    Connection priority:
    1. IRIS_TEST_CONTAINER env var - target specific test container (for tests)
    2. IRIS_TEST_PORT env var - explicit test port override
    3. IRIS_PORT env var from .env file (for production)

    Uses iris-devtester if available, falls back to direct iris module.

    Returns:
        Active database connection

    Raises:
        ValueError: If connection parameters are missing
        Exception: If connection fails
    """
    load_dotenv()
    logger = logging.getLogger(__name__)

    # Get connection parameters
    host = os.getenv('IRIS_HOST', 'localhost')
    namespace = os.getenv('IRIS_NAMESPACE', 'USER')
    username = os.getenv('IRIS_USER', '_SYSTEM')
    password = os.getenv('IRIS_PASSWORD', 'SYS')

    # Port discovery - test container first, then env vars
    port = None

    # Priority 1: Check for specific test container
    container_name = os.getenv('IRIS_TEST_CONTAINER', '')
    if container_name:
        port = get_container_port(container_name)
        if port:
            logger.debug(f"Using test container {container_name} on port {port}")

    # Priority 2: Check for test-specific port override
    if port is None:
        test_port = os.getenv('IRIS_TEST_PORT')
        if test_port:
            port = int(test_port)
            logger.debug(f"Using IRIS_TEST_PORT={port}")

    # Priority 3: Check for running test container by default name (pytest detection)
    if port is None and 'pytest' in sys.modules:
        port = get_container_port(TEST_CONTAINER_NAME)
        if port:
            logger.debug(f"Detected pytest - using test container {TEST_CONTAINER_NAME} on port {port}")

    # Priority 4: Use production port from .env
    if port is None:
        port = int(os.getenv('IRIS_PORT', '1972'))
        logger.debug(f"Using IRIS_PORT={port}")

    if not all([host, namespace, username, password]):
        raise ValueError(
            "Missing required IRIS connection parameters. "
            "Please check your .env file has IRIS_HOST, IRIS_NAMESPACE, "
            "IRIS_USER, and IRIS_PASSWORD defined."
        )

    # Use iris-devtester if available, otherwise direct iris module
    try:
        if DEVTESTER_AVAILABLE:
            conn = devtester_connect(host, port, namespace, username, password)
        elif IRIS_AVAILABLE:
            conn = iris.connect(host, port, namespace, username, password)
        else:
            raise ImportError("Neither iris-devtester nor iris module available")
        return conn
    except Exception as e:
        raise Exception(f"Failed to connect to IRIS database at {host}:{port}: {e}")


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
    Bulk insert nodes with deduplication and performance measurement.

    Implements efficient batch insertion from T022 specification.

    Args:
        connection: IRIS database connection
        node_ids: List of node IDs to insert

    Returns:
        Count of nodes successfully inserted

    Performance:
        - Target: ≥1000 nodes/second
        - Uses batch size of 1000 nodes per transaction
        - Ignores duplicates (idempotent)

    Strategy:
        Since IRIS doesn't support ON DUPLICATE KEY IGNORE, we use:
        1. Try INSERT, catch UNIQUE constraint violations
        2. Batch commits every 1000 nodes for performance
        3. Measure and log insertion rate
    """
    logger = logging.getLogger(__name__)
    cursor = connection.cursor()

    if not node_ids:
        logger.info("No nodes to insert")
        return 0

    logger.info(f"Bulk inserting {len(node_ids)} nodes...")
    start_time = datetime.now()

    inserted_count = 0
    batch_size = 1000
    current_batch = 0

    for i, node_id in enumerate(node_ids):
        try:
            cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", [node_id])
            inserted_count += 1

            # Commit batch every 1000 nodes
            if (i + 1) % batch_size == 0:
                connection.commit()
                current_batch += 1
                logger.debug(f"  Committed batch {current_batch} ({i + 1}/{len(node_ids)} nodes)")

        except Exception as e:
            error_msg = str(e).lower()
            # Ignore duplicate key errors (UNIQUE constraint violations)
            if 'unique' in error_msg or 'duplicate' in error_msg or 'constraint' in error_msg:
                # Already exists, skip
                connection.rollback()
                continue
            else:
                # Unexpected error
                logger.error(f"Error inserting node {node_id}: {e}")
                connection.rollback()
                raise

    # Final commit for remaining nodes
    try:
        connection.commit()
    except:
        connection.rollback()

    # Calculate performance
    elapsed_time = (datetime.now() - start_time).total_seconds()
    if elapsed_time > 0:
        nodes_per_second = inserted_count / elapsed_time
        logger.info(f"✅ Inserted {inserted_count} nodes in {elapsed_time:.2f}s ({nodes_per_second:.0f} nodes/sec)")

        if nodes_per_second < 1000:
            logger.warning(f"⚠️  Performance below target (≥1000 nodes/sec): {nodes_per_second:.0f} nodes/sec")
    else:
        logger.info(f"✅ Inserted {inserted_count} nodes (too fast to measure)")

    return inserted_count


def detect_orphans(connection) -> Dict[str, List[str]]:
    """
    Detect orphaned node references across graph tables.

    Implements orphan detection from T023 specification using LEFT JOIN queries
    to find node IDs that are referenced but don't exist in the nodes table.

    Args:
        connection: IRIS database connection

    Returns:
        Dict mapping table name to list of orphaned node IDs
        Example: {'rdf_edges_source': ['node1', 'node2'], 'rdf_labels': ['node3']}

    Strategy:
        For each dependent table, query for node IDs that don't exist in nodes table:
        - rdf_edges.s (source nodes)
        - rdf_edges.o_id (destination nodes)
        - rdf_labels.s
        - rdf_props.s
        - kg_NodeEmbeddings.id (if table exists)
    """
    logger = logging.getLogger(__name__)
    cursor = connection.cursor()

    orphans = {}
    total_orphans = 0

    logger.info("Detecting orphaned node references...")

    # Check rdf_edges source nodes
    query = """
    SELECT DISTINCT s FROM rdf_edges
    WHERE s NOT IN (SELECT node_id FROM nodes)
    """
    cursor.execute(query)
    orphaned_sources = [row[0] for row in cursor.fetchall()]
    if orphaned_sources:
        orphans['rdf_edges_source'] = orphaned_sources
        total_orphans += len(orphaned_sources)
        logger.warning(f"  rdf_edges (source): {len(orphaned_sources)} orphaned nodes")
        logger.debug(f"    Sample: {orphaned_sources[:5]}")

    # Check rdf_edges destination nodes
    query = """
    SELECT DISTINCT o_id FROM rdf_edges
    WHERE o_id NOT IN (SELECT node_id FROM nodes)
    """
    cursor.execute(query)
    orphaned_dests = [row[0] for row in cursor.fetchall()]
    if orphaned_dests:
        orphans['rdf_edges_dest'] = orphaned_dests
        total_orphans += len(orphaned_dests)
        logger.warning(f"  rdf_edges (dest): {len(orphaned_dests)} orphaned nodes")
        logger.debug(f"    Sample: {orphaned_dests[:5]}")

    # Check rdf_labels
    query = """
    SELECT DISTINCT s FROM rdf_labels
    WHERE s NOT IN (SELECT node_id FROM nodes)
    """
    cursor.execute(query)
    orphaned_labels = [row[0] for row in cursor.fetchall()]
    if orphaned_labels:
        orphans['rdf_labels'] = orphaned_labels
        total_orphans += len(orphaned_labels)
        logger.warning(f"  rdf_labels: {len(orphaned_labels)} orphaned nodes")
        logger.debug(f"    Sample: {orphaned_labels[:5]}")

    # Check rdf_props
    query = """
    SELECT DISTINCT s FROM rdf_props
    WHERE s NOT IN (SELECT node_id FROM nodes)
    """
    cursor.execute(query)
    orphaned_props = [row[0] for row in cursor.fetchall()]
    if orphaned_props:
        orphans['rdf_props'] = orphaned_props
        total_orphans += len(orphaned_props)
        logger.warning(f"  rdf_props: {len(orphaned_props)} orphaned nodes")
        logger.debug(f"    Sample: {orphaned_props[:5]}")

    # Check kg_NodeEmbeddings (if exists)
    try:
        query = """
        SELECT DISTINCT id FROM kg_NodeEmbeddings
        WHERE id NOT IN (SELECT node_id FROM nodes)
        """
        cursor.execute(query)
        orphaned_embeddings = [row[0] for row in cursor.fetchall()]
        if orphaned_embeddings:
            orphans['kg_NodeEmbeddings'] = orphaned_embeddings
            total_orphans += len(orphaned_embeddings)
            logger.warning(f"  kg_NodeEmbeddings: {len(orphaned_embeddings)} orphaned nodes")
            logger.debug(f"    Sample: {orphaned_embeddings[:5]}")
    except Exception as e:
        if 'not found' in str(e).lower() or 'does not exist' in str(e).lower():
            logger.debug("  kg_NodeEmbeddings table not found (OK - optional)")
        else:
            logger.warning(f"  Could not check kg_NodeEmbeddings: {e}")

    if total_orphans == 0:
        logger.info("✅ No orphaned references found!")
    else:
        logger.error(f"❌ Found {total_orphans} orphaned node references across {len(orphans)} tables")

    return orphans


def validate_migration(connection) -> Dict:
    """
    Validate migration without making changes (dry run).

    Implements T024 specification: Migration validation mode.

    This function performs all discovery and analysis steps WITHOUT modifying
    the database. It provides a comprehensive report of what would happen
    during migration.

    Args:
        connection: IRIS database connection

    Returns:
        Dict with validation report containing:
        - 'discovered_nodes': List of node IDs that would be inserted
        - 'node_count': Total number of unique nodes discovered
        - 'orphans': Dict of orphaned references by table (should be empty)
        - 'ready_for_migration': Boolean indicating if migration can proceed
        - 'issues': List of any issues found
        - 'table_breakdown': Dict with counts per table

    Strategy:
        1. Run node discovery to find all existing nodes
        2. Run orphan detection to verify referential integrity
        3. Generate comprehensive report
        4. Do NOT modify database
    """
    logger = logging.getLogger(__name__)

    logger.info("=" * 70)
    logger.info("MIGRATION VALIDATION (DRY RUN)")
    logger.info("=" * 70)

    report = {
        'discovered_nodes': [],
        'node_count': 0,
        'orphans': {},
        'ready_for_migration': False,
        'issues': [],
        'table_breakdown': {}
    }

    try:
        # Step 1: Discover nodes
        logger.info("\n[1/2] Discovering unique node IDs...")
        discovered_nodes = discover_nodes(connection)
        report['discovered_nodes'] = discovered_nodes
        report['node_count'] = len(discovered_nodes)

        # Get breakdown by table
        cursor = connection.cursor()
        cursor.execute("SELECT COUNT(DISTINCT s) FROM rdf_labels")
        report['table_breakdown']['rdf_labels'] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(DISTINCT s) FROM rdf_props")
        report['table_breakdown']['rdf_props'] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(DISTINCT s) FROM rdf_edges")
        report['table_breakdown']['rdf_edges_source'] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(DISTINCT o_id) FROM rdf_edges")
        report['table_breakdown']['rdf_edges_dest'] = cursor.fetchone()[0]

        try:
            cursor.execute("SELECT COUNT(DISTINCT id) FROM kg_NodeEmbeddings")
            report['table_breakdown']['kg_NodeEmbeddings'] = cursor.fetchone()[0]
        except:
            report['table_breakdown']['kg_NodeEmbeddings'] = 0

        # Step 2: Check for orphans
        logger.info("\n[2/2] Checking for orphaned references...")
        orphans = detect_orphans(connection)
        report['orphans'] = orphans

        # Analyze results
        logger.info("\n" + "=" * 70)
        logger.info("VALIDATION REPORT")
        logger.info("=" * 70)

        logger.info(f"\nNode Discovery:")
        logger.info(f"  Total unique nodes: {report['node_count']}")
        logger.info(f"  Breakdown by table:")
        for table, count in report['table_breakdown'].items():
            logger.info(f"    {table}: {count} nodes")

        if orphans:
            logger.error(f"\n❌ ORPHANED REFERENCES FOUND:")
            total_orphans = sum(len(nodes) for nodes in orphans.values())
            logger.error(f"  Total orphans: {total_orphans}")
            for table, orphaned_nodes in orphans.items():
                logger.error(f"  {table}: {len(orphaned_nodes)} orphaned nodes")
                report['issues'].append(
                    f"Found {len(orphaned_nodes)} orphaned references in {table}"
                )
            logger.error(
                "\n⚠️  Migration cannot proceed with orphaned references.\n"
                "    Fix: Insert missing nodes or remove orphaned references before migration."
            )
            report['ready_for_migration'] = False
        else:
            logger.info(f"\n✅ NO ORPHANED REFERENCES")
            logger.info(f"✅ Database is ready for migration")
            report['ready_for_migration'] = True

        # Summary
        logger.info("\n" + "=" * 70)
        if report['ready_for_migration']:
            logger.info("RESULT: ✅ VALIDATION PASSED - Ready for migration")
            logger.info(f"        {report['node_count']} nodes will be inserted into nodes table")
        else:
            logger.error("RESULT: ❌ VALIDATION FAILED - Fix issues before migration")
            logger.error(f"        {len(report['issues'])} issue(s) found")
        logger.info("=" * 70 + "\n")

    except Exception as e:
        logger.error(f"Validation failed with error: {e}")
        report['issues'].append(f"Validation error: {e}")
        report['ready_for_migration'] = False
        raise

    return report


def execute_migration(connection) -> bool:
    """
    Execute the full NodePK migration.

    Implements T025 specification: Migration execution mode.

    This function performs the complete migration:
    1. Create nodes table (if not exists)
    2. Discover and insert all nodes
    3. Add foreign key constraints
    4. Validate referential integrity

    Args:
        connection: IRIS database connection

    Returns:
        bool: True if migration succeeded, False otherwise

    Raises:
        Exception: If migration fails at any step

    Strategy:
        1. Check if already migrated (nodes table exists with FK constraints)
        2. Run validation first to detect orphans
        3. Execute SQL migrations (nodes table + FK constraints)
        4. Discover and bulk insert nodes
        5. Verify referential integrity post-migration
    """
    logger = logging.getLogger(__name__)

    logger.info("=" * 70)
    logger.info("EXECUTING NODEPK MIGRATION")
    logger.info("=" * 70)

    cursor = connection.cursor()

    try:
        # Step 1: Check if already migrated
        logger.info("\n[1/6] Checking migration status...")
        try:
            cursor.execute("SELECT COUNT(*) FROM nodes WHERE 1=0")
            logger.info("  nodes table already exists")

            # Check if FK constraints already exist
            cursor.execute("""
                SELECT COUNT(*) FROM INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS
                WHERE constraint_name LIKE 'fk_%'
            """)
            fk_count = cursor.fetchone()[0]

            if fk_count >= 4:  # Expecting at least 4 FK constraints (edges s/o_id, labels, props)
                logger.info(f"  ✅ Migration appears complete ({fk_count} FK constraints found)")
                logger.info("  This is idempotent - verifying integrity...")
                # Continue to validation step
            else:
                logger.warning(f"  ⚠️  nodes table exists but only {fk_count} FK constraints found")
                logger.info("  Continuing migration to add missing constraints...")

        except Exception as e:
            if 'not found' in str(e).lower() or 'does not exist' in str(e).lower():
                logger.info("  nodes table does not exist - starting fresh migration")
            else:
                raise

        # Step 2: Pre-migration validation
        logger.info("\n[2/6] Running pre-migration validation...")

        # Discover nodes without inserting
        discovered_nodes = discover_nodes(connection)
        logger.info(f"  Discovered {len(discovered_nodes)} unique nodes")

        # Step 3: Create nodes table
        logger.info("\n[3/6] Creating nodes table...")
        try:
            execute_sql_migration(connection, 'sql/migrations/001_add_nodepk_table.sql')
            logger.info("  ✅ nodes table created")
        except Exception as e:
            if 'already exists' in str(e).lower():
                logger.info("  nodes table already exists (OK)")
            else:
                raise

        # Step 4: Bulk insert nodes
        logger.info("\n[4/6] Inserting nodes...")
        inserted_count = bulk_insert_nodes(connection, discovered_nodes)
        logger.info(f"  ✅ Inserted {inserted_count} nodes")

        # Step 5: Add FK constraints
        logger.info("\n[5/6] Adding foreign key constraints...")
        try:
            execute_sql_migration(connection, 'sql/migrations/002_add_fk_constraints.sql')
            logger.info("  ✅ FK constraints added")
        except Exception as e:
            error_msg = str(e).lower()
            if 'already exists' in error_msg or 'duplicate' in error_msg:
                logger.info("  FK constraints already exist (OK)")
            elif 'foreign key' in error_msg and 'failed' in error_msg:
                logger.error(f"  ❌ FK constraint validation failed: {e}")
                logger.error("  This means there are orphaned references in the database.")
                logger.error("  Run with --validate-only to identify orphans.")
                raise
            else:
                raise

        # Step 6: Post-migration validation
        logger.info("\n[6/6] Verifying referential integrity...")
        orphans = detect_orphans(connection)

        if orphans:
            total_orphans = sum(len(nodes) for nodes in orphans.values())
            logger.error(f"  ❌ Found {total_orphans} orphaned references!")
            for table, orphaned_nodes in orphans.items():
                logger.error(f"    {table}: {len(orphaned_nodes)} orphans")
            logger.error("  Migration completed but integrity issues detected.")
            return False
        else:
            logger.info("  ✅ No orphaned references - referential integrity verified")

        # Success summary
        logger.info("\n" + "=" * 70)
        logger.info("MIGRATION COMPLETED SUCCESSFULLY")
        logger.info("=" * 70)
        logger.info(f"\n  nodes table: ✅ Created with {len(discovered_nodes)} nodes")
        logger.info(f"  FK constraints: ✅ Added to rdf_edges, rdf_labels, rdf_props")
        logger.info(f"  Referential integrity: ✅ Verified")
        logger.info("\n" + "=" * 70 + "\n")

        return True

    except Exception as e:
        logger.error(f"\n❌ Migration failed: {e}")
        logger.error("Rolling back transaction...")
        connection.rollback()
        raise


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
            # Report is already logged by validate_migration()

        elif args.execute:
            logger.info("Executing migration...")
            success = execute_migration(connection)
            if success:
                logger.info("Migration completed successfully!")
                sys.exit(0)
            else:
                logger.error("Migration failed!")
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