import re
from typing import Set, Optional

# Standard table names in the Graph_KG schema
VALID_GRAPH_TABLES = {
    "nodes",
    "rdf_labels",
    "rdf_props",
    "rdf_edges",
    "kg_NodeEmbeddings",
    "kg_NodeEmbeddings_optimized",
    "docs"
}

def sanitize_identifier(identifier: str) -> str:
    """
    Sanitizes a SQL identifier (table name, column name, index name).
    Allows only alphanumeric characters and underscores.
    
    Args:
        identifier: The identifier to sanitize
        
    Returns:
        The sanitized identifier
        
    Raises:
        ValueError: If the identifier contains invalid characters
    """
    if not identifier:
        raise ValueError("Identifier cannot be empty")
    
    # Allow alphanumeric, underscores, and dots (for schema qualification)
    if not re.match(r'^[a-zA-Z0-9_\.]+$', identifier):
        raise ValueError(f"Invalid characters in identifier: {identifier}")
    
    return identifier

def validate_table_name(table_name: str, allowed_tables: Optional[Set[str]] = None) -> str:
    """
    Validates a table name against an allowlist.
    
    Args:
        table_name: The table name to validate
        allowed_tables: Optional set of allowed table names. Defaults to VALID_GRAPH_TABLES.
        
    Returns:
        The validated table name (possibly schema-qualified)
        
    Raises:
        ValueError: If the table name is not in the allowlist
    """
    if allowed_tables is None:
        allowed_tables = VALID_GRAPH_TABLES
        
    # Remove schema prefix for validation if present
    name_to_check = table_name
    if "." in table_name:
        name_to_check = table_name.split(".")[-1]
        
    if name_to_check not in allowed_tables:
        raise ValueError(f"Table '{table_name}' is not in the allowlist")
    
    return sanitize_identifier(table_name)
