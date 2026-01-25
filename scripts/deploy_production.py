#!/usr/bin/env python3
"""
Production Deployment Script for IRIS Vector Graph.
Deploys schema, operators, security roles, and ObjectScript classes.
Configurable via CLI arguments or environment variables.
"""

import argparse
import os
import subprocess
import sys
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def run_command(cmd, input_text=None, check=True):
    """Execute a system command and return the result."""
    try:
        result = subprocess.run(
            cmd, 
            input=input_text, 
            capture_output=True, 
            text=True, 
            errors='replace',
            check=check
        )
        return result
    except subprocess.CalledProcessError as e:
        logger.error(f"Command failed: {' '.join(e.cmd)}")
        if e.stdout:
            logger.error(f"Stdout: {e.stdout}")
        if e.stderr:
            logger.error(f"Stderr: {e.stderr}")
        raise

def deploy_via_docker(container_name, schema, namespace):
    """Deploy using docker exec and file copying (recommended for IRIS)."""
    logger.info(f"Deploying to Docker container: {container_name}")
    
    status = run_command(['docker', 'inspect', '-f', '{{.State.Running}}', container_name], check=False)
    if status.returncode != 0 or 'true' not in status.stdout.lower():
        logger.error(f"Container '{container_name}' is not running or does not exist.")
        sys.exit(1)

    run_command(['docker', 'exec', container_name, 'mkdir', '-p', '/tmp/src'])
    
    logger.info("Copying source and SQL files to container...")
    run_command(['docker', 'cp', 'iris_src/src/.', f"{container_name}:/tmp/src/"])
    run_command(['docker', 'cp', 'sql/schema.sql', f"{container_name}:/tmp/schema.sql"])
    run_command(['docker', 'cp', 'sql/operators_fixed.sql', f"{container_name}:/tmp/operators_fixed.sql"])
    run_command(['docker', 'cp', 'scripts/setup_security.sql', f"{container_name}:/tmp/setup_security.sql"])
    
    setup_script = f"""iris session iris -U {namespace} <<EOF
sql
SET SCHEMA {schema};
\\i /tmp/schema.sql
\\i /tmp/operators_fixed.sql
\\i /tmp/setup_security.sql
SELECT count(*) as table_count FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = '{schema}';
q
-- Load IRIS ObjectScript classes
Do \\$system.OBJ.LoadDir("/tmp/src", "ck", .errors, 1)
H
EOF
"""
    logger.info("Executing deployment script inside IRIS session...")
    result = run_command(['docker', 'exec', '-i', container_name, 'bash'], input_text=setup_script)
    
    if result.stdout:
        logger.info(f"Deployment Output:\n{result.stdout}")
    
    logger.info("Docker deployment completed successfully.")

def deploy_via_sql(host, port, namespace, user, password, schema):
    """Deploy using direct SQL connection (limited - cannot load ObjectScript)."""
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
        
        logger.warning("SQL deployment completed (PARTIAL: ObjectScript classes were NOT deployed).")
    except Exception as e:
        logger.error(f"SQL deployment failed: {e}")
        raise

def main():
    parser = argparse.ArgumentParser(description="Production Deployment for IRIS Vector Graph")
    parser.add_argument("--mode", choices=['docker', 'sql'], default='docker', 
                        help="Deployment mode (docker recommended for full deployment)")
    parser.add_argument("--container", default=os.getenv("IRIS_CONTAINER", "iris-vector-graph"), 
                        help="Docker container name (docker mode only)")
    parser.add_argument("--host", default=os.getenv("IRIS_HOST", "localhost"), help="IRIS host")
    parser.add_argument("--port", type=int, default=int(os.getenv("IRIS_PORT", "1972")), help="IRIS port")
    parser.add_argument("--namespace", default=os.getenv("IRIS_NAMESPACE", "USER"), help="IRIS namespace")
    parser.add_argument("--user", default=os.getenv("IRIS_USER", "_SYSTEM"), help="IRIS username")
    parser.add_argument("--password", default=os.getenv("IRIS_PASSWORD", "SYS"), help="IRIS password")
    parser.add_argument("--schema", default=os.getenv("IRIS_SCHEMA", "User"), help="Target SQL schema")
    
    args = parser.parse_args()
    
    try:
        if args.mode == 'docker':
            deploy_via_docker(args.container, args.schema, args.namespace)
        else:
            deploy_via_sql(args.host, args.port, args.namespace, args.user, args.password, args.schema)
    except Exception as e:
        logger.error(f"Deployment failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
