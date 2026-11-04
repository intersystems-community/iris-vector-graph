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

import re
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


class ParallelEdgesPresentError(IVGError):
    """A graph cannot leave multigraph mode while a triple still has two edges.

    ``count`` is the number of ``(s, p, o)`` triples carrying more than one edge;
    delete the extra edges (or erase the graph) and turn the mode off again.
    """

    code = "parallel_edges_present"

    def __init__(self, graph: Optional[str], count: int):
        self.graph = graph
        self.count = count
        super().__init__(
            f"parallel_edges_present: graph {graph or '(default)'!s} has {count} "
            "triple(s) with more than one edge; delete the extra edges before "
            "turning multigraph mode off."
        )


class GraphWriteError(IVGError, RuntimeError):
    """A data-changing write failed in IRIS; everything it had written is rolled back.

    Raised by ``create_edge``, ``store_node``, ``store_edge`` and the store's
    ``write_nodes`` / ``write_edges`` where they used to log the error and return
    ``False`` (or count the row as written). ``create_edge``'s endpoint node rows had
    already committed by then, so a failed edge left orphan nodes. A duplicate is not
    an error and still answers as before. Since 4.6.0 (R203) also by ``create_node``,
    ``delete_edge``, ``set_edge_weight``, ``reify_edge``, ``delete_reification``,
    ``create_edge_temporal``, ``bulk_create_edges_temporal`` and ``enqueue_for_embedding``,
    which answered a refusal (SQLCODE -99, ``<PROTECT>``) with ``False``, ``None`` or ``0``:
    the answers they give for "already there", "no such row" and "nothing to do".
    Where a failure could not take back what it wrote, the message says what remains
    in place of "nothing written".

    Like ``BulkLoadError`` it is raised after the rollback and carries the cause; it is
    an ``IVGError`` so one ``except IVGError`` catches it, and a ``RuntimeError`` as
    ``BulkLoadError`` is. ``sqlcode`` is the IRIS SQLCODE parsed from the driver's
    message (``None`` when it carries none); the driver's exception is ``__cause__``.
    """

    _SQLCODE = re.compile(r"SQLCODE:?\s*<?\s*(-?\d+)")

    def __init__(self, operation: str, cause: BaseException, left: str = ""):
        self.operation = operation
        self.message = str(cause)
        found = self._SQLCODE.search(self.message)
        self.sqlcode: Optional[int] = int(found.group(1)) if found else None
        code = f" (SQLCODE {self.sqlcode})" if self.sqlcode is not None else ""
        # `left` says what a failure could not take back; without it, nothing was written
        super().__init__(f"{operation} failed{code}, {left or 'nothing written'}: {self.message}")


__all__ = [
    "IVGError",
    "GraphWriteError",
    "PrerequisiteError",
    "IndexNotFoundError",
    "IndexNotBuiltError",
    "EmbeddingsMissingError",
    "IndexNotSyncedError",
    "NodeNotFoundError",
    "ParallelEdgesPresentError",
]
