"""4.6.0 R160: `status().indexes.hnsw_built` says whether an HNSW index exists on a table holding node vectors.

It was `COUNT(*) FROM kg_NodeEmbeddings_optimized > 0`, a row count: an HNSW index built on `kg_NodeEmbeddings` (or on
a routed table) read "not built", and rows with no index read "built".

Worked by hand: the answer before is whether the class dictionary lists an HNSW index on any IVG table (read here
directly, `%Dictionary.CompiledIndex` with an HNSW TypeClass, not through IVG); in a namespace with none it is False.
After `CREATE INDEX ... AS HNSW` on the default node table it is True; after the `DROP INDEX` it is the answer before.
"""

from __future__ import annotations

import contextlib
import uuid

import pytest

pytestmark = [pytest.mark.e2e]


def _hnsw_in_dictionary(conn) -> list:
    """Every HNSW index the dictionary lists on an IVG embedding table (`kg_NodeEmbeddings*`, `kg_emb_*`)."""
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT i.parent, i.SqlName FROM %Dictionary.CompiledIndex i "
            "JOIN %Dictionary.CompiledClass c ON c.Name = i.parent "
            "WHERE i.TypeClass LIKE '%HNSW%' AND c.SqlSchemaName = 'Graph_KG' "
            "AND (c.SqlTableName %STARTSWITH 'kg_NodeEmbeddings' OR c.SqlTableName %STARTSWITH 'kg_emb_')"
        )
        return [tuple(r) for r in cursor.fetchall()]
    finally:
        cursor.close()


def _run(conn, sql):
    cursor = conn.cursor()
    try:
        cursor.execute(sql)
    finally:
        cursor.close()
    with contextlib.suppress(Exception):
        conn.commit()


def test_hnsw_built_follows_the_index(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    before = _hnsw_in_dictionary(iris_connection)
    status = engine.status()
    assert status.indexes.hnsw_built is bool(before), (before, status.errors)
    assert not [e for e in status.errors if "HNSW" in e], status.errors

    name = f"idx_r160_{uuid.uuid4().hex[:8]}"
    _run(iris_connection, f"CREATE INDEX {name} ON Graph_KG.kg_NodeEmbeddings (emb) AS HNSW(Distance='Cosine')")
    try:
        assert any(sql_name == name for _, sql_name in _hnsw_in_dictionary(iris_connection))
        assert engine.status().indexes.hnsw_built is True
    finally:
        _run(iris_connection, f"DROP INDEX {name} ON Graph_KG.kg_NodeEmbeddings")
    assert engine.status().indexes.hnsw_built is bool(before)
