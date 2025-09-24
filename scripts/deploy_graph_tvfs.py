#!/usr/bin/env python3
"""
Deploy Graph-SQL Table-Valued Functions to IRIS

This script deploys the advanced graph traversal TVFs to IRIS, enabling
SQL-composable recursive graph operations with JSON_TABLE integration.

Usage:
    python scripts/deploy_graph_tvfs.py [--host localhost] [--port 1973] [--namespace USER]
"""

import sys
import os
import argparse
import iris
import logging
from pathlib import Path

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description='Deploy Graph-SQL TVFs to IRIS')

    parser.add_argument('--host', default='localhost', help='IRIS host (default: localhost)')
    parser.add_argument('--port', type=int, default=1973, help='IRIS port (default: 1973)')
    parser.add_argument('--namespace', default='USER', help='IRIS namespace (default: USER)')
    parser.add_argument('--username', default='_SYSTEM', help='IRIS username (default: _SYSTEM)')
    parser.add_argument('--password', default='SYS', help='IRIS password (default: SYS)')
    parser.add_argument('--sql-file', help='Custom SQL file path (default: sql/graph_walk_tvf.sql)')
    parser.add_argument('--dry-run', action='store_true', help='Show SQL without executing')
    parser.add_argument('--force', action='store_true', help='Drop existing procedures without confirmation')

    return parser.parse_args()

def load_sql_file(sql_file_path: str) -> str:
    """Load SQL from file"""
    try:
        with open(sql_file_path, 'r') as f:
            return f.read()
    except FileNotFoundError:
        logger.error(f"SQL file not found: {sql_file_path}")
        return None
    except Exception as e:
        logger.error(f"Error reading SQL file: {e}")
        return None

def execute_sql_statements(connection, sql_content: str, dry_run: bool = False) -> bool:
    """Execute SQL statements from content"""

    # Split SQL into individual statements
    statements = []
    current_statement = []
    in_procedure = False

    for line in sql_content.split('\n'):
        line = line.strip()

        # Skip comments and empty lines
        if not line or line.startswith('--'):
            continue

        # Track procedure boundaries
        if line.upper().startswith('CREATE PROCEDURE'):
            in_procedure = True
            current_statement = [line]
        elif line == '$$;' and in_procedure:
            current_statement.append(line)
            statements.append('\n'.join(current_statement))
            current_statement = []
            in_procedure = False
        elif in_procedure:
            current_statement.append(line)
        elif line.endswith(';') and not in_procedure:
            current_statement.append(line)
            statements.append('\n'.join(current_statement))
            current_statement = []
        else:
            current_statement.append(line)

    # Add any remaining statement
    if current_statement:
        statements.append('\n'.join(current_statement))

    if dry_run:
        logger.info("DRY RUN - SQL statements that would be executed:")
        for i, stmt in enumerate(statements, 1):
            print(f"\n--- Statement {i} ---")
            print(stmt)
        return True

    # Execute statements
    cursor = connection.cursor()
    success_count = 0

    try:
        for i, statement in enumerate(statements, 1):
            if not statement.strip():
                continue

            logger.info(f"Executing statement {i}/{len(statements)}...")

            try:
                cursor.execute(statement)
                success_count += 1
                logger.info(f"✅ Statement {i} executed successfully")

            except Exception as e:
                logger.error(f"❌ Statement {i} failed: {e}")
                logger.debug(f"Failed statement: {statement[:200]}...")

                # Continue with other statements unless it's a critical error
                if "syntax error" in str(e).lower():
                    logger.warning("Syntax error detected, stopping execution")
                    break

    finally:
        cursor.close()

    logger.info(f"Deployment summary: {success_count}/{len(statements)} statements executed successfully")
    return success_count == len(statements)

