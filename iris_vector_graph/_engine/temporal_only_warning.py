"""ADR-0015: say why a structural read of a temporal-only predicate came back empty.

A predicate registered with ``set_temporal_structural(p, False)`` has no ``rdf_edges`` row and no ``^KG("out"/"in")`` entry,
so BFS, k-hop and a Cypher ``MATCH`` over it return nothing. That reads as data loss, so an EMPTY structural read that names
a registered predicate logs one warning per (graph, predicate) per object, naming the temporal reads that do answer.

It runs only when the answer is already empty, so a read that returned rows pays nothing. It is an aid: if the registry
cannot be read (a namespace whose classes predate it) the read's own answer stands and the aid stays silent.
"""

from __future__ import annotations

import functools
import json
import logging
import re
from typing import Callable, Dict, Iterable, List

logger = logging.getLogger("iris_vector_graph.temporal_only")

# a relationship type in a pattern: [:A], [r:A|B], [:A*1..3]; also matches a label, which only costs a name comparison
_NAME = re.compile(r"[:|]\s*`?([A-Za-z_][A-Za-z0-9_]*)`?")


def registered(obj) -> Dict[str, List[str]]:
    """{graph name: [predicate, ...]} of every temporal-only registration; {} when it cannot be read."""
    try:
        raw = obj._iris_obj().classMethodValue("Graph.KG.TemporalPolicy", "ListAll")
        data = json.loads(str(raw)) if raw else {}
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # an aid on a read path: the read's own answer stands
        logger.debug("temporal-only registry unreadable: %s", exc)
        return {}


def warn_if_named(obj, names: Iterable[str], what: str) -> None:
    """Log (once per graph and predicate on ``obj``) when ``names`` includes a temporal-only predicate."""
    wanted = {n for n in names if n}
    if not wanted:
        return
    said = obj.__dict__.setdefault("_temporal_only_warned", set())
    for graph, preds in registered(obj).items():
        for pred in preds:
            if pred in wanted and (graph, pred) not in said:
                said.add((graph, pred))
                logger.warning(
                    "%s returned nothing, and predicate %s is registered temporal-only in graph %r (ADR-0015): its temporal "
                    "edges have no structural rows, so BFS, k-hop and MATCH cannot see them. Read them with "
                    "get_edges_in_window / get_temporal_aggregate / get_latest_edges, or make the predicate structural "
                    "again with set_temporal_structural(%r, True) and backfill_temporal_structural_rows(%r).",
                    what, pred, graph, pred, pred,
                )


def _empty(result) -> bool:
    return result is not None and getattr(result, "error", None) in (None, "") and not getattr(result, "rows", None)


def warn_empty_cypher(func: Callable) -> Callable:
    """Decorator for ``execute_cypher(self, cypher_query, ...)``."""

    @functools.wraps(func)
    def wrapper(self, cypher_query, *args, **kwargs):
        result = func(self, cypher_query, *args, **kwargs)
        if _empty(result) and isinstance(cypher_query, str):
            warn_if_named(self, _NAME.findall(cypher_query), "this Cypher query")
        return result

    return wrapper


def warn_empty_bfs(func: Callable) -> Callable:
    """Decorator for ``execute_bfs(self, source_id, predicates, ...)``."""

    @functools.wraps(func)
    def wrapper(self, source_id, predicates, *args, **kwargs):
        result = func(self, source_id, predicates, *args, **kwargs)
        if _empty(result):
            warn_if_named(self, predicates or [], "this traversal")
        return result

    return wrapper
