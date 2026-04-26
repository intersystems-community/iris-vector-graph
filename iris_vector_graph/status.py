from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class TableCounts:
    nodes: int = 0
    edges: int = 0
    labels: int = 0
    props: int = 0
    node_embeddings: int = 0
    edge_embeddings: int = 0


@dataclass
class AdjacencyStatus:
    kg_populated: bool = False
    kg_edge_count: int = 0
    kg_edge_count_capped: bool = False
    nkg_populated: bool = False
    kg_predicates_consistent: bool = True
    bfs_path: str = "none"


@dataclass
class ObjectScriptStatus:
    deployed: bool = False
    classes: List[str] = field(default_factory=list)


@dataclass
class ArnoStatus:
    loaded: bool = False
    capabilities: Dict[str, Any] = field(default_factory=dict)


@dataclass
class IndexInventory:
    hnsw_built: bool = False
    ivf_indexes: List[str] = field(default_factory=list)
    bm25_indexes: List[str] = field(default_factory=list)
    plaid_indexes: List[str] = field(default_factory=list)


@dataclass
class EngineStatus:
    tables: TableCounts = field(default_factory=TableCounts)
    adjacency: AdjacencyStatus = field(default_factory=AdjacencyStatus)
    objectscript: ObjectScriptStatus = field(default_factory=ObjectScriptStatus)
    arno: ArnoStatus = field(default_factory=ArnoStatus)
    indexes: IndexInventory = field(default_factory=IndexInventory)
    embedding_dimension: int = 768
    probe_ms: float = 0.0
    errors: List[str] = field(default_factory=list)

    @property
    def ready_for_bfs(self) -> bool:
        return self.adjacency.kg_populated and self.tables.edges > 0

    @property
    def ready_for_multihop_bfs(self) -> bool:
        if not self.ready_for_bfs:
            return False
        if not self.adjacency.kg_predicates_consistent:
            return False
        return self.adjacency.bfs_path in ("arno", "objectscript")

    @property
    def ready_for_vector_search(self) -> bool:
        return self.tables.node_embeddings > 0

    @property
    def ready_for_edge_search(self) -> bool:
        return self.tables.edge_embeddings > 0

    @property
    def ready_for_full_text(self) -> bool:
        return bool(self.indexes.bm25_indexes)

    def report(self) -> str:
        def tick(ok: bool) -> str:
            return "✓" if ok else "✗"

        kg_count = (
            f"≥{self.adjacency.kg_edge_count:,}"
            if self.adjacency.kg_edge_count_capped
            else f"{self.adjacency.kg_edge_count:,}"
        )

        lines = [
            "IVG Engine Status",
            "═" * 50,
            f"\nSQL Tables  (probe: {self.probe_ms:.0f}ms)",
            f"  nodes              {self.tables.nodes:>10,}",
            f"  edges              {self.tables.edges:>10,}",
            f"  labels             {self.tables.labels:>10,}",
            f"  properties         {self.tables.props:>10,}",
            f"  node embeddings    {self.tables.node_embeddings:>10,}",
            f"  edge embeddings    {self.tables.edge_embeddings:>10,}",
            f"  embedding dim      {self.embedding_dimension:>10}",
            f"\nAdjacency Globals",
            f"  {tick(self.adjacency.kg_populated)} ^KG   ({kg_count} source nodes indexed)",
            f"  {tick(self.adjacency.nkg_populated)} ^NKG  (Arno integer index)",
        ]

        bfs_sym = "✓" if self.adjacency.bfs_path in ("arno", "objectscript") else "✗"
        lines.append(f"  {bfs_sym} BFS path: {self.adjacency.bfs_path}")

        if not self.adjacency.kg_predicates_consistent and self.adjacency.kg_populated:
            lines.append(
                "  ⚠ ^KG predicate mismatch — stale from different data snapshot. "
                "Run BuildKG() after reloading graph data."
            )

        if not self.adjacency.kg_populated and self.tables.edges > 0:
            lines.append(
                "  ⚠ ^KG empty but edges exist — "
                "call BuildKG() or rebuild on container startup"
            )

        lines += [
            f"\nObjectScript Classes",
            f"  {tick(self.objectscript.deployed)} Deployed",
        ]
        for cls in sorted(self.objectscript.classes):
            lines.append(f"    · {cls}")

        lines.append(f"\nArno Accelerator")
        if self.arno.loaded:
            algos = [k for k, v in self.arno.capabilities.items()
                     if v and k not in ("nkg_data",)]
            lines.append(f"  ✓ Loaded — algorithms: {', '.join(algos) or 'none'}")
        else:
            lines.append("  ✗ Not loaded  (BFS/PPR using ObjectScript fallback)")

        lines += [
            f"\nIndexes",
            f"  {tick(self.indexes.hnsw_built)} HNSW node vector index",
            f"  {'✓' if self.indexes.ivf_indexes else '·'} IVF:   "
            f"{', '.join(self.indexes.ivf_indexes) or 'none'}",
            f"  {'✓' if self.indexes.bm25_indexes else '·'} BM25:  "
            f"{', '.join(self.indexes.bm25_indexes) or 'none'}",
            f"  {'✓' if self.indexes.plaid_indexes else '·'} PLAID: "
            f"{', '.join(self.indexes.plaid_indexes) or 'none'}",
        ]

        if self.errors:
            lines.append(f"\nProbe errors ({len(self.errors)})")
            for e in self.errors:
                lines.append(f"  ! {e}")

        lines.append("\n" + "═" * 50)
        return "\n".join(lines)
