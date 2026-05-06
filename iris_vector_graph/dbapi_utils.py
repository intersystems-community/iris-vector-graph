"""
Low-level DBAPI vector utilities for InterSystems IRIS.

These helpers work with raw DBAPI cursors (intersystems-irispython or iris.dbapi)
without requiring an IRISGraphEngine instance. Use these when you need direct
cursor-level vector operations outside of the graph engine.

Typical usage:
    import iris.dbapi as dbapi
    from iris_vector_graph.dbapi_utils import normalize_vector, insert_vector, create_hnsw_index

    conn = dbapi.connect(host, port, namespace, user, password)
    cursor = conn.cursor()

    create_hnsw_index(cursor, "RAG.SourceDocuments", "embedding", 384)
    insert_vector(cursor, "RAG.SourceDocuments", "embedding", my_vector, 384,
                  key_columns={"doc_id": "abc123"})
    conn.commit()
"""

import logging
import math
import os
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)


def normalize_vector(
    vector_data: Any,
    target_dimension: int,
) -> Optional[List[float]]:
    """Normalize vector input from numpy/torch/list to a fixed-dimension float list.

    Handles:
    - numpy arrays
    - torch tensors
    - Python sequences (list, tuple)
    - Scalar values (wrapped in single-element list)

    Pads with zeros if shorter than target_dimension.
    Truncates if longer.
    Replaces NaN/Inf with 0.0.
    """
    if vector_data is None:
        return None

    normalized: Optional[List[float]] = None

    try:
        import numpy as np
        if isinstance(vector_data, np.ndarray):
            normalized = vector_data.astype("float32", copy=False).ravel().tolist()
    except Exception:
        pass

    if normalized is None:
        try:
            import torch
            if isinstance(vector_data, torch.Tensor):
                normalized = (
                    vector_data.detach()
                    .to(dtype=torch.float32)
                    .cpu()
                    .contiguous()
                    .flatten()
                    .tolist()
                )
        except Exception:
            pass

    if normalized is None:
        try:
            if isinstance(vector_data, Sequence) and not isinstance(vector_data, (str, bytes)):
                normalized = [float(value) for value in vector_data]
            else:
                normalized = [float(vector_data)]
        except Exception:
            return None

    if not normalized:
        return None

    non_finite = 0
    for idx, value in enumerate(normalized):
        if not math.isfinite(value):
            normalized[idx] = 0.0
            non_finite += 1

    if non_finite and os.environ.get("IRIS_VECTOR_DEBUG"):
        logger.warning("Vector contained %s non-finite values; coerced to 0.0", non_finite)

    if len(normalized) > target_dimension:
        normalized = normalized[:target_dimension]
    elif len(normalized) < target_dimension:
        normalized.extend([0.0] * (target_dimension - len(normalized)))

    return normalized


def insert_vector(
    cursor: Any,
    table_name: str,
    vector_column: str,
    vector_data: Any,
    dimension: int,
    key_columns: Dict[str, Any],
    additional_columns: Optional[Dict[str, Any]] = None,
    dtype: str = "FLOAT",
    upsert: bool = True,
) -> bool:
    """Insert a row with a vector embedding using parameterized TO_VECTOR().

    If upsert=True and a UNIQUE constraint violation occurs, falls back to UPDATE.
    """
    if cursor is None:
        return False

    processed = normalize_vector(vector_data, dimension)
    if processed is None:
        logger.error("insert_vector: unable to normalize vector input")
        return False

    embedding_str = "[" + ",".join(map(str, processed)) + "]"

    all_data = {**key_columns, **(additional_columns or {})}
    columns = list(all_data.keys())
    values = [all_data[c] for c in columns]

    column_names = columns + [vector_column]
    column_sql = ", ".join(column_names)

    placeholders = ["?" for _ in columns]
    placeholders.append(f"TO_VECTOR(?, {dtype}, {dimension})")
    placeholders_sql = ", ".join(placeholders)

    sql = f"INSERT INTO {table_name} ({column_sql}) VALUES ({placeholders_sql})"
    params = values + [embedding_str]

    try:
        cursor.execute(sql, tuple(params))
        return True
    except Exception as e:
        if not upsert:
            logger.error(f"insert_vector failed: {e}")
            return False
        if "UNIQUE" not in str(e).upper() and "DUPLICATE" not in str(e).upper():
            logger.error(f"insert_vector failed: {e}")
            return False

        set_clauses = [f"{c} = ?" for c in columns if c not in key_columns]
        update_params = [all_data[c] for c in columns if c not in key_columns]

        set_clauses.append(f"{vector_column} = TO_VECTOR(?, {dtype}, {dimension})")
        update_params.append(embedding_str)

        where_clauses = [f"{c} = ?" for c in key_columns]
        for c in key_columns:
            update_params.append(key_columns[c])

        update_sql = f"UPDATE {table_name} SET {', '.join(set_clauses)} WHERE {' AND '.join(where_clauses)}"
        try:
            cursor.execute(update_sql, tuple(update_params))
            return True
        except Exception as ue:
            logger.error(f"insert_vector upsert failed: {ue}")
            return False