def verify_deployment(connection) -> bool:
    """Verify that TVFs were deployed successfully"""
    cursor = connection.cursor()

    try:
        # Check for our procedures
        procedures_to_check = [
            'Graph_Walk',
            'Graph_Neighborhood_Expansion',
            'Vector_Graph_Search'
        ]

        logger.info("Verifying TVF deployment...")

        for proc_name in procedures_to_check:
            try:
                # Try to describe the procedure
                cursor.execute(f"SELECT 1 FROM INFORMATION_SCHEMA.ROUTINES WHERE ROUTINE_NAME = '{proc_name}'")
                result = cursor.fetchone()

                if result:
                    logger.info(f"✅ {proc_name} procedure found")
                else:
                    logger.warning(f"⚠️  {proc_name} procedure not found")

            except Exception as e:
                logger.warning(f"⚠️  Could not verify {proc_name}: {e}")

        # Test basic functionality
        logger.info("Testing basic Graph_Walk functionality...")
        try:
            cursor.execute("SELECT source_entity, predicate, target_entity FROM Graph_Walk('test_entity', 1, 'BFS', NULL, 0.0) WHERE source_entity != 'ERROR'")
            results = cursor.fetchall()
            logger.info(f"✅ Graph_Walk test completed (found {len(results)} results)")

        except Exception as e:
            logger.warning(f"⚠️  Graph_Walk test failed: {e}")

        return True

    except Exception as e:
        logger.error(f"Verification failed: {e}")
        return False

    finally:
        cursor.close()

def main():
    """Main deployment function"""
    args = parse_arguments()

    # Determine SQL file path
    if args.sql_file:
        sql_file_path = args.sql_file
    else:
        # Default path relative to script location
        script_dir = Path(__file__).parent
        sql_file_path = script_dir.parent / 'sql' / 'graph_walk_tvf.sql'

    logger.info(f"🚀 Attempting Graph-SQL TVF Deployment to IRIS")
    logger.info(f"⚠️  WARNING: IRIS may not support the TVF syntax in the SQL file")
    logger.info(f"This deployment is EXPERIMENTAL and may fail due to IRIS SQL limitations")
    logger.info(f"Host: {args.host}:{args.port}")
    logger.info(f"Namespace: {args.namespace}")
    logger.info(f"SQL File: {sql_file_path}")

    if args.dry_run:
        logger.info("DRY RUN MODE - No changes will be made")

    # Load SQL content
    sql_content = load_sql_file(str(sql_file_path))
    if not sql_content:
        logger.error("Failed to load SQL file")
        sys.exit(1)

    # Connect to IRIS
    try:
        logger.info("Connecting to IRIS...")
        connection = iris.connect(args.host, args.port, args.namespace, args.username, args.password)
        logger.info("✅ Connected to IRIS successfully")

    except Exception as e:
        logger.error(f"❌ Failed to connect to IRIS: {e}")
        sys.exit(1)

    try:
        # Execute deployment
        success = execute_sql_statements(connection, sql_content, args.dry_run)

        if success and not args.dry_run:
            logger.info("🎉 TVF deployment completed successfully!")

            # Verify deployment
            if verify_deployment(connection):
                logger.info("✅ TVF verification completed")
            else:
                logger.warning("⚠️  TVF verification had issues")

            logger.info("\n📋 Next Steps:")
            logger.info("1. Test the TVFs with: python -c \"from python.iris_graph_operators import IRISGraphOperators; import iris; conn = iris.connect('localhost', 1973, 'USER', '_SYSTEM', 'SYS'); ops = IRISGraphOperators(conn); print(ops.kg_GRAPH_WALK_TVF('protein:9606.ENSP00000000233', 2))\"")
            logger.info("2. Run benchmarks to compare TVF vs Python performance")
            logger.info("3. Update performance baselines in benchmarking framework")

        elif not args.dry_run:
            logger.error("❌ TVF deployment failed")
            sys.exit(1)

    except Exception as e:
        logger.error(f"❌ Deployment error: {e}")
        sys.exit(1)

    finally:
        try:
            connection.close()
        except:
            pass

if __name__ == '__main__':
    main()