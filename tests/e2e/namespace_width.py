"""The width a namespace's vector columns are declared at, for an E2E test in a shared namespace.

4.7.0 row 25: `initialize_schema()` at another width raises `EmbeddingIdentityConflict` rather
than re-declaring the namespace's empty vector columns, and `rewidth=True` still refuses a column
that holds vectors. The suite's shared namespace is set up at 768 (tests/conftest.py's
SESSION_EMBEDDING_DIM) and often holds vectors another test stored, so a test whose subject is not
the width builds its engine at the width it finds there (`of`).
"""

from __future__ import annotations

from tests.conftest import SESSION_EMBEDDING_DIM


def of(conn) -> int:
    """`Graph_KG.kg_NodeEmbeddings.emb`'s declared width, or the session width where the
    namespace has no such column yet."""
    from iris_vector_graph.schema import GraphSchema

    cursor = conn.cursor()
    try:
        width = GraphSchema.get_embedding_dimension(cursor, "Graph_KG.kg_NodeEmbeddings")
    finally:
        cursor.close()
    return width or SESSION_EMBEDDING_DIM
