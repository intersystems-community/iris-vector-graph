"""
Setup utilities for IRIS Vector Graph.

Provides data loading functions for biomedical and fraud detection domains.
"""

import os
import logging
import re
from pathlib import Path

logger = logging.getLogger("scripts.setup")


def _decompose_multi_row_insert(stmt: str) -> list[str]:
    """
    Decompose a multi-row INSERT statement into single-row INSERTs.
    
    IRIS SQL does not support: INSERT INTO t(a,b) VALUES (1,2), (3,4);
    This function converts it to:
        INSERT INTO t(a,b) VALUES (1,2);
        INSERT INTO t(a,b) VALUES (3,4);
    
    Handles:
    - Quoted strings with commas inside
    - Nested parentheses (e.g., function calls like TO_VECTOR('[1,2,3]'))
    - NULL values
    """
    stmt = stmt.strip()
    if not stmt:
        return []
    
    # Check if this is an INSERT ... VALUES statement
    insert_match = re.match(
        r"(INSERT\s+(?:%OR\s+%IGNORE\s+)?INTO\s+\S+\s*\([^)]*\)\s*VALUES)\s*(.+)",
        stmt,
        re.IGNORECASE | re.DOTALL
    )
    if not insert_match:
        # Not a VALUES insert, return as-is
        return [stmt]
    
    prefix = insert_match.group(1)  # "INSERT INTO table(cols) VALUES"
    values_part = insert_match.group(2).strip()
    
    # Remove trailing semicolon if present
    if values_part.endswith(';'):
        values_part = values_part[:-1].strip()
    
    # Parse value tuples, respecting quotes and nested parentheses
    tuples = []
    current_tuple = []
    paren_depth = 0
    in_single_quote = False
    in_double_quote = False
    i = 0
    
    while i < len(values_part):
        ch = values_part[i]
        
        # Handle escape sequences in quotes
        if (in_single_quote or in_double_quote) and ch == '\\' and i + 1 < len(values_part):
            current_tuple.append(ch)
            current_tuple.append(values_part[i + 1])
            i += 2
            continue
        
        # Handle quotes
        if ch == "'" and not in_double_quote:
            # Check for escaped quote ('')
            if in_single_quote and i + 1 < len(values_part) and values_part[i + 1] == "'":
                current_tuple.append("''")
                i += 2
                continue
            in_single_quote = not in_single_quote
            current_tuple.append(ch)
            i += 1
            continue
        
        if ch == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
            current_tuple.append(ch)
            i += 1
            continue
        
        # If we're in quotes, just append
        if in_single_quote or in_double_quote:
            current_tuple.append(ch)
            i += 1
            continue
        
        # Handle parentheses
        if ch == '(':
            paren_depth += 1
            current_tuple.append(ch)
            i += 1
            continue
        
        if ch == ')':
            paren_depth -= 1
            current_tuple.append(ch)
            # If we just closed the outermost paren, we have a complete tuple
            if paren_depth == 0 and current_tuple:
                tuples.append(''.join(current_tuple).strip())
                current_tuple = []
            i += 1
            continue
        
        # Handle comma between tuples (only at depth 0)
        if ch == ',' and paren_depth == 0:
            # Skip whitespace and comma between tuples
            i += 1
            continue
        
        # Regular character
        if paren_depth > 0:
            current_tuple.append(ch)
        i += 1
    
    # Handle any remaining tuple
    if current_tuple:
        remaining = ''.join(current_tuple).strip()
        if remaining:
            tuples.append(remaining)
    
    # If only one tuple, return original statement
    if len(tuples) <= 1:
        return [stmt]
    
    # Generate single-row inserts
    return [f"{prefix} {t}" for t in tuples]


def get_project_root() -> Path:
    """Get the project root directory."""
    return Path(__file__).parent.parent.parent


def _ensure_nodes_exist(connection, sql_content):
    """Scan SQL for entity IDs and ensure they exist in the nodes table."""
    cursor = connection.cursor()
    
    # Find all single-quoted strings that look like entity IDs (contain a colon)
    found_ids = set(re.findall(r"'([A-Za-z0-9_]+:[A-Za-z0-9_\-\.]+)'", sql_content))
    
    if not found_ids:
        return

    logger.info(f"Pre-loading {len(found_ids)} nodes for referential integrity")

    for node_id in found_ids:
        if len(node_id) < 256:
            try:
                # Use %OR %IGNORE for idempotency
                cursor.execute("INSERT %OR %IGNORE INTO nodes (node_id) VALUES (?)", (node_id,))
            except Exception:
                pass
    
    connection.commit()


