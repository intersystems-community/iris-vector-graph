"""Structured selection model for embed_nodes / embed_edges (spec 179).

Replaces raw ``where=`` SQL fragments with typed, injection-safe parameters.
"""
from __future__ import annotations

import fnmatch
from typing import List, Optional, Sequence

try:
    from pydantic import BaseModel, field_validator
    _PYDANTIC = True
except ImportError:
    _PYDANTIC = False


if _PYDANTIC:
    class EmbedSelector(BaseModel):
        label: Optional[str] = None
        node_ids: Optional[List[str]] = None
        predicate: Optional[str] = None
        source_label: Optional[str] = None
        target_label: Optional[str] = None
        exclude_pattern: Optional[str] = None
        missing_only: bool = False

        @field_validator("exclude_pattern")
        @classmethod
        def _validate_glob(cls, v):
            if v is not None and any(c in v for c in (";", "--", "/*", "EXEC")):
                raise ValueError(f"Unsafe exclude_pattern rejected: {v!r}")
            return v

else:
    class EmbedSelector:  # type: ignore[no-redef]
        def __init__(
            self,
            label: Optional[str] = None,
            node_ids: Optional[List[str]] = None,
            predicate: Optional[str] = None,
            source_label: Optional[str] = None,
            target_label: Optional[str] = None,
            exclude_pattern: Optional[str] = None,
            missing_only: bool = False,
        ):
            if exclude_pattern is not None and any(
                c in exclude_pattern for c in (";", "--", "/*", "EXEC")
            ):
                raise ValueError(f"Unsafe exclude_pattern rejected: {exclude_pattern!r}")
            self.label = label
            self.node_ids = node_ids
            self.predicate = predicate
            self.source_label = source_label
            self.target_label = target_label
            self.exclude_pattern = exclude_pattern
            self.missing_only = missing_only


def _glob_to_sql_like(pattern: str) -> str:
    return pattern.replace("%", r"\%").replace("_", r"\_").replace("*", "%").replace("?", "_")


def build_node_where(
    sel: EmbedSelector,
    schema_prefix: str = "Graph_KG",
    embeddings_table: str = "Graph_KG.kg_NodeEmbeddings",
) -> str:
    parts: List[str] = []

    if sel.label is not None:
        parts.append(
            f"node_id IN (SELECT s FROM {schema_prefix}.rdf_labels WHERE label = '{sel.label}')"
        )

    if sel.node_ids is not None:
        if not sel.node_ids:
            parts.append("1=0")
        else:
            ids_csv = ", ".join(f"'{nid}'" for nid in sel.node_ids)
            parts.append(f"node_id IN ({ids_csv})")

    if sel.exclude_pattern is not None:
        like = _glob_to_sql_like(sel.exclude_pattern)
        parts.append(f"node_id NOT LIKE '{like}'")

    if sel.missing_only:
        parts.append(f"node_id NOT IN (SELECT id FROM {embeddings_table})")

    return " AND ".join(parts) if parts else ""


def build_edge_where(
    sel: EmbedSelector,
    schema_prefix: str = "Graph_KG",
    edge_table: str = "Graph_KG.rdf_edges",
    embeddings_table: str = "Graph_KG.kg_EdgeEmbeddings",
) -> str:
    parts: List[str] = []

    if sel.predicate is not None:
        parts.append(f"p = '{sel.predicate}'")

    if sel.source_label is not None:
        parts.append(
            f"s IN (SELECT s FROM {schema_prefix}.rdf_labels WHERE label = '{sel.source_label}')"
        )

    if sel.target_label is not None:
        parts.append(
            f"o_id IN (SELECT s FROM {schema_prefix}.rdf_labels WHERE label = '{sel.target_label}')"
        )

    if sel.exclude_pattern is not None:
        like = _glob_to_sql_like(sel.exclude_pattern)
        parts.append(f"s NOT LIKE '{like}' AND o_id NOT LIKE '{like}'")

    if sel.missing_only:
        pass

    return " AND ".join(parts) if parts else ""


__all__ = ["EmbedSelector", "build_node_where", "build_edge_where"]
