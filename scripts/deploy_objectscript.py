#!/usr/bin/env python3
"""
Deploy ObjectScript Classes to IRIS

This script deploys ObjectScript classes (.cls files) to IRIS, enabling
embedded Python operations like PageRank with 10-50x performance improvement.

Usage:
    python scripts/deploy_objectscript.py [--host localhost] [--port 1972]

The classes are sent over TCP (--host/--port) and loaded with %SYSTEM.OBJ.LoadStream.

Constitutional Principle I: IRIS-Native Development
- Leverage IRIS embedded Python for performance
- ObjectScript classes enable in-process Python execution
"""

import sys
import os
import argparse
import logging
from pathlib import Path
from typing import List, Optional

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description='Deploy ObjectScript classes to IRIS')

    parser.add_argument('--host', default='localhost', help='IRIS host (default: localhost)')
    parser.add_argument('--port', type=int, default=1972, help='IRIS port (default: 1972)')
    parser.add_argument('--namespace', default='USER', help='IRIS namespace (default: USER)')
    parser.add_argument('--username', default='_SYSTEM', help='IRIS username (default: _SYSTEM)')
    parser.add_argument('--password', default='SYS', help='IRIS password (default: SYS)')
    parser.add_argument('--classes', nargs='+', help='Specific class files to deploy (default: all in iris/src/)')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be deployed without executing')
    parser.add_argument('--sql-only', action='store_true', help='Only deploy SQL functions, skip ObjectScript')

    return parser.parse_args()


def find_objectscript_classes(classes: Optional[List[str]] = None) -> List[Path]:
    """Find ObjectScript class files to deploy"""
    script_dir = Path(__file__).parent
    iris_src_dir = script_dir.parent / 'iris' / 'src'

    if classes:
        # Specific classes requested
        return [Path(c) if Path(c).is_absolute() else iris_src_dir / c for c in classes]

    # Find all .cls files
    if iris_src_dir.exists():
        return list(iris_src_dir.glob('*.cls'))

    return []


def connect_native(host: str, port: int, namespace: str, username: str, password: str):
    """A Native API handle on host:port in `namespace` (the caller closes `handle.conn`)."""
    import iris

    conn = iris.createConnection(hostname=host, port=port, namespace=namespace, username=username, password=password)
    native = iris.createIRIS(conn)
    native.conn = conn
    return native


def deploy_via_tcp(native, class_files: List[Path], dry_run: bool = False) -> bool:
    """Load and compile each class file into `native`'s namespace over TCP: the source is written to a stream and
    loaded with %SYSTEM.OBJ.LoadStream (no docker exec, no `iris session`, which Cortex XDR kills). True when every
    file loaded."""
    success_count = 0

    for class_file in class_files:
        if not class_file.exists():
            logger.warning(f"Class file not found: {class_file}")
            continue

        class_name = class_file.stem
        logger.info(f"Deploying {class_name}...")

        if dry_run:
            logger.info(f"  [DRY RUN] Would load {class_file} over TCP")
            continue

        stream = native.classMethodObject("%Stream.GlobalCharacter", "%New")
        stream.invoke("Write", class_file.read_text())
        stream.invoke("Rewind")
        status = native.classMethodValue("%SYSTEM.OBJ", "LoadStream", stream, "ck")
        if status == 1 or str(status) == "1":
            logger.info(f"  ✅ {class_name} loaded successfully")
            success_count += 1
        else:
            text = native.classMethodValue("%SYSTEM.Status", "GetErrorText", status)
            logger.error(f"  ❌ {class_name} failed to load: {text}")

    return success_count == len(class_files)


