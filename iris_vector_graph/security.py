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
    "kg_EdgeEmbeddings",
    "docs",
    "fhir_bridges",
    "rdf_reifications",
    "kg_IVFMeta",
    "kg_BM25Meta",
    "kg_PlaidMeta",
    # spec 226: what model produced each embedding table's vectors, and how wide they are
    "embedding_registry",
    # spec 227: where a vector goes when its graph cannot be recovered. Allowlisted
    # because initialize_schema creates it through `_t()`, which validates every name
    # it is handed — an unlisted table makes schema initialisation raise, not degrade.
    "embedding_quarantine",
    # spec 227: the 4.0.0 migration drains a 3.2.0 table into one of these and renames
    # it into the source's place. Both names are constants built from SOURCE_TABLES, but
    # they reach the same `_t()` every other table does, so an unlisted name would make
    # the migration raise a validation error rather than a migration error.
    "kg_NodeEmbeddings_ivg400",
    "kg_NodeEmbeddings_optimized_ivg400",
}

#: Spec 227 (FR-012): a routed embedding table, `kg_emb_` + 16 hex characters.
#:
#: Routed names cannot be listed — there is one per `(graph, model)` pair and the
#: graph is caller-supplied — so the allowlist admits the *shape* instead. The
#: shape is closed: exactly 16 lowercase hex characters, which is what
#: `routing.route_table_name` produces and nothing else. A caller-supplied string
#: cannot reach this pattern without already being a sha256 prefix, so the
#: injection surface the allowlist exists to close stays closed.
_ROUTED_EMBEDDING_TABLE = re.compile(r"^kg_emb_[0-9a-f]{16}$")

#: Spec 230 (FR-006): a routed *edge* embedding table, `kg_eemb_` + 16 hex.
#:
#: Its own pattern rather than an alternation inside the node one, because the two
#: answers are used for different decisions: a node route can be handed to the node
#: DDL and the node row count, an edge route cannot. A single predicate matching both
#: would let an edge route through every check written for nodes.
_ROUTED_EDGE_EMBEDDING_TABLE = re.compile(r"^kg_eemb_[0-9a-f]{16}$")


def is_routed_embedding_table(name: str) -> bool:
    """Is `name` a spec 227 routed *node* embedding table name?

    Answers about the name only. Whether such a table or registry row exists is a
    database question — see `resolve_route`.

    False for an edge route: `kg_eemb_…` holds `(graph_id, s, p, o_id)` rows, so
    admitting it here would hand a table of edge triples to the node paths. See
    `is_routed_edge_embedding_table`.
    """
    return bool(name) and bool(_ROUTED_EMBEDDING_TABLE.match(name))


def is_routed_edge_embedding_table(name: str) -> bool:
    """Is `name` a spec 230 routed edge embedding table name?

    Answers about the name only, exactly as `is_routed_embedding_table` does.
    """
    return bool(name) and bool(_ROUTED_EDGE_EMBEDDING_TABLE.match(name))

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

    Spec 227 routed embedding tables (``kg_emb_<16 hex>``) and spec 230 routed edge
    embedding tables (``kg_eemb_<16 hex>``) are admitted by shape when the default
    allowlist is in force — they cannot be enumerated, since there is one per
    ``(graph, model)`` pair. A caller that passes its own ``allowed_tables`` is
    narrowing deliberately and gets exactly what it listed.
    """
    routed_ok = allowed_tables is None
    if allowed_tables is None:
        allowed_tables = VALID_GRAPH_TABLES

    # Remove schema prefix for validation if present
    name_to_check = table_name
    if "." in table_name:
        name_to_check = table_name.split(".")[-1]

    if name_to_check not in allowed_tables and not (
        routed_ok
        and (
            is_routed_embedding_table(name_to_check)
            or is_routed_edge_embedding_table(name_to_check)
        )
    ):
        raise ValueError(f"Table '{table_name}' is not in the allowlist")
    
    return sanitize_identifier(table_name)
