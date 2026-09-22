"""Schema migrations that move data, not just DDL.

A migration lives here rather than in ``schema.py`` when it is a multi-step data
movement that must be re-runnable and reportable. ``schema.py`` owns the DDL that
describes the end state; this package owns getting an existing installation to
that state without losing a row.

The 4.0.0 migration (spec 227) moves namespace-wide embeddings into graph-scoped
routed tables. It never deletes a vector and never guesses a graph: a row whose
graph cannot be established goes to ``Graph_KG.embedding_quarantine`` and leaves
only by explicit operator placement.

Spec 230 adds the two steps that finish the same job elsewhere in the schema: the
BM25 corpus and the edge vectors gain a graph (``docs_and_edge_vectors``), and the
four flat ``^KG`` node stores are dropped and rebuilt keyed by graph
(``kg_node_stores``). All three are re-runnable and report what they moved.

:func:`upgrade_to_4_0_0` sequences all four steps in the one order that works and
reports each separately, declining a step whose table is already in the 4.0.0 shape
rather than migrating migrated data (``upgrade``).
"""

from .docs_and_edge_vectors import (
    DOCS_QUARANTINE_REASONS,
    EDGE_VECTOR_QUARANTINE_REASONS,
    AmbiguousDocument,
    AmbiguousEdgeVector,
    PlacementReport,
    migrate_docs_and_edge_vectors,
    place_documents,
    place_edge_vectors,
)
from .graph_scoped_embeddings import (
    AmbiguousVector,
    MigrationReport,
    migrate_to_graph_scoped_embeddings,
)
from .kg_node_stores import (
    REKEYED_STORES,
    KgRekeyReport,
    rekey_kg_node_stores,
)
from .upgrade import (
    UPGRADE_STEPS,
    StepResult,
    UpgradeReport,
    upgrade_to_4_0_0,
)

__all__ = [
    "StepResult",
    "UPGRADE_STEPS",
    "UpgradeReport",
    "AmbiguousDocument",
    "AmbiguousEdgeVector",
    "AmbiguousVector",
    "DOCS_QUARANTINE_REASONS",
    "EDGE_VECTOR_QUARANTINE_REASONS",
    "KgRekeyReport",
    "MigrationReport",
    "PlacementReport",
    "REKEYED_STORES",
    "migrate_docs_and_edge_vectors",
    "migrate_to_graph_scoped_embeddings",
    "place_documents",
    "place_edge_vectors",
    "rekey_kg_node_stores",
    "upgrade_to_4_0_0",
]