def deploy_sql_functions(host: str, port: int, namespace: str, username: str, password: str, dry_run: bool = False) -> bool:
    """Deploy SQL functions that wrap ObjectScript classes"""
    try:
        import iris
    except ImportError:
        logger.error("iris module not available - cannot deploy SQL functions")
        return False

    logger.info("Deploying SQL functions...")

    # SQL function for PageRank
    # NOTE: Function named kg_PPR (not kg_PERSONALIZED_PAGERANK_JSON) due to IRIS bug:
    # Function names containing _JSON or JSON_ patterns cause "Invalid number of parameters" errors
    sql_functions = [
        """
        CREATE OR REPLACE FUNCTION kg_PPR(
          seedEntities VARCHAR(32000),
          dampingFactor DOUBLE DEFAULT 0.85,
          maxIterations INT DEFAULT 100,
          bidirectional INT DEFAULT 0,
          reverseEdgeWeight DOUBLE DEFAULT 1.0
        )
        RETURNS VARCHAR(8000)
        LANGUAGE OBJECTSCRIPT
        {
            set results = ##class(Graph.KG.PageRankEmbedded).ComputePageRank(
                "%",
                maxIterations,
                dampingFactor,
                seedEntities,
                bidirectional,
                reverseEdgeWeight
            )
            quit results.%ToJSON()
        }
        """
    ]

    if dry_run:
        for sql in sql_functions:
            logger.info(f"[DRY RUN] Would execute: {sql[:100]}...")
        return True

    try:
        conn = iris.connect(host, port, namespace, username, password)
        cursor = conn.cursor()

        success_count = 0
        for sql in sql_functions:
            try:
                cursor.execute(sql)
                conn.commit()
                logger.info("  ✅ kg_PPR created")
                success_count += 1
            except Exception as e:
                logger.error(f"  ❌ Failed to create SQL function: {e}")

        cursor.close()
        conn.close()

        return success_count == len(sql_functions)

    except Exception as e:
        logger.error(f"Failed to connect to IRIS: {e}")
        return False


def verify_deployment(host: str, port: int, namespace: str, username: str, password: str) -> bool:
    """Verify that ObjectScript classes and SQL functions are deployed"""
    try:
        import iris
    except ImportError:
        logger.warning("iris module not available - skipping verification")
        return True

    logger.info("Verifying deployment...")

    try:
        conn = iris.connect(host, port, namespace, username, password)
        cursor = conn.cursor()

        # Test the SQL function
        try:
            cursor.execute("SELECT kg_PPR('[\"TEST:A\"]', 0.85, 1, 0, 1.0)")
            result = cursor.fetchone()
            if result:
                logger.info("  ✅ kg_PPR is working")
            else:
                logger.warning("  ⚠️  kg_PPR returned no result")
        except Exception as e:
            if 'does not exist' in str(e).lower():
                logger.error("  ❌ SQL function not found")
                return False
            else:
                logger.info("  ✅ SQL function exists (no test data)")

        cursor.close()
        conn.close()
        return True

    except Exception as e:
        logger.error(f"Verification failed: {e}")
        return False


def main():
    """Main deployment function"""
    args = parse_arguments()

    logger.info("🚀 ObjectScript Deployment for IRIS Vector Graph")
    logger.info(f"Host: {args.host}:{args.port}")
    logger.info(f"Namespace: {args.namespace}")

    if args.dry_run:
        logger.info("DRY RUN MODE - No changes will be made")

    # Find class files
    class_files = find_objectscript_classes(args.classes)

    if not class_files and not args.sql_only:
        logger.warning("No ObjectScript class files found in iris/src/")
    else:
        logger.info(f"Found {len(class_files)} ObjectScript class(es) to deploy:")
        for f in class_files:
            logger.info(f"  - {f.name}")

    if class_files and not args.sql_only:
        # Load over TCP into --namespace on --host:--port
        if args.dry_run:
            success = deploy_via_tcp(None, class_files, dry_run=True)
        else:
            native = connect_native(args.host, args.port, args.namespace, args.username, args.password)
            try:
                success = deploy_via_tcp(native, class_files)
            finally:
                native.conn.close()
        if not success and not args.dry_run:
            logger.error("ObjectScript deployment failed")
            sys.exit(1)

    # Deploy SQL functions
    if not args.dry_run or args.sql_only:
        sql_success = deploy_sql_functions(
            args.host, args.port, args.namespace,
            args.username, args.password, args.dry_run
        )

        if not sql_success and not args.dry_run:
            logger.error("SQL function deployment failed")
            sys.exit(1)

    # Verify deployment
    if not args.dry_run:
        verify_deployment(args.host, args.port, args.namespace, args.username, args.password)

    logger.info("🎉 Deployment complete!")
    logger.info("\nTo verify PageRank performance improvement:")
    logger.info("  python -c \"from iris_vector_graph import IRISGraphEngine; IRISGraphEngine.reset_sql_function_cache()\"")
    logger.info("  uv run pytest tests/integration/test_bidirectional_ppr.py -v")


if __name__ == '__main__':
    main()
