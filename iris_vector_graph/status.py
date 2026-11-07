from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

from .constants import DEFAULT_EMBEDDING_DIMENSION

IndexState = Literal["ready", "empty", "building", "absent"]

#: What ``EngineStatus.unknown`` may name: each part of the report a failed (or skipped) probe leaves untold
STATUS_PARTS = ("graph", "acceleration", "sync", "vector_index", "fulltext_index", "ivf_index", "plaid_index", "ledger")


@dataclass
class TableCounts:
    """Rows of the IVG tables, every graph's together (``node_count()`` and ``edge_count()`` count one graph's)."""

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
    # Graph.KG.ArnoAccel.LibraryVersion() of the loaded build ("arno-callout X.Y.Z+<sha>"); "" when unknown
    version: str = ""
    # The library file: the one this engine's server process loaded, else the path its loaders resolve and would
    # try (IVG_ARNO_LIB, else ^ArnoAccel("lib_path"), else /usr/irissys/mgr/libarno_callout.so; R132); "" when
    # IVG's classes are not deployed, or when IVG_DISABLE_ARNO=1 (nothing was tried)
    lib_path: str = ""
    # Whether lib_path is on the server's file system (Graph.KG.ArnoAccel.LibExists); None when not asked (4.6.0 R225)
    lib_found: Optional[bool] = None
    # IVG_DISABLE_ARNO=1 in this process's environment: the engine does not load the library (4.6.0 R207, P3)
    disabled: bool = False


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
class LedgerStatus:
    """Spec 213 (FR-050): revision-ledger counters mirrored into the engine status report."""

    state: str = "never-enabled"
    strict: bool = False
    head_seq: int = 0
    head_id: Optional[str] = None
    commits_ok: int = 0
    replays_idem: int = 0
    rejections: Dict[str, int] = field(default_factory=dict)
    last_verify_ms: Optional[int] = None
    last_verify_result: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "strict": self.strict,
            "head_seq": self.head_seq,
            "head_id": self.head_id,
            "commits_ok": self.commits_ok,
            "replays_idem": self.replays_idem,
            "rejections": dict(self.rejections),
            "last_verify_ms": self.last_verify_ms,
            "last_verify_result": self.last_verify_result,
        }


