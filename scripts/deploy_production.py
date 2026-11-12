#!/usr/bin/env python3
"""
Production Deployment Script for IRIS Vector Graph.
Deploys schema, operators, security roles, and ObjectScript classes.
Configurable via CLI arguments or environment variables.
"""

import argparse
import os
import sys
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def _connect(host, port, namespace, user, password):
    import iris

    return iris.connect(hostname=host, port=port, namespace=namespace, username=user, password=password)


def _deploy_packaged_classes(conn):
    from iris_vector_graph._engine.class_deploy import deploy_packaged_classes

    return deploy_packaged_classes(conn)


def deploy_classes(host, port, namespace, user, password):
    """Load and compile IVG's ObjectScript classes (iris_src/src) into `namespace` over TCP, as the engine's own
    deploy does: no docker exec and no `iris session`, which Cortex XDR kills. Raises on a class that failed."""
    logger.info(f"Deploying ObjectScript classes over TCP to {host}:{port} ({namespace})")
    conn = _connect(host, port, namespace, user, password)
    try:
        result = _deploy_packaged_classes(conn)
    finally:
        conn.close()
    errors = list(result.errors) if result is not None else []
    if errors:
        raise RuntimeError(f"ObjectScript class deploy failed: {errors}")
    logger.info("ObjectScript classes deployed.")


def deploy_via_sql(host, port, namespace, user, password, schema):
    """Run the SQL files over a direct SQL connection (the ObjectScript classes are deploy_classes')."""
    try:
        import iris
    except ImportError:
        logger.error("intersystems-irispython not found. Run 'pip install intersystems-irispython'.")
        sys.exit(1)
        
    logger.info(f"Deploying via SQL connection to {host}:{port} ({namespace})")
    
    try:
        conn = iris.connect(
            hostname=host,
            port=port,
            namespace=namespace,
            username=user,
            password=password
        )
        cursor = conn.cursor()
        
        cursor.execute(f"SET SCHEMA {schema}")
        
        for file_path in ['sql/schema.sql', 'sql/operators_fixed.sql', 'scripts/setup_security.sql']:
            logger.info(f"Processing {file_path}...")
            with open(file_path, 'r') as f:
                content = f.read()
                statements = content.split(';')
                for stmt in statements:
                    if stmt.strip():
                        try:
                            cursor.execute(stmt)
                        except Exception as e:
                            logger.warning(f"Statement failed in {file_path}: {e}")
                            logger.debug(f"Failed statement: {stmt}")
        
        conn.commit()
        cursor.close()
        conn.close()
        
        logger.info("SQL files applied.")
    except Exception as e:
        logger.error(f"SQL deployment failed: {e}")
        raise

def main():
    parser = argparse.ArgumentParser(description="Production Deployment for IRIS Vector Graph")
    parser.add_argument("--mode", choices=['full', 'docker', 'sql'], default='full',
                        help="full: the SQL files, then the ObjectScript classes, both over TCP (docker: the same, the "
                             "name it had when it ran docker exec); sql: the SQL files only")
    parser.add_argument("--host", default=os.getenv("IRIS_HOST", "localhost"), help="IRIS host")
    parser.add_argument("--port", type=int, default=int(os.getenv("IRIS_PORT", "1972")), help="IRIS port")
    parser.add_argument("--namespace", default=os.getenv("IRIS_NAMESPACE", "USER"), help="IRIS namespace")
    parser.add_argument("--user", default=os.getenv("IRIS_USER", "_SYSTEM"), help="IRIS username")
    parser.add_argument("--password", default=os.getenv("IRIS_PASSWORD", "SYS"), help="IRIS password")
    parser.add_argument("--schema", default=os.getenv("IRIS_SCHEMA", "User"), help="Target SQL schema")
    
    args = parser.parse_args()
    
    try:
        deploy_via_sql(args.host, args.port, args.namespace, args.user, args.password, args.schema)
        if args.mode in ('full', 'docker'):
            deploy_classes(args.host, args.port, args.namespace, args.user, args.password)
        else:
            logger.warning("SQL deployment completed (PARTIAL: ObjectScript classes were NOT deployed).")
    except Exception as e:
        logger.error(f"Deployment failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