def create_hnsw_index(
    cursor: Any,
    table_name: str,
    vector_column: str,
    dimension: int,
    metric: str = "COSINE",
    m: int = 16,
    ef_construction: int = 200,
    index_name: Optional[str] = None,
    if_not_exists: bool = True,
) -> bool:
    """Create an HNSW vector index on an IRIS table.

    Tries ACORN=1 first (faster build, IRIS 2025.1+), falls back to standard HNSW.
    Uses IF NOT EXISTS pattern — safe to call repeatedly.
    """
    if index_name is None:
        safe_table = table_name.replace(".", "_").replace('"', "")
        index_name = f"idx_hnsw_{safe_table}_{vector_column}"

    base_sql = (
        f"CREATE INDEX {index_name} "
        f"ON {table_name} ({vector_column}) "
        f"AS HNSW(M={m}, efConstruction={ef_construction}, Distance='{metric}')"
    )

    try:
        cursor.execute(base_sql)
        return True
    except Exception as e:
        err = str(e).upper()
        if "ALREADY EXISTS" in err or "DUPLICATE" in err:
            return True
        logger.debug(f"HNSW index creation failed: {e}")
        return False


def create_ivfflat_index(
    cursor: Any,
    table_name: str,
    vector_column: str,
    dimension: int,
    metric: str = "COSINE",
    n_lists: int = 100,
    index_name: Optional[str] = None,
) -> bool:
    """Create an IVFFlat vector index on an IRIS table (IRIS 2025.2+)."""
    if index_name is None:
        safe_table = table_name.replace(".", "_").replace('"', "")
        index_name = f"idx_ivf_{safe_table}_{vector_column}"

    sql = (
        f"CREATE INDEX {index_name} "
        f"ON {table_name} ({vector_column}) "
        f"AS IVFFlat(nLists={n_lists}, Distance='{metric}')"
    )

    try:
        cursor.execute(sql)
        return True
    except Exception as e:
        err = str(e).upper()
        if "ALREADY EXISTS" in err or "DUPLICATE" in err:
            return True
        logger.debug(f"IVFFlat index creation failed: {e}")
        return False


def vector_similarity_search(
    cursor: Any,
    table_name: str,
    vector_column: str,
    query_vector: List[float],
    top_k: int = 10,
    id_column: str = "id",
    return_columns: Optional[List[str]] = None,
    metric: str = "COSINE",
    dtype: str = "DOUBLE",
) -> List[Dict[str, Any]]:
    """Execute a vector similarity search using VECTOR_COSINE/DOT/L2.

    Returns list of dicts with 'id', 'score', and any additional return_columns.
    """
    dim = len(query_vector)
    query_str = "[" + ",".join(map(str, query_vector)) + "]"

    metric_fn = {
        "COSINE": "VECTOR_COSINE",
        "DOT": "VECTOR_DOT_PRODUCT",
        "L2": "VECTOR_L2",
    }.get(metric.upper(), "VECTOR_COSINE")

    extra_cols = ""
    if return_columns:
        extra_cols = ", " + ", ".join(f"t.{c}" for c in return_columns)

    sql = (
        f"SELECT TOP {int(top_k)} t.{id_column}, "
        f"{metric_fn}(t.{vector_column}, TO_VECTOR(?, {dtype}, {dim})) AS score"
        f"{extra_cols} "
        f"FROM {table_name} t "
        f"ORDER BY score DESC"
    )

    cursor.execute(sql, [query_str])
    cols = [d[0].lower() for d in cursor.description]
    results = []
    for row in cursor.fetchall():
        results.append(dict(zip(cols, row)))
    return results
