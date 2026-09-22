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


def _sql_literal(value: str) -> str:
    """A string safe to embed in a quoted SQL literal.

    This module builds a WHERE body rather than a parameterised statement, so a graph
    ID arriving from a caller is interpolated. Doubling the quote is what keeps it a
    value: a graph named ``o'brien`` must not be able to close the literal.
    """
    return str(value).replace("'", "''")


def _glob_to_sql_like(pattern: str) -> str:
    return pattern.replace("%", r"\%").replace("_", r"\_").replace("*", "%").replace("?", "_")


def build_node_where(
    sel: EmbedSelector,
    schema_prefix: str = "Graph_KG",
    embeddings_table: str = "Graph_KG.kg_NodeEmbeddings",
    graph_id: Optional[str] = None,
) -> str:
    """The WHERE body selecting the nodes to embed.

    `graph_id` scopes both the node selection and the `missing_only` subquery. It is
    optional so that a caller with no graph keeps 3.2.0's namespace-wide reach, but a
    caller that names a graph gets only that graph: a routed table holds one graph's
    rows, while the legacy `kg_NodeEmbeddings` holds every graph that has not been
    routed away, so the subquery needs the predicate too (spec 227).
    """
    parts: List[str] = []

    if graph_id is not None:
        parts.append(f"COALESCE(graph_id, '') = COALESCE('{_sql_literal(graph_id)}', '')")

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
        # `SELECT id` here read the embedding table's RowID, so every node compared
        # unequal to every value in the list and `missing_only` selected the whole
        # graph. It cost a re-embed rather than a wrong answer, which is why it went
        # unnoticed through the 227 re-key (T071).
        scope = (
            ""
            if graph_id is None
            else f" WHERE COALESCE(graph_id, '') = COALESCE('{_sql_literal(graph_id)}', '')"
        )
        parts.append(f"node_id NOT IN (SELECT node_id FROM {embeddings_table}{scope})")

    return " AND ".join(parts) if parts else ""


def build_edge_where(
    sel: EmbedSelector,
    schema_prefix: str = "Graph_KG",
    edge_table: str = "Graph_KG.rdf_edges",
    embeddings_table: str = "Graph_KG.kg_EdgeEmbeddings",
    graph_id: Optional[str] = None,
) -> str:
    """The WHERE body selecting the edges to embed.

    `graph_id` scopes the selection to one graph (spec 230, FR-006). Optional, so a
    caller naming no graph keeps 3.2.0's namespace-wide reach; a caller that names
    one gets only that graph's edges, because writing a vector for every triple
    found would invent graph-A membership for graph B's edges.

    `missing_only` is not scoped here and does nothing: `embed_edges` answers "which
    of these are already done" from the pair's routed edge table, which is the only
    place that can be true of a graph *and* a model. A predicate here could only
    name one table, and which table that is depends on the route.
    """
    parts: List[str] = []

    if graph_id is not None:
        parts.append(f"COALESCE(graph_id, '') = COALESCE('{_sql_literal(graph_id)}', '')")

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