def load_sql_file(connection, sql_file_path):
    """Load and execute a SQL file robustly.
    
    Handles IRIS SQL limitations:
    - Decomposes multi-row INSERTs into single-row INSERTs
    - Strips trailing semicolons (except for END;)
    - Ignores benign errors (already exists, does not exist)
    """
    if not sql_file_path.exists():
        raise FileNotFoundError(f"SQL file not found: {sql_file_path}")
        
    with open(sql_file_path, 'r') as f:
        sql_content = f.read()
        
    # 1. Ensure nodes exist for FK integrity
    _ensure_nodes_exist(connection, sql_content)
    
    # 2. Split into statements using the robust splitter
    from tests.conftest import _split_sql_statements
    statements = _split_sql_statements(sql_content)
    
    cursor = connection.cursor()
    for stmt in statements:
        stmt = stmt.strip()
        if not stmt:
            continue
        
        # 3. Decompose multi-row INSERTs (IRIS doesn't support them)
        decomposed = _decompose_multi_row_insert(stmt)
        
        for single_stmt in decomposed:
            single_stmt = single_stmt.strip()
            if not single_stmt:
                continue
                
            try:
                # IRIS SQL sometimes doesn't like trailing semicolons in execute()
                if single_stmt.endswith(';') and not single_stmt.upper().endswith('END;'):
                    single_stmt = single_stmt[:-1]
                    
                cursor.execute(single_stmt)
            except Exception as e:
                # Benign errors (already exists, etc.)
                msg = str(e).lower()
                if "already exists" in msg or "does not exist" in msg:
                    continue
                logger.warning(f"Statement failed in {sql_file_path.name}: {e}")
                if "validation" in msg or "sqlcode" in msg:
                    logger.debug(f"Failed statement: {single_stmt[:200]}...")
                
    connection.commit()


def load_sample_data(connection=None):
    """Load biomedical sample data."""
    from iris_devtester.utils.dbapi_compat import get_connection as dbapi_connect
    from iris_devtester.connections import auto_detect_iris_host_and_port
    
    if connection is None:
        host, port = auto_detect_iris_host_and_port()
        connection = dbapi_connect(host or 'localhost', port or 1972, 'USER', '_SYSTEM', 'SYS')
    
    sql_file = get_project_root() / "scripts" / "sample_data_768.sql"
    load_sql_file(connection, sql_file)
    print("Biomedical sample data loaded successfully")


def load_fraud_data(connection=None):
    """Load fraud detection sample data."""
    from iris_devtester.utils.dbapi_compat import get_connection as dbapi_connect
    from iris_devtester.connections import auto_detect_iris_host_and_port
    
    if connection is None:
        host, port = auto_detect_iris_host_and_port()
        connection = dbapi_connect(host or 'localhost', port or 1972, 'USER', '_SYSTEM', 'SYS')
    
    sql_file = get_project_root() / "sql" / "fraud_sample_data.sql"
    if not sql_file.exists():
        sql_file = get_project_root() / "src" / "iris_demo_server" / "sql" / "fraud_sample_data.sql"
        
    load_sql_file(connection, sql_file)
    print("Fraud detection sample data loaded successfully")


def verify_data_loaded(connection=None) -> dict:
    """Verify sample data is loaded and return counts."""
    from iris_devtester.utils.dbapi_compat import get_connection as dbapi_connect
    from iris_devtester.connections import auto_detect_iris_host_and_port
    
    if connection is None:
        host, port = auto_detect_iris_host_and_port()
        connection = dbapi_connect(host or 'localhost', port or 1972, 'USER', '_SYSTEM', 'SYS')
    
    cursor = connection.cursor()
    counts = {}
    
    for label in ['Gene', 'Disease', 'Drug', 'Protein', 'Account', 'Transaction', 'Alert']:
        try:
            cursor.execute("SELECT COUNT(*) FROM rdf_labels WHERE label = ?", (label,))
            counts[label] = cursor.fetchone()[0]
        except Exception:
            counts[label] = 0
            
    try:
        cursor.execute("SELECT COUNT(*) FROM kg_NodeEmbeddings")
        counts['embeddings'] = cursor.fetchone()[0]
    except Exception:
        counts['embeddings'] = 0
        
    return counts
