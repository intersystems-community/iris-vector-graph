"""Library-wide constants.

Deliberately dependency-free so every module can import it without a cycle.
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
