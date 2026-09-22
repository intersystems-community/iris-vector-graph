"""Library-wide constants.

Deliberately dependency-free so every module can import it without a cycle.

A graph ID is collision avoidance, not an authorisation boundary: it keeps two
callers' data from overwriting each other, and it is supplied by the caller, so
it cannot decide what that caller is allowed to read (spec 227 FR-032).
"""

#: Vector width used when a caller does not name one.
#:
#: This is the fallback for public entry points that must keep working without
#: an explicit dimension — ``GraphSchema.get_base_schema_sql``,
#: ``GraphSchema.get_procedures_sql_list``, the ``/admin/schema`` request model,
#: the ``ivg`` CLI and ``EngineStatus``. Before 3.1.0 those sites carried their
#: own literals and ``get_procedures_sql_list`` disagreed with the rest at 1000,
#: so the retrieval procedures could be declared at a width the tables did not
#: have. One constant, one width.
#:
#: ``IRISGraphEngine.initialize_schema`` does *not* use this: it raises unless
#: the dimension was set at construction or inferred from a stored embedding.
#: A caller that has embeddings knows their width.
DEFAULT_EMBEDDING_DIMENSION = 768

#: Table names in the Graph_KG schema whose ``emb`` column is a VECTOR.
#: Order matters only for log readability.
VECTOR_TABLE_NAMES = (
    "kg_NodeEmbeddings",
    "kg_NodeEmbeddings_optimized",
    "kg_EdgeEmbeddings",
)

#: The table ``GraphSchema.get_embedding_dimension`` answers about by default.
DEFAULT_EMBEDDING_TABLE = "Graph_KG.kg_NodeEmbeddings"

#: The graph a call means when it names none.
#:
#: ``graph=None`` means *this* graph, not *every* graph — there is no value that
#: means every graph (spec 227 FR-004). Specs 214 and 223 established ``""`` as
#: the default graph in the relational tables and ``0`` in the ``^KG`` globals;
#: this is the relational half.
DEFAULT_GRAPH = ""

#: Prefix of a routed embedding table: ``kg_emb_`` + 16 hex of the route hash.
#:
#: The name is deliberately unreadable. A readable ``graph_model`` concatenation
#: breaks on two IRIS rules at once: DDL strips underscores when deriving the
#: class name and de-duplicates collisions with a numeric suffix, and the derived
#: class name has a 220-character ceiling (spec 227 research R4). Never recompute
#: a route name to find an existing route — read the registry row.
ROUTE_TABLE_PREFIX = "kg_emb_"

#: Prefix of a routed *edge* embedding table: ``kg_eemb_`` + 16 hex (spec 230 FR-006).
#:
#: A separate prefix, not a separate hash: the two kinds of route have to be
#: distinguishable by name, because code that classifies a table by
#: ``name.startswith(ROUTE_TABLE_PREFIX)`` — the admin row count and the migration's
#: table scan both do — would otherwise read an edge route as a node route and
#: count edge triples as nodes. ``kg_eemb_`` is not reachable by prefix from
#: ``kg_emb_`` in either direction, so neither test can match the other's tables.
#:
#: The key spaces differ too: a node route holds ``(graph_id, node_id)`` with a
#: composite FK to ``nodes``, an edge route holds ``(graph_id, s, p, o_id)``. One
#: table cannot be both, so one name cannot serve both.
EDGE_ROUTE_TABLE_PREFIX = "kg_eemb_"

#: Rows per call to the ObjectScript bulk-ingest fast paths.
#:
#: The chunk exists because the JSON payload crosses into ObjectScript as one
#: argument. It lived in ``engine.py`` while the only two readers were in
#: ``_engine/nodes_edges.py``, where the name was undefined — both fast paths
#: raised ``NameError`` on their first loop and their handlers logged
#: "falling back to SQL path", so a deployed container took the row-at-a-time
#: path and said so only at WARNING.
BULK_CHUNK_SIZE = 1000
