"""Engine-side exception hierarchy for iris-vector-graph.

Design principle (SDK ergonomics rubric, Principle 2): *raise* when an operation
is structurally impossible to satisfy; *return empty* only when a valid query
legitimately found nothing. Every exception below carries an actionable message
that points the caller at the recovery method.

All engine-side errors descend from ``IVGError`` (defined in ``sdk.py``), so the
whole package — client transport errors and engine prerequisite errors alike —
shares a single catchable root::

    from iris_vector_graph import IVGError
    try:
        idx.search(q)
    except IVGError as e:
        ...
"""
from __future__ import annotations

from typing import Optional

from .sdk import IVGError


class PrerequisiteError(IVGError):
    """Raised when a required setup step was skipped. Carries a ``remedy``."""

    def __init__(self, message: str, remedy: Optional[str] = None):
        self.remedy = remedy
        if remedy:
            message = f"{message} {remedy}"
        super().__init__(message)


class IndexNotFoundError(PrerequisiteError):
    """A named index does not exist yet."""

    def __init__(self, name: str, known: Optional[list] = None):
        self.name = name
        known_str = ""
        if known is not None:
            known_str = f" Known indexes: {sorted(known)}." if known else " No indexes exist yet."
        super().__init__(
            f"Index {name!r} not found.{known_str}",
            remedy="Create it with engine.create_index(name, type=...).",
        )


class IndexNotBuiltError(PrerequisiteError):
    """An index exists but contains no entries (build never ran or found nothing)."""

    def __init__(self, name: str, rows: int = 0):
        self.name = name
        self.rows = rows
        super().__init__(
            f"Index {name!r} is empty ({rows} rows indexed).",
            remedy="Call engine.index(name).build() after embedding/loading data.",
        )


class EmbeddingsMissingError(PrerequisiteError):
    """A vector operation needs embeddings that have not been generated."""

    def __init__(self, scope: str = "nodes", detail: str = ""):
        self.scope = scope
        msg = f"No embeddings found for {scope}."
        if detail:
            msg = f"No embeddings found for {scope}: {detail}."
        remedy = (
            "Call engine.embed_nodes(...)." if scope == "nodes"
            else "Call engine.embed_edges(...)."
        )
        super().__init__(msg, remedy=remedy)


class IndexNotSyncedError(PrerequisiteError):
    """Bulk writes happened but the adjacency/acceleration index is stale."""

    def __init__(self, pending: Optional[int] = None):
        self.pending = pending
        detail = f" ({pending} pending changes)" if pending is not None else ""
        super().__init__(
            f"Graph index is out of sync with recent bulk writes{detail}.",
            remedy="Call engine.sync() before querying.",
        )


class NodeNotFoundError(IVGError):
    """A referenced node id does not exist in the graph."""

    def __init__(self, node_id: str):
        self.node_id = node_id
        super().__init__(f"Node {node_id!r} not found in the graph.")


__all__ = [
    "IVGError",
    "PrerequisiteError",
    "IndexNotFoundError",
    "IndexNotBuiltError",
    "EmbeddingsMissingError",
    "IndexNotSyncedError",
    "NodeNotFoundError",
]
