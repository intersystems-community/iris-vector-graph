from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

IndexState = Literal["ready", "empty", "building", "absent"]


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
class SyncReport:
    """Result of engine.verify_sync() — quantitative ^KG/^NKG drift check.

    Compares the authoritative SQL edge count (Graph_KG.rdf_edges) against the
    adjacency-global edge count (^NKG "$meta" edgeCount). Divergence means a
    write path bypassed global maintenance (drop_graph, delete_node, raw SQL,
    the SQL table bridge, or an interrupted bulk load) and the acceleration
    indexes are stale. ``pending_sync`` reflects the in-process _nkg_dirty flag.
    """

    in_sync: bool = True
    sql_edges: int = 0
    global_edges: int = 0
    global_nodes: int = 0
    pending_sync: bool = False
    healed: bool = False
    detail: Optional[str] = None

    def __bool__(self) -> bool:
        return self.in_sync

    def to_dict(self) -> Dict[str, Any]:
        return {
            "in_sync": self.in_sync,
            "sql_edges": self.sql_edges,
            "global_edges": self.global_edges,
            "global_nodes": self.global_nodes,
            "pending_sync": self.pending_sync,
            "healed": self.healed,
            "detail": self.detail,
        }


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
    pending_sync: bool = False
    internals: Optional[Dict[str, Any]] = None

    @property
    def vector_index_state(self) -> IndexState:
        if self.tables.node_embeddings == 0:
            return "absent"
        if self.indexes.hnsw_built:
            return "ready"
        return "empty"

    @property
    def fulltext_index_state(self) -> IndexState:
        if self.indexes.bm25_indexes:
            return "ready"
        return "absent"

    @property
    def acceleration_state(self) -> IndexState:
        if self.adjacency.nkg_populated:
            return "ready"
        if self.adjacency.kg_populated:
            return "empty"
        return "absent"

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

    def report(self, internals: bool = False) -> str:
        def tick(ok: bool) -> str:
            return "✓" if ok else "✗"

        sync_label = "needs sync" if self.pending_sync else "in sync"
        vec_label = self.vector_index_state
        ft_label = self.fulltext_index_state
        accel_label = self.acceleration_state

        vec_detail = ""
        if self.tables.node_embeddings > 0:
            vec_detail = f" ({self.tables.node_embeddings:,} indexed)"

        ivf_names = ", ".join(self.indexes.ivf_indexes) if self.indexes.ivf_indexes else "none"
        bm25_names = ", ".join(self.indexes.bm25_indexes) if self.indexes.bm25_indexes else "none"
        plaid_names = ", ".join(self.indexes.plaid_indexes) if self.indexes.plaid_indexes else "none"

        lines = [
            "IVG Engine Status",
            "═" * 50,
            f"\nGraph:           {self.tables.nodes:,} nodes · {self.tables.edges:,} edges"
            f"  (probe: {self.probe_ms:.0f}ms)",
            f"Vector index:    {vec_label}{vec_detail}",
            f"Full-text index: {ft_label}",
            f"Acceleration:    {accel_label}",
            f"Sync state:      {sync_label}",
        ]

        if self.indexes.ivf_indexes or self.indexes.plaid_indexes:
            lines.append(f"\nAdditional indexes")
            lines.append(f"  IVF:   {ivf_names}")
            lines.append(f"  PLAID: {plaid_names}")
            lines.append(f"  BM25:  {bm25_names}")

        if not self.adjacency.kg_predicates_consistent and self.adjacency.kg_populated:
            lines.append(
                "\n⚠  Index mismatch — graph data has changed. Call engine.sync()."
            )

        if self.pending_sync:
            lines.append("\n⚠  Pending bulk writes — call engine.sync() before querying.")

        if internals:
            kg_count = (
                f"≥{self.adjacency.kg_edge_count:,}"
                if self.adjacency.kg_edge_count_capped
                else f"{self.adjacency.kg_edge_count:,}"
            )
            lines += [
                f"\nGlobals (internals)",
                f"  {tick(self.adjacency.kg_populated)} ^KG   ({kg_count} source nodes indexed)",
                f"  {tick(self.adjacency.nkg_populated)} ^NKG  (integer acceleration index)",
            ]
            bfs_sym = "✓" if self.adjacency.bfs_path in ("arno", "objectscript") else "✗"
            lines.append(f"  {bfs_sym} BFS path: {self.adjacency.bfs_path}")

            lines.append(f"\nObjectScript classes")
            lines.append(f"  {tick(self.objectscript.deployed)} Deployed")
            for cls in sorted(self.objectscript.classes):
                lines.append(f"    · {cls}")

            lines.append(f"\nArno accelerator")
            if self.arno.loaded:
                algos = [k for k, v in self.arno.capabilities.items()
                         if v and k not in ("nkg_data",)]
                lines.append(f"  ✓ Loaded — algorithms: {', '.join(algos) or 'none'}")
            else:
                lines.append("  ✗ Not loaded  (BFS/PPR using ObjectScript fallback)")

            lines += [
                f"\nSQL tables",
                f"  nodes              {self.tables.nodes:>10,}",
                f"  edges              {self.tables.edges:>10,}",
                f"  labels             {self.tables.labels:>10,}",
                f"  properties         {self.tables.props:>10,}",
                f"  node embeddings    {self.tables.node_embeddings:>10,}",
                f"  edge embeddings    {self.tables.edge_embeddings:>10,}",
                f"  embedding dim      {str(self.embedding_dimension or 'auto'):>10}",
            ]

            if self.internals:
                lines.append(f"\nExtra")
                for k, v in self.internals.items():
                    lines.append(f"  {k}: {v}")

        if self.errors:
            lines.append(f"\nProbe errors ({len(self.errors)})")
            for e in self.errors:
                lines.append(f"  ! {e}")

        lines.append("\n" + "═" * 50)
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.report(internals=False)