@dataclass
class EngineStatus:
    tables: TableCounts = field(default_factory=TableCounts)
    adjacency: AdjacencyStatus = field(default_factory=AdjacencyStatus)
    objectscript: ObjectScriptStatus = field(default_factory=ObjectScriptStatus)
    arno: ArnoStatus = field(default_factory=ArnoStatus)
    indexes: IndexInventory = field(default_factory=IndexInventory)
    embedding_dimension: int = DEFAULT_EMBEDDING_DIMENSION
    probe_ms: float = 0.0
    errors: List[str] = field(default_factory=list)
    pending_sync: bool = False
    internals: Optional[Dict[str, Any]] = None
    ledger: Optional[LedgerStatus] = None
    # Spec 262: the namespace's IVG Schema (its record; None when it has none, with the reason in `errors`), and how
    # many objects IVG 4.5.x created in SQLUser and User are still there (engine.remove_legacy_objects() lists them).
    schema: Optional[str] = None
    legacy_objects: int = 0
    # 4.6.0 R200: the functions and procedures setup declares that the IVG Schema does not hold (setup could not
    # create them; initialize_schema() logs why, and a re-run tries again), as the DDL names them
    missing_routines: List[str] = field(default_factory=list)
    # 4.6.0 R050: what the report cannot tell because its probe failed or did not run (STATUS_PARTS); the report says
    # "unknown" for each rather than 0 nodes, ObjectScript, in sync, absent or not enabled (round 3, expert W8)
    unknown: List[str] = field(default_factory=list)

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
    def acceleration_label(self) -> str:
        """What answers traversals: `Arno <version>` when the library is loaded, else ObjectScript (4.6.0, NR7), saying
        so when `IVG_DISABLE_ARNO=1` kept the engine from loading it (R207)."""
        if self.arno.disabled:
            return "ObjectScript (Arno disabled: IVG_DISABLE_ARNO=1)"
        if not self.arno.loaded:
            return "ObjectScript (Arno not loaded)"
        version = self.arno.version.removeprefix("arno-callout").strip()
        return f"Arno {version}" if version else "Arno"

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

        unknown = "unknown (see Probe errors)"
        sync_label = "needs sync" if self.pending_sync else "in sync"
        if "sync" in self.unknown:
            sync_label = unknown
        vec_label = self.vector_index_state
        ft_label = unknown if "fulltext_index" in self.unknown else self.fulltext_index_state
        accel_label = unknown if "acceleration" in self.unknown else self.acceleration_label
        graph_label = (
            unknown if "graph" in self.unknown
            else f"{self.tables.nodes:,} nodes · {self.tables.edges:,} edges in every graph"
        )

        vec_detail = ""
        if "vector_index" in self.unknown:
            vec_label = unknown
        elif vec_label == "empty":
            # vectors stored and no ANN index over them (R225: it read "empty (6 indexed)")
            vec_label = f"no ANN index ({self.tables.node_embeddings:,} vectors, searched by a scan)"
        elif self.tables.node_embeddings > 0:
            vec_detail = f" ({self.tables.node_embeddings:,} indexed)"

        def names(found: List[str], part: str) -> str:
            return unknown if part in self.unknown else ", ".join(found) or "none"

        ivf_names = names(self.indexes.ivf_indexes, "ivf_index")
        bm25_names = names(self.indexes.bm25_indexes, "fulltext_index")
        plaid_names = names(self.indexes.plaid_indexes, "plaid_index")

        lines = [
            "IVG Engine Status",
            "═" * 50,
            # every graph's rows, where node_count() and edge_count() count the default graph's (4.6.0 R050)
            f"\nGraph:           {graph_label}  (probe: {self.probe_ms:.0f}ms)",
            f"Vector index:    {vec_label}{vec_detail}",
            f"Full-text index: {ft_label}",
            f"Acceleration:    {accel_label}",
        ]
        if self.arno.lib_path:
            if self.arno.loaded:
                state = "loaded"
            elif self.arno.lib_found is False:
                state = "not loaded: no such file on the server"
            else:
                state = "not loaded"
            lines.append(f"Arno library:    {self.arno.lib_path} ({state})")
        lines += [
            f"Sync state:      {sync_label}",
            f"IVG Schema:      {self.schema or 'none recorded'}",
        ]
        if self.legacy_objects:
            lines.append(f"Legacy objects:  {self.legacy_objects} (remove_legacy_objects() lists them)")
        if self.missing_routines:
            lines.append(
                f"Routines missing: {', '.join(self.missing_routines)} (setup could not create them; "
                "initialize_schema() logs why)"
            )

        if "ledger" in self.unknown:
            lines.append(f"Ledger:          {unknown}")
        elif self.ledger is not None:
            if self.ledger.state == "never-enabled":
                lines.append("Ledger:          not enabled")
            else:
                rej = sum(self.ledger.rejections.values()) if self.ledger.rejections else 0
                lines.append(
                    f"Ledger:          {self.ledger.state}{' (strict)' if self.ledger.strict else ''}"
                    f" · head seq {self.ledger.head_seq} · {self.ledger.commits_ok:,} commits"
                    f" · {rej:,} rejections"
                    + (f" · last verify {self.ledger.last_verify_result}" if self.ledger.last_verify_result else "")
                )

        if (self.indexes.ivf_indexes or self.indexes.plaid_indexes
                or {"ivf_index", "plaid_index"} & set(self.unknown)):
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
                f"  {tick(self.adjacency.kg_populated)} ^KG   ({kg_count} edges indexed)",
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
