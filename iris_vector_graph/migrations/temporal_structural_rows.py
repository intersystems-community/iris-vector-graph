"""Backfill structural rows for temporal-only edges (spec 246, ADR-0009).

A temporal edge written before ``temporal_structural_rows=True`` exists in the temporal
globals and the ``^KG`` shadow, but has no ``rdf_edges`` row, so SQL and Cypher cannot see
it. This pass gives every such triple one row, marked ``{"temporal": true}``, through
``create_edge``, which is the same writer the flag path uses.

* The triples come from the temporal read (``get_edges_in_window``) per graph. A triple
  that already has a row, marked or not, is left alone.
* Dry run computes the plan and makes no write. ``rows_before``/``rows_after`` is the
  ``rdf_edges`` row count, so a caller can check the run was read-only.
* Re-running is safe: once a row exists the triple is no longer in the plan.
* ``graphs`` names the graphs to scan. The default graph is ``None``. Named graphs are
  listed by the caller; the pass does not guess them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

from iris_vector_graph._engine.temporal import _ALL_TIME_END, TEMPORAL_QUALIFIERS

Triple = Tuple[Optional[str], str, str, str]  # (graph, source, predicate, target)
EdgeKey = Tuple[str, str, str]  # (source, predicate, target)


@dataclass
class BackfillReport:
    """What a backfill pass planned, wrote, and left in the ``rdf_edges`` table."""

    dry_run: bool
    planned: int = 0
    inserted: int = 0
    rows_before: int = 0
    rows_after: int = 0
    triples: List[Triple] = field(default_factory=list)


def _row_count(cursor, engine) -> int:
    cursor.execute(f"SELECT COUNT(*) FROM {engine._t('rdf_edges')}")
    row = cursor.fetchone()
    return int(row[0]) if row else 0


def _structural_triples(cursor, engine, graph) -> Set[EdgeKey]:
    cursor.execute(
        f"SELECT s, p, o_id FROM {engine._t('rdf_edges')} "
        "WHERE COALESCE(graph_id, '') = COALESCE(?, '')",
        [graph or ""],
    )
    return {(s, p, o) for s, p, o in cursor.fetchall()}


def _temporal_triples(engine, graph) -> Dict[EdgeKey, float]:
    """Every temporal triple in ``graph``, mapped to the weight of one of its edges."""
    weights: Dict[EdgeKey, float] = {}
    for edge in engine.get_edges_in_window(
        source="", predicate="", start=0, end=_ALL_TIME_END, graph=graph,
    ):
        key = (edge["s"], edge["p"], edge["o"])
        weights.setdefault(key, float(edge.get("w", 1.0)))
    return weights


def backfill_structural_rows(
    conn,
    *,
    engine=None,
    graphs: Iterable[Optional[str]] = (None,),
    dry_run: bool = False,
) -> BackfillReport:
    """Give every temporal-only triple in ``graphs`` a marked ``rdf_edges`` row.

    Args:
        conn: the DB-API connection the engine uses.
        engine: an ``IRISGraphEngine`` on ``conn``. Built here when omitted.
        graphs: graphs to scan; ``None`` is the default graph.
        dry_run: compute the plan only. Makes no write.

    Returns:
        A :class:`BackfillReport`. In a dry run ``inserted`` is 0 and ``planned`` is
        what a real run would insert.
    """
    if engine is None:
        from iris_vector_graph.engine import IRISGraphEngine

        engine = IRISGraphEngine(conn, vector_dtype="DOUBLE")

    cursor = conn.cursor()
    report = BackfillReport(dry_run=dry_run)
    report.rows_before = _row_count(cursor, engine)

    for graph in graphs:
        graph = graph or None
        temporal = _temporal_triples(engine, graph)
        structural = _structural_triples(cursor, engine, graph)
        missing = sorted(key for key in temporal if key not in structural)
        report.planned += len(missing)
        report.triples.extend((graph, s, p, o) for s, p, o in missing)
        if dry_run:
            continue
        for s, p, o in missing:
            # create_edge is the one structural writer; it commits per call.
            created = engine.create_edge(
                s, p, o, weight=temporal[(s, p, o)],
                qualifiers=dict(TEMPORAL_QUALIFIERS), graph=graph,
            )
            if created:
                report.inserted += 1

    report.rows_after = _row_count(cursor, engine)
    return report
