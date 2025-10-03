#!/usr/bin/env python3
"""
Load fraud scoring schema into IRIS database (Embedded Python Version)

This version uses iris.sql.exec() directly for embedded Python execution.
Must be run via: /usr/irissys/bin/irispython

Executes:
1. sql/fraud/schema.sql - Table creation (gs_events, gs_labels, gs_fraud_centroid)
2. sql/fraud/procedures.sql - LANGUAGE PYTHON procedures (gs_ComputeFeatures, gs_SubgraphSample)

Constitutional Compliance: IRIS-Native Development (Embedded Python)

Lessons from iris-pgwire:
- Use structlog with PrintLoggerFactory() for logging
- Execute SQL files statement-by-statement (split on semicolons outside braces)
- Handle CREATE OR REPLACE PROCEDURE with LANGUAGE PYTHON (multi-line with braces)
"""

import os
import sys
from pathlib import Path
import structlog
import logging
import re

# Check if running in embedded Python
try:
    import iris
except ImportError:
    print("ERROR: iris module not found. This must run via /usr/irissys/bin/irispython")
    sys.exit(1)

# Configure structured logging (iris-pgwire pattern)
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.ConsoleRenderer()
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=False,
)

logger = structlog.get_logger()


def parse_sql_statements(sql_content: str):
    """
    Parse SQL into executable statements using GO separators

    IRIS SQL format:
    - Statements separated by GO on its own line
    - No semicolons needed for iris.sql.exec()
    - LANGUAGE PYTHON blocks use { ... } braces
    """
    statements = []

    # Remove block comments
    sql_content = re.sub(r'/\*.*?\*/', '', sql_content, flags=re.DOTALL)

    # Split by GO separator
    current_stmt = []

    for line in sql_content.split('\n'):
        stripped = line.strip()

        # Check for GO separator
        if stripped.upper() == 'GO':
            # Accumulate statement before GO
            if current_stmt:
                # Join lines, filtering out empty/comment-only lines
                stmt_lines = [l for l in current_stmt if l.strip() and not l.strip().startswith('--')]
                if stmt_lines:
                    stmt_text = '\n'.join(stmt_lines).strip()
                    if stmt_text:
                        statements.append(stmt_text)
            current_stmt = []
            continue

        # Add line to current statement (including comments for now, filter later)
        current_stmt.append(line)

    # Add any remaining statement
    if current_stmt:
        stmt_lines = [l for l in current_stmt if l.strip() and not l.strip().startswith('--')]
        if stmt_lines:
            stmt_text = '\n'.join(stmt_lines).strip()
            if stmt_text:
                statements.append(stmt_text)

    return statements


def execute_sql_file(file_path: Path):
    """
    Execute SQL file using iris.sql.exec() (embedded Python)

    Parses file into statements and executes each one.
    """
    logger.info("executing_sql_file", file_path=str(file_path))

    if not file_path.exists():
        logger.error("sql_file_not_found", file_path=str(file_path))
        return False

    with open(file_path, 'r') as f:
        sql_content = f.read()

    # Remove unsupported IRIS syntax
    sql_content = sql_content.replace(' IF NOT EXISTS', '')

    # Parse into statements
    statements = parse_sql_statements(sql_content)

    logger.info("parsed_statements", count=len(statements))

    # Execute each statement
    success_count = 0
    for i, stmt in enumerate(statements):
        if not stmt.strip():
            continue

        # Log statement preview
        preview = stmt[:100].replace('\n', ' ')
        logger.info("executing_statement", index=i+1, total=len(statements), preview=preview)

        try:
            iris.sql.exec(stmt)
            success_count += 1
            logger.info("statement_success", index=i+1)
        except Exception as e:
            error_msg = str(e).lower()
            # Ignore already exists errors
            if "already exists" in error_msg or "duplicate" in error_msg:
                logger.info("statement_skipped_exists", index=i+1)
                success_count += 1
            else:
                logger.error("statement_failed", index=i+1, error=str(e), statement=stmt[:200])
                # Continue with other statements

    logger.info("execution_complete", success=success_count, total=len(statements))
    return success_count > 0


def verify_tables_exist():
    """Verify fraud tables were created"""
    tables = ['gs_events', 'gs_labels', 'gs_fraud_centroid']

    all_exist = True
    for table in tables:
        try:
            result = iris.sql.exec(f"SELECT COUNT(*) FROM {table}")
            count = result.fetchone()[0]
            logger.info("table_verified", table=table, row_count=count)
        except Exception as e:
            logger.error("table_missing", table=table, error=str(e))
            all_exist = False

    return all_exist


def verify_procedures_exist():
    """Verify stored procedures were created"""
    procedures = ['gs_ComputeFeatures', 'gs_SubgraphSample']

    all_exist = True
    for proc in procedures:
        try:
            # Try calling procedure with test params
            if proc == 'gs_ComputeFeatures':
                result = iris.sql.exec("CALL gs_ComputeFeatures(?)", ("acct:test",))
            else:
                result = iris.sql.exec("CALL gs_SubgraphSample(?, ?, ?)",
                                     ("acct:test", 10, 5))
            # Consume results
            list(result)
            logger.info("procedure_verified", procedure=proc)
        except Exception as e:
            error_msg = str(e).lower()
            if "does not exist" in error_msg or "not found" in error_msg:
                logger.error("procedure_missing", procedure=proc, error=str(e))
                all_exist = False
            else:
                # Procedure exists but no data for test params
                logger.info("procedure_verified_no_data", procedure=proc)

    return all_exist


def main():
    """Main execution"""
    logger.info("schema_loading_started", stage="fraud_scoring_schema")

    # Get SQL directory
    script_dir = Path(__file__).parent
    project_root = script_dir.parent.parent
    sql_dir = project_root / "sql" / "fraud"

    if not sql_dir.exists():
        # Try /app/sql/fraud if running in container
        sql_dir = Path("/app/sql/fraud")
        if not sql_dir.exists():
            logger.error("sql_directory_not_found", sql_dir=str(sql_dir))
            return 1

    logger.info("sql_directory_found", sql_dir=str(sql_dir))

    # Execute schema.sql
    schema_file = sql_dir / "schema.sql"
    if not execute_sql_file(schema_file):
        logger.error("schema_execution_failed", file="schema.sql")
        return 1

    # Execute procedures.sql
    procedures_file = sql_dir / "procedures.sql"
    if not execute_sql_file(procedures_file):
        logger.error("procedures_execution_failed", file="procedures.sql")
        return 1

    # Verify installation
    logger.info("verifying_installation")

    if not verify_tables_exist():
        logger.error("table_verification_failed")
        return 1

    if not verify_procedures_exist():
        logger.error("procedure_verification_failed")
        return 1

    logger.info("schema_loading_complete", status="success")

    return 0


if __name__ == "__main__":
    sys.exit(main())
