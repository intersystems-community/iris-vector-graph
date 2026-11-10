import json
import logging
import numbers
import re
from typing import Any, Dict, List, Optional

from iris_vector_graph._engine._rdf_utils import graph_id_of_iri
from iris_vector_graph._engine.ledger import ledger_check as _ledger_check
from iris_vector_graph._widths import VALUE_WIDTH, check_width
from iris_vector_graph.prop_values import prop_value
from iris_vector_graph.schema_name import runs_in_ivg_schema, schema_package
from iris_vector_graph.security import vector_text

logger = logging.getLogger(__name__)


class SnapshotReadError(RuntimeError):
    """``save_snapshot`` could not read a table or global it archives; no archive was written (4.6.0 R271)."""


def _unreadable(what: str, exc: Exception) -> SnapshotReadError:
    return SnapshotReadError(
        f"save_snapshot: cannot read {what}: {exc}. No archive was written: one without it would restore an "
        "empty table (or global) in its place."
    )


def _table_missing(exc: Exception) -> bool:
    """SQLCODE -30: the table does not exist (not a refusal to read it)."""
    return _SQLCODE_MINUS_30.search(str(exc)) is not None


#: "[SQLCODE: <-30>:..." (the DB-API driver) or "SQLCODE=-30"; not -300 to -309.
_SQLCODE_MINUS_30 = re.compile(r"SQLCODE\s*[:=]\s*<?-30(?!\d)")


def _raise_nkg_version(iris_obj, floor: int) -> None:
    """Put ^NKG("$meta","version") past both the archive's and the one before restore.

    The imported ^NKG carries the archive's version, which can be one the Rust
    betweenness cache in this process already holds for another graph. See
    tests/unit/test_411_nkg_version_monotonic.py.
    """
    restored = int(iris_obj.get("^NKG", "$meta", "version") or 0)
    iris_obj.set(max(floor, restored) + 1, "^NKG", "$meta", "version")


def _raise_kg_version(iris_obj, floor: int) -> None:
    """Put ``^KG("__version")`` past both the archive's value and the one before the import.

    The import writes the archive's counter over the live one, and the graph the archive
    holds is new content either way. An answer cached while the graph was empty (between
    the erase inside a replace-restore and the import) must not match afterwards, and a
    merge-restore adds content with no other bump. Spec 236.
    """
    restored = int(iris_obj.get("^KG", "__version") or 0)
    iris_obj.set(max(floor, restored), "^KG", "__version")
    iris_obj.classMethodVoid("Graph.KG.ArnoAccel", "BumpVersion")


def _ivg_version() -> str:
    """The installed package version, resolved late.

    Imported inside the function because `iris_vector_graph/__init__.py` imports
    the engine, which imports this module — at module scope this is a cycle.
    """
    try:
        from iris_vector_graph import __version__

        return str(__version__)
    except Exception:  # pragma: no cover - a broken install, not a snapshot bug
        return "unknown"


# The storage layout a snapshot was taken from. Before spec-223 the temporal
# globals had no graph subscript, so a pre-223 archive's `^KG("tout")` keys read
# `(timestamp, source, ...)` where a graph key now belongs. Importing one puts
# timestamps in the graph position: every window query then finds nothing, and the
# restore reports success. The stamp exists so that restore can refuse instead.
SNAPSHOT_LAYOUT = "graph-scoped"

# The globals whose values include `$vector`s (and, in ^PLAID's docPacked, $Lists of them). The Native API's getters
# raise <LIST ERROR> on a `$vector` and its setters cannot write one, so these values are read and written on the
# server by Graph.KG.Snapshot (4.6.0 R302); the archive line of a vector carries "vec" (its type) or "list".
VECTOR_VALUE_GLOBALS = ("^IVF", "^PLAID", "^VecIdx")

# `Graph.KG.GraphStores.Inventory()` is the one declared inventory of stores holding
# graph content: the Eraser walks it to remove a graph, and the snapshot walks it to
# export one (ADR-0004, grilling Q20). Every name that inventory returns appears
# here, mapped to what the snapshot does with it:
#
#   "sql"     — exported as a table
#   "global"  — exported as an NDJSON global subtree
#   "derived" — deliberately not exported, rebuilt from a store that is; the reason
#               is recorded in DERIVED_REASONS
#
# A store that is merely *absent* is the defect this table exists to prevent. The
# globals list used to be hand-written next to the export loop and named `out` and
# `in` only, so all five temporal subtrees, plus `label`, `prop` and `labelset`,
# were silently dropped from every archive. tests/integration/test_snapshot_inventory.py
# compares these keys against the live inventory so the two cannot drift again.
STORE_PLAN = {
    # SQL
    "Graph_KG.nodes": "sql",
    "Graph_KG.rdf_edges": "sql",
    "Graph_KG.rdf_labels": "sql",
    "Graph_KG.rdf_props": "sql",
    "Graph_KG.rdf_reifications": "sql",
    "Graph_KG.kg_NodeEmbeddings": "sql",
    "Graph_KG.kg_EdgeEmbeddings": "sql",
    # The BM25 corpus. It was in no plan and in no inventory, so `kg_TXT` came back
    # empty after a restore that reported every table it knew about as complete.
    "Graph_KG.docs": "sql",
    # Spec 227's routes live here and nowhere else: the registry row is the only
    # record of a routed table's name, graph, model and declared width (FR-011).
    # Without it a restored install has the vectors' tables standing and unclaimed,
    # every `resolve_route` reports a miss, and the restore reports success.
    "Graph_KG.embedding_registry": "sql",
    # Spec 232's FHIR graph bookkeeping: the references that did not resolve, the url
    # each definitional key declared, and every canonical link a source carries. The
    # repository is not in the archive, so none of it can be rebuilt on restore, and
    # without it canonical links stay unresolved until a full resync.
    "Graph_KG.fhir_unresolved": "sql",
    "Graph_KG.fhir_definitions": "sql",
    "Graph_KG.fhir_canonical_refs": "sql",
    "Graph_KG.kg_NodeEmbeddings_optimized": "derived",
    # ^KG — structural adjacency. Out-degree is not stored (`deg`/`degp` are gone); the generation and the cache
    # that `Graph.KG.Adj.Degree` keeps are derived and listed below.
    '^KG("out")': "global",
    '^KG("in")': "global",
    # The two-hop counts. Exported rather than rebuilt: they are cheap to carry and
    # consistent with the adjacency in the same archive, whereas a rebuild on
    # restore would need Arno loaded on the machine doing the restoring.
    '^KG("deg2p")': "global",
    '^KG("deg2p_exact")': "global",
    # ^KG — the temporal index, all six subtrees
    '^KG("tout")': "global",
    '^KG("tin")': "global",
    '^KG("bucket")': "global",
    '^KG("tagg")': "global",
    '^KG("edgeprop")': "global",
    # spec 255: the latest-event index and its completeness flag. Exported rather than rebuilt: a restore without it
    # has no flag, and every latest read on the graph would walk timestamps until a rebuild.
    '^KG("tlast")': "global",
    # ^KG — the node stores (graph-scoped as of spec 230) and the one interning
    # subtree that still carries no graph, exported whole
    '^KG("label")': "global",
    '^KG("prop")': "global",
    '^KG("labelset")': "global",
    '^KG("deg2p_exact_merged")': "derived",
    '^KG("gen")': "derived",
    '^KG("degc")': "derived",
    '^KG("degw")': "derived",
    # Other globals
    "^NKG": "global",
    '^IVG.Ledger("tuple")': "global",
    '^IVG.Ledger("stmt")': "global",
    # Spec 234: which graphs are multigraphs. Without it a restored multigraph's
    # parallel edges come back in a graph that refuses a second edge per triple.
    "^IVG.GraphMode": "global",
    # ADR-0015: which predicates of a graph are temporal-only. Without it a restored graph would write structural
    # rows for them again.
    "^IVG.TemporalStructural": "global",
    "^ArnoKG": "derived",
    "^IVG.AlgoCache": "derived",
}

DERIVED_REASONS = {
    "Graph_KG.kg_NodeEmbeddings_optimized": (
        "the reindexed twin of kg_NodeEmbeddings, rebuilt by the vector-index "
        "migration from the rows this snapshot does carry"
    ),
    "^ArnoKG": (
        "a whole-database JSON materialization of ^KG, rebuilt on demand and "
        "invalidated by ^KG(\"__version\"); exporting it would restore a second, "
        "independently stale copy of everything already in the archive"
    ),
    "^IVG.AlgoCache": (
        "cached algorithm answers (spec 236), each stored under the ^KG(\"__version\") "
        "it was computed at; recomputed on demand, and an archive would restore answers "
        "for a graph whose counter the restore then moves past"
    ),
    '^KG("gen")': (
        "the per-source generation that validates the degree cache; rebuilt by the writers as they bump it, and "
        "cleared by a restore for every graph it wrote, because the restore sets `out`/`in` without bumping it"
    ),
    '^KG("degc")': (
        "the degree cache `Graph.KG.Adj.Degree` refills on demand from the `out` entries, each entry stamped with "
        "the generation it was counted at; an archive would restore counts stamped for a graph the restore changes"
    ),
    '^KG("degw")': (
        "the whole-graph stamp that says every degree cache entry of a graph is valid (the `^KG(\"__version\")` current "
        "when the cache was last warmed); a restore changes `out`/`in` without bumping it, so it is cleared with the cache"
    ),
    '^KG("deg2p_exact_merged")': (
        "Arno's two-hop counts over ^NKG, rebuilt by "
        "Graph.KG.NKGAccelTraversal:Build2HopExact from the ^NKG this snapshot does "
        "carry; since ^NKG's interning has no graph dimension the numbers cannot be "
        "attributed to a graph, so restoring them would put one graph's counts in "
        "front of another's"
    ),
}


#: Where the routed-embedding section of a snapshot's metadata lives. Routed tables
#: are not in the inventory one by one on purpose — which of them exists is a fact
#: about the registry, not about the schema — so the archive records the ones it
#: found rather than a plan that could name a table nobody created.
ROUTED_TABLES_KEY = "routed_embedding_tables"

#: The order `restore_snapshot` loads the plain SQL tables in, foreign keys first.
#: Module-level rather than local to the method because a table can otherwise sit in
#: the export and be missing from the import with nothing able to compare the two —
#: which is how the registry was exported and never read back.
RESTORE_TABLE_ORDER = [
    "Graph_KG_nodes.ndjson",
    "Graph_KG_rdf_edges.ndjson",
    "Graph_KG_rdf_labels.ndjson",
    "Graph_KG_rdf_props.ndjson",
    "Graph_KG_rdf_reifications.ndjson",
    # `docs` deliberately carries no FK onto `nodes` (a caller may write a document
    # before the node exists), so its position here is for readability only.
    "Graph_KG_docs.ndjson",
    # After the nodes: the routed tables rebuilt from these rows carry an FK onto
    # `nodes (graph_id, node_id)`.
    "Graph_KG_embedding_registry.ndjson",
    # No FKs. The restore creates these tables first: they exist only where a FHIR
    # graph was registered (`GraphSchema.get_fhir_graph_schema_sql`).
    "Graph_KG_fhir_unresolved.ndjson",
    "Graph_KG_fhir_definitions.ndjson",
    "Graph_KG_fhir_canonical_refs.ndjson",
    "Graph_KG_fhir_graphs.ndjson",
    # No FK: a code may map to a node of a graph the archive does not hold.
    "Graph_KG_code_crosswalk.ndjson",
    "Graph_KG_rdf_namespaces.ndjson",
    "Graph_KG_table_mappings.ndjson",
    "Graph_KG_relationship_mappings.ndjson",
    "Graph_KG_fhir_bridges.ndjson",
]

#: The plain tables `save_snapshot` exports with `SELECT *`. A table that does not
#: exist (the lazy FHIR tables, in a namespace with no FHIR graph) exports no file.
SQL_TABLES_EXPORT = [
    "Graph_KG.nodes",
    "Graph_KG.rdf_edges",
    "Graph_KG.rdf_labels",
    "Graph_KG.rdf_props",
    "Graph_KG.rdf_reifications",
    # The BM25 corpus, which no archive carried until 4.0.0: `kg_TXT` came back empty
    # after a restore that reported success, and nothing compared the two because
    # `docs` was in no inventory either.
    "Graph_KG.docs",
    # The registry names every route; without it the restored tables are orphans and
    # every scoped search reports a miss (spec 227, FR-011).
    "Graph_KG.embedding_registry",
    "Graph_KG.fhir_unresolved",
    "Graph_KG.fhir_definitions",
    "Graph_KG.fhir_canonical_refs",
    "Graph_KG.fhir_graphs",
    "Graph_KG.code_crosswalk",
    "Graph_KG.rdf_namespaces",
    "Graph_KG.table_mappings",
    "Graph_KG.relationship_mappings",
    "Graph_KG.fhir_bridges",
]

#: Tables with no single identifying first column. A merge restore skips a row that
#: is already present; for these, "present" means every column matches. On the first
#: column alone, which is `graph_id`, a graph's second row matched its first.
FHIR_TABLES = frozenset(
    ("Graph_KG.fhir_unresolved", "Graph_KG.fhir_definitions", "Graph_KG.fhir_canonical_refs")
)

#: The tables `GraphSchema.get_fhir_graph_schema_sql` creates on a namespace's first FHIR registration. A namespace
#: with none exports no file for them, and a restore of an archive holding one creates them first.
LAZY_FHIR_TABLES = FHIR_TABLES | {"Graph_KG.fhir_graphs"}

#: Configuration the archive carries beside the content, with each table's primary key: a FHIR graph's registration
#: (its repository, schemas and the watermarks of the last sync) and the code crosswalk that maps codes onto nodes.
#: Neither is derived from the archive's rows, and until 4.7.0 neither was archived: a restore elsewhere left the
#: graph unregistered and every concept lookup empty (row 26). They are not in `STORE_PLAN`, which is
#: `Graph.KG.GraphStores.Inventory()`'s graph content, because no erase removes them: a registration outlives an
#: erase (its watermarks reset), and the crosswalk is no graph's. A replacing restore writes the archive's row over
#: the one there (the restored edges are the archive's, and so are the watermarks that account for them); a merge
#: keeps the row there. Both match on the key: `code_system_uri`, the first column, is shared by every code of a
#: system, so the generic merge guard would skip all but one.
CONFIGURATION_TABLES = {
    "Graph_KG.fhir_graphs": ("graph_id",),
    "Graph_KG.code_crosswalk": ("target_graph", "target_node_id", "code_system_uri", "code"),
    # 4.7.0 row 26b: the RDF prefixes `register_namespace` records, the SQL tables and relationships mapped as nodes
    # and edges (`map_sql_table`, `map_sql_relationship`), and the deprecated FHIR code bridges
    "Graph_KG.rdf_namespaces": ("prefix",),
    "Graph_KG.table_mappings": ("label",),
    "Graph_KG.relationship_mappings": ("source_label", "predicate", "target_label"),
    "Graph_KG.fhir_bridges": ("fhir_code", "kg_node_id"),
}


def routed_export_plan(registry_rows) -> Dict[str, Dict[str, Any]]:
    """Which routed tables a snapshot exports, read off the registry rows.

    ``registry_rows`` are ``(table_name, graph_id, model_key, dimension, dtype)``
    tuples straight out of ``Graph_KG.embedding_registry``. The answer is keyed by
    qualified table name, which is what the export and the restore both use.

    Two kinds of row are dropped rather than planned:

    * a legacy table the registry claims (``kg_NodeEmbeddings`` and its optimized
      twin). Those are exported by name already, and a restore that ran
      ``CREATE TABLE`` for one would be creating a table the schema owns.
    * anything whose name is not the routed shape ``kg_emb_<16 hex>``. That name
      becomes an identifier inside DDL on restore, and the only names routing
      produces come from ``routing.route_table_name``; anything else in that column
      is damage or somebody else's table.

    A row with no declared width is dropped too: the width is what makes the
    ``CREATE TABLE`` on restore possible at all, and guessing one produces a table
    whose first insert is SQLCODE -104.
    """
    from iris_vector_graph.security import is_routed_embedding_table

    plan: Dict[str, Dict[str, Any]] = {}
    for row in registry_rows:
        table_name, graph_id, model_key, dimension, dtype = (list(row) + [None] * 5)[:5]
        if not is_routed_embedding_table(str(table_name or "")):
            continue
        try:
            width = int(dimension)
        except (TypeError, ValueError):
            logger.warning(
                "Snapshot: route %s has no declared width in the registry and cannot "
                "be restored; its vectors are not exported",
                table_name,
            )
            continue
        if width <= 0:
            continue
        plan[f"Graph_KG.{table_name}"] = {
            "graph_id": graph_id or "",
            "model_key": model_key,
            "dimension": width,
            "dtype": str(dtype or "DOUBLE").upper(),
        }
    return plan


#: `^KG` subtrees an archive written by an older release may hold that this release no longer has. A restore skips
#: their lines instead of planting counters nothing reads and nothing keeps right. (3.2.0 and earlier wrote them.)
LEGACY_KG_COUNTERS = ("deg", "degp")

#: `^KG` subtrees that are derived from the adjacency (the generation and the degree cache). Never exported; a
#: restore skips them if an archive carries them anyway, and kills them for every graph whose `out`/`in` it wrote.
DERIVED_KG_CACHE = ("gen", "degc", "degw")

#: `^KG` subtrees the adjacency functional index derives from the rows (spec 252). Still exported, for a restore into
#: a namespace without the index; a restore into one with it skips them.
INDEX_DERIVED_KG = ("out", "in", "label")


def ledger_storage_globals(schema: str) -> List[str]:
    """The default-storage globals of the two persistent ledger classes, which the deploy compiles into the IVG
    Schema's package (4.6.0 R084): ``Graph.KG.LedgerRevisionD`` for ``Graph_KG``, ``IVGALT.LedgerRevisionD`` for
    ``IVGALT``."""
    package = schema_package(schema)
    return [f"{package}.LedgerRevisionD", f"{package}.LedgerRevisionI", f"{package}.LedgerStatsD"]


_COLUMN_NAME = re.compile(r"[A-Za-z_%][A-Za-z0-9_]*\Z")


def _column_names(row: Dict[str, Any]) -> List[str]:
    """An archived row's keys, which ``restore_snapshot`` writes as the INSERT's column list: each must be a plain SQL
    identifier, or the row is refused (4.6.0: a key is archive text, and pasted as it came it could add SQL)."""
    cols = list(row.keys())
    bad = [c for c in cols if not isinstance(c, str) or not _COLUMN_NAME.match(c)]
    if bad:
        raise ValueError(f"archived row has a column name that is not an identifier: {bad[0]!r}")
    return cols


def snapshot_globals(schema: str) -> List[tuple]:
    """``(global name, subscript prefixes)`` for every global ``save_snapshot`` archives: the one list both the save
    and the restore's allow-list read."""
    return [
        # Every ^KG subtree STORE_PLAN declares exported, not a second list
        # of them. The two lists disagreed for as long as both existed.
        ("KG", kg_export_subscripts()),
        ("BM25Idx", [[]]),
        ("IVF", [[]]),
        ("PLAID", [[]]),
        ("VecIdx", [[]]),
        ("NKG", [[]]),
        # spec 213: revision ledger storage (FR-043). ^IVG.Ledger holds head,
        # records, statement map, tuple index, idempotency; the D/I globals
        # are the default storage of the two persistent ledger classes, which
        # live in the IVG Schema's package (R084).
        ("IVG.Ledger", [[]]),
        *((g, [[]]) for g in ledger_storage_globals(schema)),
        # spec 234: per-graph multigraph mode
        ("IVG.GraphMode", [[]]),
        # ADR-0015: per-graph temporal-only predicates
        ("IVG.TemporalStructural", [[]]),
    ]


#: The ledger storage an archive made under the default IVG Schema holds, restored under any other one as before.
LEGACY_SNAPSHOT_GLOBALS = frozenset(ledger_storage_globals("Graph_KG"))


def restorable_globals(schema: str) -> frozenset:
    """The global names ``restore_snapshot`` writes: what ``save_snapshot`` archives, and the ledger storage of LEGACY_SNAPSHOT_GLOBALS.

    The name comes from an archive entry (``globals/<name>.ndjson``), and an archive is a file anyone can write; any
    other name is skipped, not written, so an archive cannot set ^ROUTINE, ^oddDEF or another application's
    globals (4.6.0)."""
    return frozenset(g for g, _ in snapshot_globals(schema)) | LEGACY_SNAPSHOT_GLOBALS


def kg_export_subscripts() -> List[List[str]]:
    """The ``^KG`` subtrees ``save_snapshot`` walks, read off ``STORE_PLAN``.

    Derived rather than restated: a second hand-maintained list is exactly how the
    temporal subtrees came to be in the inventory and absent from the export.
    """
    prefix, suffix = '^KG("', '")'
    return [
        [name[len(prefix) : -len(suffix)]]
        for name, disposition in STORE_PLAN.items()
        if name.startswith(prefix) and disposition == "global"
    ]


class SnapshotMixin:
    """Graph snapshot/serialization mixin for IRISGraphEngine.

    Provides graph I/O operations: import from networkx/RDF/OBO ontologies,
    export/import snapshots as portable ZIP archives, and NDJSON graph serialization.
    """

    def load_networkx(
        self,
        G,
        label_attr: str = "type",
        skip_existing: bool = True,
        progress_callback=None,
        auto_sync: bool = True,
        auto_rebuild_kg: bool = None,
    ) -> dict:
        """Write a networkx graph's nodes and edges into the default graph, one ``create_node`` or
        ``create_edge`` call each. A node's labels come from its ``label_attr`` attribute (``"type"``); an edge's
        type from its ``predicate``, ``label`` or ``key`` attribute, else ``is_a`` (4.6.0 R225).

        A node's labels come from ``label_attr`` (a string or a list), else its ``namespace`` attribute; its
        other attributes become properties, stored as ``create_node`` stores them (4.6.4: a number as the number, a
        list or map as its JSON; they were their ``str()``), a value of another type as its ``str()``. A value is
        stored whole; one whose stored text is longer than ``rdf_props.val`` (64000 characters) raises ``ValueError``
        naming the node and property, before anything is written (4.7.0: text was cut to 60000 characters without a
        word). An edge's predicate is its
        ``predicate``, ``label`` or ``key`` attribute, else a multigraph's string edge key (where obonet keeps an OBO
        relationship's type), else ``is_a``; the rest become qualifiers.
        Returns ``nodes``, ``edges``, ``skipped_nodes`` and ``skipped_edges`` (calls that returned
        ``False``), and with ``auto_sync`` calls ``sync()`` when anything was added. ``skip_existing`` is
        accepted and not used; ``auto_rebuild_kg`` is a deprecated name for ``auto_sync``.
        """
        _ledger_check(self, "load_networkx")
        if auto_rebuild_kg is not None:
            import warnings

            warnings.warn(
                "auto_rebuild_kg= is deprecated. Use auto_sync= instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            auto_sync = auto_rebuild_kg
        added_nodes = 0
        added_edges = 0
        skipped_nodes = 0
        skipped_edges = 0
        total_nodes = G.number_of_nodes()
        total_edges = G.number_of_edges()

        def _node_props(data):
            props = {}
            for k, v in data.items():
                if k in (label_attr, "namespace") or v is None:
                    continue
                # 4.6.4: a number, boolean, string, list or map goes to create_node as it is, which stores it as a
                # Cypher write does; str() stored 30 as '30', True as 'True' and a list as its Python repr
                passed = isinstance(v, (str, list, dict, numbers.Real)) or type(v).__name__ in ("bool_", "bool")
                props[k] = v if passed else str(v)
            return props

        # 4.7.0 row 3d: every value is checked before the first node is written, as create_node writes one at a time
        for node_id, data in G.nodes(data=True):
            for k, v in _node_props(data).items():
                check_width("load_networkx", f"property {k!r} value", prop_value(v), VALUE_WIDTH, f"node {node_id!r} ")
        for node_id, data in G.nodes(data=True):
            labels = []
            if label_attr and label_attr in data:
                val = data[label_attr]
                labels = [val] if isinstance(val, str) else list(val)
            elif "namespace" in data:
                labels = [data["namespace"]]
            props = _node_props(data)
            if self.create_node(node_id=str(node_id), labels=labels, properties=props):
                added_nodes += 1
            else:
                skipped_nodes += 1
            n_done = added_nodes + skipped_nodes
            if n_done % 10000 == 0:
                logger.info(
                    f"Nodes: {n_done:,}/{total_nodes:,} ({added_nodes:,} added, {skipped_nodes:,} skipped)"
                )
                if progress_callback:
                    progress_callback(n_done, added_edges + skipped_edges)
        logger.info(f"Nodes complete: {added_nodes:,} added, {skipped_nodes:,} skipped")
        if progress_callback:
            progress_callback(added_nodes + skipped_nodes, 0)
        from iris_vector_graph.bulk_loader import networkx_edge_rows

        for src, predicate, dst, qualifiers in networkx_edge_rows(G):
            if self.create_edge(
                source_id=src,
                predicate=predicate,
                target_id=dst,
                qualifiers=qualifiers,
            ):
                added_edges += 1
            else:
                skipped_edges += 1
            e_done = added_edges + skipped_edges
            if e_done % 10000 == 0:
                logger.info(
                    f"Edges: {e_done:,}/{total_edges:,} ({added_edges:,} added, {skipped_edges:,} skipped)"
                )
                if progress_callback:
                    progress_callback(added_nodes + skipped_nodes, e_done)
        logger.info(f"Edges complete: {added_edges:,} added, {skipped_edges:,} skipped")
        if progress_callback:
            progress_callback(added_nodes + skipped_nodes, added_edges + skipped_edges)
        stats = {
            "nodes": added_nodes,
            "edges": added_edges,
            "skipped_nodes": skipped_nodes,
            "skipped_edges": skipped_edges,
        }
        if auto_sync and (added_nodes > 0 or added_edges > 0):
            self.sync()
        return stats

    def import_rdf(
        self,
        path: str,
        format: Optional[str] = None,
        batch_size: int = 10000,
        progress=None,
        infer=False,
        graph: Optional[str] = None,
    ) -> Dict[str, int]:
        """Parse an RDF file with rdflib and write its triples as nodes, edges and properties; commits per batch.

        The format comes from the extension when ``format`` is ``None`` (Turtle otherwise). An IRI or blank
        node object becomes an edge, a literal becomes a property of the subject (the predicate's local name
        as the key, plus a ``_lang`` key for a language tag). Triples go into ``graph``; in N-Quads or TriG
        a quad's own graph name wins. ``^KG`` is rebuilt afterwards, and ``infer`` (``True`` or a rule set
        name) runs ``materialize_inference``. Returns ``triples``, ``nodes``, ``edges`` and ``properties``
        (statements that succeeded, rows already present included), and ``inferred`` with ``infer``; a row
        that fails is logged and skipped. A literal is stored whole; one longer than ``rdf_props.val`` (64000
        characters) raises ``ValueError`` naming its subject and predicate, before anything is written (4.7.0: it
        was cut to 64000 and counted as written). Needs ``rdflib``.
        """
        _ledger_check(self, "import_rdf")
        try:
            import rdflib  # noqa: F401 - presence probe; the ImportError below is the point
            from rdflib import (
                BNode,
                ConjunctiveGraph,
                Graph,
            )
            from rdflib import Literal as RDFLiteral
            from rdflib import (
                URIRef,
            )
        except ImportError:
            raise ImportError("import_rdf requires rdflib: pip install rdflib")

        if format is None:
            ext = path.rsplit(".", 1)[-1].lower()
            format = {
                "ttl": "turtle",
                "nt": "nt",
                "nq": "nquads",
                "n3": "n3",
                "trig": "trig",
                "jsonld": "json-ld",
            }.get(ext, "turtle")

        is_quads = format in ("nquads", "trig")
        if is_quads:
            g = ConjunctiveGraph()
        else:
            g = Graph()
        g.parse(path, format=format)
        # 4.7.0 row 3d: checked before the first batch is written, as the batches commit one by one
        for s, p, o, *_ in g.quads() if is_quads else g:
            if not isinstance(o, (URIRef, BNode)):
                check_width("import_rdf", "literal", o, VALUE_WIDTH, f"subject {str(s)!r} predicate {str(p)!r} ")

        from iris_vector_graph._write_txn import caller_transaction_level, refuse_in_caller_transaction, write_txn

        # 4.7.0: each batch is a transaction of IVG's, nested in the caller's when there is one (conn.commit()
        # committed the caller's work). Inside one, ^KG is the adjacency index's, kept in the same transaction; a
        # namespace without the index rebuilds ^KG with BuildKG on another connection, which cannot wait for it.
        in_caller = caller_transaction_level(self.conn) is not None
        if in_caller and not self._adjacency_indexed:
            refuse_in_caller_transaction(
                self.conn, "import_rdf()",
                "this namespace has no adjacency index, so ^KG is rebuilt (BuildKG) outside the transaction",
            )
        cursor = self.conn.cursor()
        nodes_inserted = 0
        edges_inserted = 0
        props_inserted = 0
        triple_count = 0
        blank_prefix = f"_:{abs(hash(path)) % 10**8}:"

        def _node_id(term):
            if isinstance(term, URIRef):
                return str(term)
            if isinstance(term, BNode):
                return f"{blank_prefix}{term}"
            return str(term)

        def _ensure_node(nid, node_graph):
            """Create the node in the graph its edges are going into (FR-008).

            `fk_edges_src` / `fk_edges_dest` reference `(graph_id, node_id)` in
            4.0.0. Creating these nodes in the default graph left every edge of a
            named-graph import failing its foreign key, swallowed below, and
            `import_rdf` returned `edges: 0` for a file it had just parsed.
            """
            try:
                cursor.execute(
                    f"INSERT INTO {self._t('nodes')} (node_id, graph_id) SELECT ?, ? "
                    f"WHERE NOT EXISTS (SELECT 1 FROM {self._t('nodes')} "
                    f"WHERE node_id = ? AND COALESCE(graph_id, '') = COALESCE(?, ''))",
                    [nid, node_graph or "", nid, node_graph or ""],
                )
                return True
            except Exception as e:
                logger.warning(
                    "import_rdf: node %r not created in graph %r: %s", nid, node_graph, e
                )
                return False

        # A node ID can appear in more than one graph within one file (nquads/trig),
        # so the batch is keyed on the pair, not the ID.
        batch_nodes: set = set()
        batch_edges: List = []
        batch_props: List = []

        def _flush():
            nonlocal nodes_inserted, edges_inserted, props_inserted
            txn = write_txn(self.conn, self._ivg_schema, cursor)
            for nid, node_graph in batch_nodes:
                if _ensure_node(nid, node_graph):
                    nodes_inserted += 1
            for s, p, o, edge_graph in batch_edges:
                try:
                    if edge_graph:
                        cursor.execute(
                            f"INSERT INTO {self._t('rdf_edges')} (s, p, o_id, graph_id) SELECT ?, ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM {self._t('rdf_edges')} WHERE s = ? AND p = ? AND o_id = ? AND graph_id = ?)",
                            [s, p, o, edge_graph, s, p, o, edge_graph],
                        )
                    else:
                        cursor.execute(
                            f"INSERT INTO {self._t('rdf_edges')} (s, p, o_id, graph_id) SELECT ?, ?, ?, '' WHERE NOT EXISTS (SELECT 1 FROM {self._t('rdf_edges')} WHERE s = ? AND p = ? AND o_id = ? AND COALESCE(graph_id, '') = '')",
                            [s, p, o, s, p, o],
                        )
                    edges_inserted += 1
                except Exception as e:
                    logger.warning(
                        "import_rdf: edge %r -[%r]-> %r not written in graph %r: %s",
                        s, p, o, edge_graph, e,
                    )
            for s, k, v, prop_graph in batch_props:
                try:
                    # FR-034: a property belongs to a node *in a graph*. Written
                    # unscoped, an import into one graph set the property of a node
                    # of the same ID in another.
                    cursor.execute(
                        f'INSERT INTO {self._t("rdf_props")} (graph_id, s, "key", val) '
                        f"VALUES (?, ?, ?, ?)",
                        [prop_graph or "", s, k, v],
                    )
                    props_inserted += 1
                except Exception as e:
                    logger.warning(
                        "import_rdf: property %r of %r not written in graph %r: %s",
                        k, s, prop_graph, e,
                    )
            try:
                txn.commit()
            except Exception as e:
                txn.rollback()
                # It was passed over (4.6.0 R276): the batch counted as written and was not.
                from iris_vector_graph.errors import GraphWriteError

                raise GraphWriteError(
                    "import_rdf", e,
                    left=f"the batches before this one are committed ({triple_count} triples read so far)",
                ) from e
            batch_nodes.clear()
            batch_edges.clear()
            batch_props.clear()

        triples_iter = g.quads() if is_quads else ((s, p, o, None) for s, p, o in g)

        for s, p, o, graph_ctx in triples_iter:
            triple_count += 1
            s_id = _node_id(s)
            p_str = _node_id(p)

            effective_graph = graph
            if graph_ctx is not None:
                # quads() yields the context Graph, whose str() is a Turtle description of the graph, not its name
                # (R100: every quad went into a graph named "<...> a rdfg:Graph;..."). The name is its identifier;
                # rdflib names the default graph with a blank node or urn:x-rdflib:default.
                ident = getattr(graph_ctx, "identifier", graph_ctx)
                ctx_str = str(ident)
                if (
                    isinstance(ident, URIRef)
                    and ctx_str
                    and ctx_str not in ("DEFAULT", "urn:x-rdflib:default")
                ):
                    effective_graph = graph_id_of_iri(ctx_str)  # urn:ivg:graph:trials is graph `trials` (R122)

            batch_nodes.add((s_id, effective_graph))

            if isinstance(o, RDFLiteral):
                key = p_str.rsplit("/", 1)[-1].rsplit("#", 1)[-1][:128]
                val = str(o)
                lang = getattr(o, "language", None)
                if lang:
                    batch_props.append((s_id, key, val, effective_graph))
                    batch_props.append((s_id, f"{key}_lang", lang, effective_graph))
                else:
                    batch_props.append((s_id, key, val, effective_graph))
            elif isinstance(o, (URIRef, BNode)):
                o_id = _node_id(o)
                batch_nodes.add((o_id, effective_graph))
                batch_edges.append((s_id, p_str, o_id, effective_graph))
            else:
                batch_props.append(
                    (s_id, p_str[:128], str(o), effective_graph)
                )

            if triple_count % batch_size == 0:
                _flush()
                if progress:
                    progress(triple_count, 0)

        _flush()

        # inside the caller's transaction the adjacency index wrote ^KG with each row (above)
        if not in_caller:
            try:
                self._iris_obj().classMethodVoid("Graph.KG.Traversal", "BuildKG")
            except Exception as e:
                logger.warning(f"import_rdf BuildKG failed (^KG may be stale): {e}")

        result = {
            "triples": triple_count,
            "nodes": nodes_inserted,
            "edges": edges_inserted,
            "properties": props_inserted,
        }

        if infer:
            rules = infer if isinstance(infer, str) else "rdfs"
            inf_result = self.materialize_inference(rules=rules, graph=graph)
            result["inferred"] = inf_result.get("inferred", 0)

        return result

    @runs_in_ivg_schema
    def save_snapshot(
        self,
        path: str,
        layers: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Write the whole database's graph content to a ZIP archive at ``path``; reads only.

        ``layers`` picks ``"sql"`` (the graph tables, ``kg_NodeEmbeddings``, ``kg_EdgeEmbeddings`` and every
        routed embedding table, one NDJSON file each) and ``"globals"`` (``^KG``, ``^NKG``, the BM25, IVF,
        PLAID and vector indexes, the ledger and the graph-mode globals); both by default. Every graph is
        included. ``metadata.json`` records the layout, versions, per-table row counts and routed tables.
        Returns ``path``, ``tables`` (rows per table), ``globals`` (names written) and ``snapshot_ts``. A
        table or global it cannot read (a login without SELECT on it, say) raises ``SnapshotReadError``, a
        ``RuntimeError`` naming it and IRIS's error, and no archive is written. Before 4.6.0 (R271) such a table
        was recorded with 0 rows, so a restore from the archive emptied it. A routed table the registry names but
        the database does not hold is archived as a route of 0 rows (the restore recreates it empty), and the
        FHIR tables, which exist only once a FHIR graph was set up, are left out when absent.
        """
        import json as _json
        import time as _time
        import zipfile as _zipfile

        if layers is None:
            layers = ["sql", "globals"]

        ts = int(_time.time() * 1000)
        import sys as _sys

        # Imported here, not at module scope: the name was used below without ever
        # being imported, so the version probe raised NameError, the bare `except`
        # ate it, and every archive ever written recorded "unknown".
        from iris_vector_graph.schema import _call_classmethod

        try:
            iris_ver = str(_call_classmethod(self.conn, "%SYSTEM.Version", "GetVersion"))
        except Exception:
            iris_ver = "unknown"
        metadata: Dict[str, Any] = {
            "version": "1.1",
            "layout": SNAPSHOT_LAYOUT,
            "globals_format": "ndjson",
            "created_ts": ts,
            # The installed package version, not a literal: a hardcoded string
            # names whichever release last edited this line, and an archive that
            # cannot be attributed to the code that wrote it is no help upgrading.
            "ivg_version": _ivg_version(),
            "iris_version": iris_ver,
            "python_version": f"{_sys.version_info.major}.{_sys.version_info.minor}",
            "has_vector_sql": False,
            "embedding_dim": self.embedding_dimension or 0,
            "layers": layers,
            "tables": {},
            "globals": {},
            # Spec 227: the routed embedding tables this archive holds, with the
            # widths the restore has to recreate them at. Empty on an install with
            # no routes, which is a different fact from absent (an archive written
            # before routes existed), and restore treats them the same only because
            # both mean "no routed tables here".
            ROUTED_TABLES_KEY: {},
        }

        sql_data: Dict[str, str] = {}
        globals_data: Dict[str, bytes] = {}

        VECTOR_TABLE = "Graph_KG.kg_NodeEmbeddings"

        if "sql" in layers:
            cursor = self.conn.cursor()
            for table in SQL_TABLES_EXPORT:
                try:
                    cursor.execute(f"SELECT * FROM {table}")
                    all_desc = cursor.description
                    rows = cursor.fetchall()
                    # Row ids and computed columns: restore writes neither (IRIS refuses a value for a computed field).
                    _ROWID_NAMES = {"edge_id", "reification_id", "label_id", "prop_id", "val_key"}
                    skip = {i for i, d in enumerate(all_desc) if d[0].lower() in _ROWID_NAMES}
                    cols = [d[0].lower() for i, d in enumerate(all_desc) if i not in skip]
                    lines = []
                    for row in rows:
                        lines.append(
                            _json.dumps(
                                {
                                    k: (
                                        None
                                        if v is None
                                        else (
                                            v.isoformat()
                                            if hasattr(v, "isoformat")
                                            else (
                                                float(v)
                                                if hasattr(v, "__float__")
                                                and not isinstance(v, (int, str, bool))
                                                else v
                                            )
                                        )
                                    )
                                    for k, v in zip(
                                        cols,
                                        [val for i, val in enumerate(row) if i not in skip],
                                    )
                                }
                            )
                        )
                    sql_data[table] = "\n".join(lines)
                    metadata["tables"][table] = len(rows)
                except Exception as e:
                    # The FHIR tables exist only once a FHIR graph was set up; a namespace
                    # without them has nothing to archive there, and the restore creates
                    # them only for an archive that holds their files.
                    # `code_crosswalk` is missing from a namespace initialised before 4.1.0.
                    if (table in LAZY_FHIR_TABLES or table in CONFIGURATION_TABLES) and _table_missing(e):
                        continue
                    raise _unreadable(table, e) from e

            try:
                # `SELECT id` here read the table's RowID, so a snapshot recorded
                # integers where node IDs belong and restored vectors attached to
                # nothing — with every INSERT succeeding, so the restore reported the
                # full count. `graph_id` is exported because a vector belongs to a
                # graph now; a snapshot without it cannot be put back (spec 227).
                cursor.execute(
                    f"SELECT graph_id, node_id, emb, metadata FROM {VECTOR_TABLE}"
                )
                rows = cursor.fetchall()
                lines = []
                for row in rows:
                    gid, nid, emb_val, meta_val = row[0], row[1], row[2], row[3]
                    emb_str = str(emb_val) if emb_val is not None else None
                    lines.append(
                        _json.dumps(
                            {
                                "graph_id": gid or "",
                                "node_id": nid,
                                "emb": emb_str,
                                "metadata": meta_val,
                            }
                        )
                    )
                sql_data[VECTOR_TABLE] = "\n".join(lines)
                metadata["tables"][VECTOR_TABLE] = len(rows)
                metadata["has_vector_sql"] = True
            except Exception as e:
                raise _unreadable(VECTOR_TABLE, e) from e

            EDGE_VECTOR_TABLE = "Graph_KG.kg_EdgeEmbeddings"
            try:
                cursor.execute(f"SELECT s, p, o_id, emb FROM {EDGE_VECTOR_TABLE}")
                rows = cursor.fetchall()
                lines = []
                for row in rows:
                    s_val, p_val, o_val, emb_val = row[0], row[1], row[2], row[3]
                    emb_str = str(emb_val) if emb_val is not None else None
                    lines.append(
                        _json.dumps({"s": s_val, "p": p_val, "o_id": o_val, "emb": emb_str})
                    )
                sql_data[EDGE_VECTOR_TABLE] = "\n".join(lines)
                metadata["tables"][EDGE_VECTOR_TABLE] = len(rows)
            except Exception as e:
                raise _unreadable(EDGE_VECTOR_TABLE, e) from e

            # Spec 227's routed tables. Discovered from the registry rather than
            # listed: the inventory names the registry, and which `kg_emb_<hash>`
            # tables exist is a fact that registry holds. A fixed list here could
            # only ever name tables nobody created or miss the ones somebody did.
            registry_table = self._registry_table()
            try:
                cursor.execute(
                    "SELECT table_name, graph_id, model_key, dimension, dtype "
                    f"FROM {registry_table}"
                )
                routed_plan = routed_export_plan(cursor.fetchall())
            except Exception as e:
                raise _unreadable(registry_table, e) from e

            for routed_table, route in routed_plan.items():
                try:
                    cursor.execute(
                        f"SELECT graph_id, node_id, emb, metadata FROM {routed_table}"
                    )
                    rows = cursor.fetchall()
                except Exception as e:
                    # A registry row naming a table that is not there: reported, not
                    # skipped quietly. The route is in the archive either way, so a
                    # restore recreates it empty rather than losing the identity. A
                    # table that is there and refused to this login is another fact:
                    # 0 rows would be a lie about it (R271).
                    if not _table_missing(e):
                        raise _unreadable(routed_table, e) from e
                    logger.warning(
                        "Snapshot: route %s is named by the registry but not readable: %s",
                        routed_table,
                        e,
                    )
                    metadata[ROUTED_TABLES_KEY][routed_table] = dict(route, rows=0)
                    continue
                lines = []
                for row in rows:
                    gid, nid, emb_val, meta_val = row[0], row[1], row[2], row[3]
                    lines.append(
                        _json.dumps(
                            {
                                "graph_id": gid or "",
                                "node_id": nid,
                                "emb": str(emb_val) if emb_val is not None else None,
                                "metadata": meta_val,
                            }
                        )
                    )
                sql_data[routed_table] = "\n".join(lines)
                metadata["tables"][routed_table] = len(rows)
                metadata[ROUTED_TABLES_KEY][routed_table] = dict(route, rows=len(rows))

        if "globals" in layers:
            GLOBALS_EXPORT = snapshot_globals(getattr(self, "_schema_prefix", "Graph_KG"))
            # A global that does not exist reads as no lines; any error is a read that
            # failed, and the archive would restore the global empty (R271).
            try:
                iris_obj = self._iris_obj()
            except Exception as e:
                raise _unreadable("the globals (no Native API on this connection; layers=['sql'] skips them)", e) from e
            for gname, subscript_prefixes in GLOBALS_EXPORT:
                lines = []
                for prefix_subs in subscript_prefixes:
                    try:
                        lines.extend(
                            self._export_global_to_ndjson(iris_obj, f"^{gname}", prefix_subs)
                        )
                    except Exception as eg:
                        raise _unreadable(f"^{gname}{prefix_subs or ''}", eg) from eg
                if lines:
                    content = "\n".join(lines).encode("utf-8")
                    globals_data[gname] = content
                    metadata["globals"][gname] = {
                        "format": "ndjson",
                        "subscripts": [s for sl in subscript_prefixes for s in sl],
                        "size": len(content),
                    }
        with _zipfile.ZipFile(path, "w", _zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("metadata.json", _json.dumps(metadata, indent=2))
            for table, content in sql_data.items():
                safe_name = table.replace(".", "_").replace("/", "_")
                zf.writestr(f"sql/{safe_name}.ndjson", content)
            for gname, content in globals_data.items():
                zf.writestr(f"globals/{gname}.ndjson", content)

        return {
            "path": path,
            "tables": metadata["tables"],
            "globals": list(globals_data.keys()),
            "snapshot_ts": ts,
        }

    @staticmethod
    def snapshot_info(path: str) -> Dict[str, Any]:
        """Read a snapshot archive's ``metadata.json`` without touching the database.

        Returns the whole ``metadata`` dict and, from it, ``tables``, ``has_vector_sql``, ``version``,
        ``layout``, ``snapshot_ts`` and ``globals``. A static method: ``IRISGraphEngine.snapshot_info(path)``
        needs no connection.
        """
        import json as _json
        import zipfile as _zipfile

        with _zipfile.ZipFile(path, "r") as zf:
            with zf.open("metadata.json") as f:
                metadata = _json.loads(f.read())
        return {
            "metadata": metadata,
            "tables": metadata.get("tables", {}),
            "has_vector_sql": metadata.get("has_vector_sql", False),
            "version": metadata.get("version", "unknown"),
            # None means the archive predates the stamp, which is a different fact
            # from "graph-scoped" and has to stay distinguishable from it.
            "layout": metadata.get("layout"),
            "snapshot_ts": metadata.get("created_ts", 0),
            "globals": metadata.get("globals", {}),
        }

    @runs_in_ivg_schema
    def restore_snapshot(
        self,
        path: str,
        merge: bool = False,
    ) -> Dict[str, Any]:
        """Load a ``save_snapshot`` archive into this database; writes.

        Without ``merge`` it first calls ``erase_all()``, emptying every graph, and then inserts the
        archive's rows and globals; with ``merge`` it keeps what is there and inserts rows that are not.
        Routed embedding tables are recreated at the archive's widths. An archive of another storage layout
        raises ``ValueError`` before anything is erased. Returns ``restored_tables``, ``failed_rows`` (per
        table, rows the database refused; empty means complete), ``restored_globals``, ``restored_layers``
        and ``snapshot_ts``. A global it cannot write (or an archive with globals on a connection without the
        Native API) raises ``RuntimeError`` naming it, the tables and globals already written staying written;
        before 4.6.0 (R271) that was a logged warning and a result that looked complete. Likewise a table whose
        commit fails raises ``RuntimeError`` naming it and the tables committed before it; before 4.6.0 (R276) the
        failure was ignored and the table counted as restored.
        """
        _ledger_check(self, "restore_snapshot")
        from iris_vector_graph._write_txn import refuse_in_caller_transaction

        # 4.7.0: it committed the caller's work, table by table
        refuse_in_caller_transaction(
            self.conn, "restore_snapshot()",
            "a restore erases the graph (without merge), may create tables, and commits table by table",
        )
        import json as _json
        import zipfile as _zipfile

        with _zipfile.ZipFile(path, "r") as zf:
            names = zf.namelist()
            metadata = _json.loads(zf.read("metadata.json"))

            sql_files = {n: zf.read(n).decode("utf-8") for n in names if n.startswith("sql/")}
            global_files = {n: zf.read(n) for n in names if n.startswith("globals/")}

        # A snapshot from another layout has its ^KG subscripts in different
        # positions, so importing it writes timestamps where graph keys belong and
        # every scoped read afterwards finds nothing. Refuse rather than succeed
        # emptily.
        #
        # An archive with no stamp predates the stamp, which means it predates
        # spec-223 and carries no temporal globals at all — there is nothing in it
        # for the layout to be wrong about, so it restores. Refusing those would
        # break every existing archive to guard against a mismatch that cannot bite.
        declared_layout = metadata.get("layout")
        if declared_layout is not None and declared_layout != SNAPSHOT_LAYOUT:
            raise ValueError(
                f"snapshot was taken from the {declared_layout!r} storage layout and "
                f"cannot be restored into a {SNAPSHOT_LAYOUT!r} database: its ^KG "
                "subscripts mean different things. Restore it into a database of its "
                "own layout and migrate there."
            )

        restored_tables: Dict[str, int] = {}
        failed_rows: Dict[str, int] = {}
        restored_globals: List[str] = []
        cursor = self.conn.cursor()

        TABLE_ORDER = RESTORE_TABLE_ORDER
        VECTOR_FILE = "Graph_KG_kg_NodeEmbeddings.ndjson"

        if not merge:
            # A non-merge restore replaces the database, so it needs exactly what
            # erase_all provides. It used to keep its own list: six tables named
            # here, plus whichever globals the snapshot's own metadata happened to
            # name. A snapshot written without global metadata therefore left the
            # previous database's ^KG and ^NKG in place, and the restored rows
            # inherited adjacency for content that was never restored — a fourth
            # spelling of the same drift, and the reason the inventory has one
            # owner now (ADR-0004).
            #
            # It also cleared table by table with a commit after each, so a failure
            # halfway left the database in neither state. erase_all is one
            # transaction.
            self.erase_all()

        def _insert_row(table: str, row: Dict[str, Any]) -> bool:
            if not row:
                return False
            try:
                cols = _column_names(row)
                vals = list(row.values())
                placeholders = ", ".join(["?"] * len(cols))
                col_list = ", ".join(cols)
                cursor.execute(
                    f"INSERT INTO {table} ({col_list}) SELECT {placeholders} "
                    f"WHERE NOT EXISTS (SELECT 1 FROM {table} WHERE "
                    + " AND ".join(
                        f"{c} = ?" if row[c] is not None else f"{c} IS NULL" for c in cols[:1]
                    )
                    + ")",
                    vals + [vals[0]],
                )
                return True
            except Exception:
                return False

        def _register_endpoints(graph_id: str, *node_ids) -> None:
            """Make sure an edge's endpoints exist in the edge's own graph.

            Spec 227 put `fk_edges_dest` on `(graph_id, o_id)` and `fk_edges_src` on
            `(graph_id, s)`, and IRIS checks both on every insert. An archive written
            before 4.0.0 carries a graph on the *edge* and nothing on the node, so its
            nodes all land in the default graph and every named-graph edge fails
            `SQLCODE -121` — silently, because one unrestorable row is only counted.
            A v2.16 archive with one `acme` edge restored a database with none.

            Registering the endpoint is a recovery, not an invention: the archive says
            the edge was in that graph, which means its endpoints were too — the old
            layout simply had nowhere to write that down.
            """
            for nid in node_ids:
                if not nid:
                    continue
                try:
                    cursor.execute(
                        f"INSERT INTO {self._t('nodes')} (graph_id, node_id) "
                        "SELECT ?, ? WHERE NOT EXISTS (SELECT 1 FROM "
                        f"{self._t('nodes')} WHERE COALESCE(graph_id, '') = "
                        "COALESCE(?, '') AND node_id = ?)",
                        [graph_id, nid, graph_id, nid],
                    )
                except Exception as e:
                    logger.debug(
                        "restore: could not register %s in graph %r: %s", nid, graph_id, e
                    )

        def _commit(what: str) -> None:
            """Commit ``what``'s statements, or raise naming it. A failed commit was ``except Exception: pass``, so
            the table's rows were counted as restored and were not there (4.6.0 R276)."""
            try:
                self.conn.commit()
            except Exception as e:
                raise RuntimeError(
                    f"restore_snapshot: could not commit {what}: {e}. The tables committed before it are restored "
                    f"({sorted(restored_tables)}); {what} and everything after it are not"
                    + ("" if merge else ", and erase_all() had emptied the database first")
                    + "."
                ) from e

        if any(
            f"sql/{t.replace('Graph_KG.', 'Graph_KG_')}.ndjson" in sql_files for t in LAZY_FHIR_TABLES
        ):
            from iris_vector_graph.schema import GraphSchema
            from iris_vector_graph.utils import _split_sql_statements

            for stmt in _split_sql_statements(GraphSchema.get_fhir_graph_schema_sql()):
                if not stmt.strip():
                    continue
                try:
                    cursor.execute(stmt)
                except Exception as e:
                    if "already" not in str(e).lower():
                        logger.warning("restore: FHIR table DDL failed: %s", e)
            _commit("the FHIR tables' DDL")

        for fname_short in TABLE_ORDER:
            fname = f"sql/{fname_short}"
            if fname not in sql_files:
                continue
            table_name = fname_short.replace("Graph_KG_", "Graph_KG.").replace(".ndjson", "")
            count = 0
            failed = 0
            for line in sql_files[fname].splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = _json.loads(line)
                    # Strip RowID/identity columns that cannot be explicitly inserted
                    if table_name == "Graph_KG.rdf_edges":
                        row = {k: v for k, v in row.items() if k.lower() != "id"}
                    # An archive written before the graph key was tightened spells the
                    # default graph NULL; ADR-0003 spells it ''. They mean the same
                    # graph, and the live column is Required — so a NULL here is a
                    # spelling to translate, not a row to drop. v2.16 wrote exactly
                    # this, and the row loss it used to cause was silent.
                    if "graph_id" in row and row["graph_id"] is None:
                        row["graph_id"] = ""
                    if table_name == "Graph_KG.rdf_edges":
                        _register_endpoints(
                            row.get("graph_id") or "", row.get("s"), row.get("o_id")
                        )
                    cols = _column_names(row)
                    vals = list(row.values())
                    placeholders = ", ".join(["?"] * len(cols))
                    col_list = ", ".join(cols)
                    keys = CONFIGURATION_TABLES.get(table_name)
                    if keys and not merge:
                        cursor.execute(
                            f"INSERT OR UPDATE INTO {table_name} ({col_list}) VALUES ({placeholders})", vals
                        )
                    elif keys:
                        cursor.execute(
                            f"INSERT INTO {table_name} ({col_list}) SELECT {placeholders} WHERE NOT EXISTS "
                            f"(SELECT 1 FROM {table_name} WHERE {' AND '.join(f'{k} = ?' for k in keys)})",
                            vals + [row[k] for k in keys],
                        )
                    elif merge and table_name in FHIR_TABLES:
                        keyed = [c for c in cols if row[c] is not None]
                        guard = " AND ".join(
                            [f"{c} = ?" for c in keyed]
                            + [f"{c} IS NULL" for c in cols if row[c] is None]
                        )
                        cursor.execute(
                            f"INSERT INTO {table_name} ({col_list}) SELECT {placeholders} "
                            f"WHERE NOT EXISTS (SELECT 1 FROM {table_name} WHERE {guard})",
                            vals + [row[c] for c in keyed],
                        )
                    elif merge:
                        cursor.execute(
                            f"INSERT INTO {table_name} ({col_list}) SELECT {placeholders} "
                            f"WHERE NOT EXISTS (SELECT 1 FROM {table_name} WHERE {cols[0]} = ?)",
                            vals + [vals[0]],
                        )
                    else:
                        cursor.execute(
                            f"INSERT INTO {table_name} ({col_list}) VALUES ({placeholders})",
                            vals,
                        )
                    count += 1
                except Exception as e:
                    # Keep going: one bad row should not abandon an upgrade. But it
                    # is reported, because `restored_tables` alone cannot tell a
                    # dropped row from an archive that never held it.
                    failed += 1
                    logger.debug("restore: row insert failed for %s: %s", table_name, e)
            _commit(table_name)
            restored_tables[table_name] = count
            if failed:
                failed_rows[table_name] = failed
                logger.warning(
                    "restore: %d of %d row(s) for %s did not land; see failed_rows in "
                    "the result",
                    failed,
                    failed + count,
                    table_name,
                )

        if f"sql/{VECTOR_FILE}" in sql_files:
            count = 0
            for line in sql_files[f"sql/{VECTOR_FILE}"].splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = _json.loads(line)
                    # Both keys are accepted: `node_id` is the 4.0.0 export, `id` is
                    # what a 3.x archive holds. A pre-4.0.0 archive carries no graph,
                    # so its rows land in the default graph — the only graph that
                    # existed when it was written.
                    nid = row.get("node_id") or row.get("id")
                    gid = row.get("graph_id") or ""
                    emb_str = row.get("emb")
                    meta_val = row.get("metadata")
                    if nid and emb_str:
                        try:
                            # `save_snapshot` exports the metadata column, so the
                            # restore writes it. It used to read it into a local and
                            # insert (id, emb) only, which lost every embedding's
                            # provenance without a symptom — the vectors came back, so
                            # nothing looked wrong.
                            emb_text = vector_text(emb_str)
                            if merge:
                                cursor.execute(
                                    "INSERT INTO Graph_KG.kg_NodeEmbeddings"
                                    " (graph_id, node_id, emb, metadata) "
                                    f"SELECT ?, ?, TO_VECTOR('{emb_text}', {self.vector_dtype}), ? "
                                    "WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.kg_NodeEmbeddings"
                                    " WHERE COALESCE(graph_id, '') = COALESCE(?, '')"
                                    " AND node_id = ?)",
                                    [gid, nid, meta_val, gid, nid],
                                )
                            else:
                                cursor.execute(
                                    "INSERT INTO Graph_KG.kg_NodeEmbeddings"
                                    " (graph_id, node_id, emb, metadata) "
                                    f"VALUES (?, ?, TO_VECTOR('{emb_text}', {self.vector_dtype}), ?)",
                                    [gid, nid, meta_val],
                                )
                            count += 1
                        except Exception as e:
                            failed_rows["Graph_KG.kg_NodeEmbeddings"] = (
                                failed_rows.get("Graph_KG.kg_NodeEmbeddings", 0) + 1
                            )
                            logger.debug("restore: embedding insert failed for %s: %s", nid, e)
                except Exception as e:
                    failed_rows["Graph_KG.kg_NodeEmbeddings"] = (
                        failed_rows.get("Graph_KG.kg_NodeEmbeddings", 0) + 1
                    )
                    logger.debug("restore: embedding row unreadable: %s", e)
            _commit("Graph_KG.kg_NodeEmbeddings")
            restored_tables["Graph_KG.kg_NodeEmbeddings"] = count

        # --- spec 227's routed tables -------------------------------------------
        #
        # These are physical tables that did not exist before the archive named them,
        # so the restore creates each one at the width the archive recorded and then
        # loads it. The width has to come from the archive: a routed column's
        # declaration is what makes its first insert legal (SQLCODE -104 otherwise),
        # and this engine's own `embedding_dimension` says nothing about a graph
        # somebody else configured.
        #
        # The registry rows have already landed above, which is what makes the routes
        # *claimed* again — without them the tables would stand unreferenced and every
        # `resolve_route` would report a miss over live vectors.
        from iris_vector_graph.security import is_routed_embedding_table

        archived_routes = dict(metadata.get(ROUTED_TABLES_KEY) or {})
        routed_files = {
            name[len("sql/") : -len(".ndjson")].replace("Graph_KG_", "Graph_KG.")
            for name in sql_files
            if name.startswith("sql/Graph_KG_kg_emb_") and name.endswith(".ndjson")
        }
        # A routed file with no metadata entry is an archive whose section was lost or
        # predates it. Its width is still recoverable from the registry row that came
        # back a moment ago, so it is rebuilt rather than dropped.
        for orphan in sorted(routed_files - set(archived_routes)):
            archived_routes.setdefault(orphan, {})

        for routed_table in sorted(archived_routes):
            route = archived_routes[routed_table] or {}
            short = routed_table.split(".", 1)[-1]
            if not is_routed_embedding_table(short):
                logger.warning(
                    "restore: %s is not a routed embedding table name; skipped", routed_table
                )
                continue
            # the archive's spelling names the file; the statement names the table by its shape, in this IVG Schema,
            # so a prefix the archive put before the dot never reaches the SQL (4.6.0)
            target = self._t(short)
            graph_id = route.get("graph_id") or ""
            width = route.get("dimension")
            dtype = route.get("dtype") or "DOUBLE"
            if not width:
                # Fall back to the restored registry row — the same declaration the
                # export read, just reached from the other side.
                try:
                    cursor.execute(
                        f"SELECT graph_id, dimension, dtype FROM {self._registry_table()} "
                        "WHERE table_name = ?",
                        [short],
                    )
                    row = cursor.fetchone()
                except Exception:
                    row = None
                if row:
                    graph_id = row[0] or graph_id
                    width = row[1]
                    dtype = row[2] or dtype
            try:
                width = int(width)
            except (TypeError, ValueError):
                width = 0
            if width <= 0:
                failed_rows[routed_table] = len(
                    [
                        ln
                        for ln in sql_files.get(
                            f"sql/{routed_table.replace('Graph_KG.', 'Graph_KG_')}.ndjson", ""
                        ).splitlines()
                        if ln.strip()
                    ]
                )
                logger.warning(
                    "restore: %s has no declared width in the archive or the restored "
                    "registry, so it cannot be recreated; its vectors are reported in "
                    "failed_rows",
                    routed_table,
                )
                continue

            try:
                self._create_routed_table(cursor, short, dimension=width, dtype=dtype)
            except Exception as e:
                failed_rows[routed_table] = failed_rows.get(routed_table, 0) + 1
                logger.warning("restore: could not create %s: %s", routed_table, e)
                continue

            fname = f"sql/{routed_table.replace('Graph_KG.', 'Graph_KG_')}.ndjson"
            count = 0
            for line in sql_files.get(fname, "").splitlines():
                line = line.strip()
                if not line:
                    continue
                nid = None
                try:
                    row = _json.loads(line)
                    nid = row.get("node_id") or row.get("id")
                    gid = row.get("graph_id") or ""
                    emb_str = row.get("emb")
                    meta_val = row.get("metadata")
                    if not (nid and emb_str):
                        continue
                    emb_text = vector_text(emb_str)
                    if merge:
                        cursor.execute(
                            f"INSERT INTO {target} (graph_id, node_id, emb, metadata) "
                            f"SELECT ?, ?, TO_VECTOR('{emb_text}', {dtype}), ? "
                            f"WHERE NOT EXISTS (SELECT 1 FROM {target} "
                            "WHERE COALESCE(graph_id, '') = COALESCE(?, '') AND node_id = ?)",
                            [gid, nid, meta_val, gid, nid],
                        )
                    else:
                        cursor.execute(
                            f"INSERT INTO {target} (graph_id, node_id, emb, metadata) "
                            f"VALUES (?, ?, TO_VECTOR('{emb_text}', {dtype}), ?)",
                            [gid, nid, meta_val],
                        )
                    count += 1
                except Exception as e:
                    failed_rows[routed_table] = failed_rows.get(routed_table, 0) + 1
                    logger.debug("restore: routed embedding insert failed for %s: %s", nid, e)
            _commit(routed_table)
            restored_tables[routed_table] = count

            # The archived `index_state` is a statement about the database the archive
            # came from, and this is a table created seconds ago with no index on it.
            # Recording what the archive said would reproduce exactly the claim spec
            # 226 removed — an HNSW row for an index that is not there. So the build is
            # re-attempted here and whatever this database answers is what gets written.
            state, err = self._attempt_route_index(cursor, short)
            self._record_route_index_state(cursor, short, graph_id, state, err)

        # Routes appeared, their tables were created and their index states rewritten;
        # anything this engine cached about them is now a claim about a table from
        # before the restore.
        try:
            self.invalidate_route_cache()
        except Exception as e:
            logger.debug("restore: route cache not invalidated: %s", e)

        EDGE_VECTOR_FILE = "Graph_KG_kg_EdgeEmbeddings.ndjson"
        if f"sql/{EDGE_VECTOR_FILE}" in sql_files:
            count = 0
            for line in sql_files[f"sql/{EDGE_VECTOR_FILE}"].splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = _json.loads(line)
                    s_val = row.get("s")
                    p_val = row.get("p")
                    o_val = row.get("o_id")
                    emb_str = row.get("emb")
                    if s_val and p_val and o_val and emb_str:
                        try:
                            emb_text = vector_text(emb_str)
                            if merge:
                                cursor.execute(
                                    "INSERT INTO Graph_KG.kg_EdgeEmbeddings (s, p, o_id, emb) "
                                    f"SELECT ?, ?, ?, TO_VECTOR('{emb_text}', {self.vector_dtype}) "
                                    "WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.kg_EdgeEmbeddings "
                                    "WHERE s=? AND p=? AND o_id=?)",
                                    [s_val, p_val, o_val, s_val, p_val, o_val],
                                )
                            else:
                                cursor.execute(
                                    "INSERT INTO Graph_KG.kg_EdgeEmbeddings (s, p, o_id, emb) "
                                    f"VALUES (?, ?, ?, TO_VECTOR('{emb_text}', {self.vector_dtype}))",
                                    [s_val, p_val, o_val],
                                )
                            count += 1
                        except Exception as e:
                            failed_rows["Graph_KG.kg_EdgeEmbeddings"] = (
                                failed_rows.get("Graph_KG.kg_EdgeEmbeddings", 0) + 1
                            )
                            logger.debug("restore: edge embedding insert failed: %s", e)
                except Exception as e:
                    failed_rows["Graph_KG.kg_EdgeEmbeddings"] = (
                        failed_rows.get("Graph_KG.kg_EdgeEmbeddings", 0) + 1
                    )
                    logger.debug("restore: edge embedding row unreadable: %s", e)
            _commit("Graph_KG.kg_EdgeEmbeddings")
            restored_tables["Graph_KG.kg_EdgeEmbeddings"] = count

        if global_files:
            allowed_globals = restorable_globals(getattr(self, "_schema_prefix", "Graph_KG"))
            iris_obj, nkg_floor, kg_floor = None, 0, 0
            try:
                iris_obj = self._iris_obj()
                nkg_floor = int(iris_obj.get("^NKG", "$meta", "version") or 0)
                kg_floor = int(iris_obj.get("^KG", "__version") or 0)
                for gfile_path, content in global_files.items():
                    gname = (
                        gfile_path.replace("globals/", "")
                        .replace(".ndjson", "")
                        .replace(".xml", "")
                        .replace(".gof", "")
                    )
                    ndjson = content.decode("utf-8", errors="replace")
                    if gname not in allowed_globals:
                        failed_rows[f"^{gname}"] = sum(1 for ln in ndjson.splitlines() if ln.strip())
                        logger.warning("restore: the archive holds ^%s, which IVG does not archive; not written", gname)
                        continue
                    try:
                        count = self._import_global_from_ndjson(iris_obj, f"^{gname}", ndjson)
                    except Exception as e:
                        # R271: this was a warning, and the restore returned as if complete with the
                        # global (^KG, say) partly or not at all written, after erase_all had run.
                        raise RuntimeError(
                            f"restore_snapshot: could not write ^{gname}: {e}. The tables are restored "
                            f"({sorted(restored_tables)}) and the globals before it ({restored_globals}); "
                            f"^{gname} and the globals after it are not."
                        ) from e
                    if count > 0:
                        restored_globals.append(gname)
            finally:
                if iris_obj is not None:
                    _raise_nkg_version(iris_obj, nkg_floor)
                    _raise_kg_version(iris_obj, kg_floor)

        restored_layers = []
        if restored_tables:
            restored_layers.append("sql")
        if restored_globals:
            restored_layers.append("globals")

        if not restored_tables and not restored_globals and "globals" in restored_layers:
            logger.warning(
                "Globals-only restore: SQL tables are empty — rdf_edges queries will return no results"
            )

        return {
            "restored_tables": restored_tables,
            # Per-table counts of what the archive held and the database refused.
            # Empty means a complete restore; anything else is data the consumer
            # does not have, and the only place they can see it.
            "failed_rows": failed_rows,
            "restored_globals": restored_globals,
            "restored_layers": restored_layers,
            "snapshot_ts": metadata.get("created_ts", 0),
        }

    def _export_global_to_ndjson(self, iris_obj, global_name: str, prefix_subs: list) -> list:
        import json as _json

        lines = []

        # "" is the only start sentinel that collates before every subscript, and it
        # is the one $Order/nextSubscript is documented to take. Seeding with 0 asked
        # for the subscript *after* 0, so the one key the walk could never emit was 0
        # itself — which is the default graph (ADR-0003) at the graph level, and the
        # epoch at the timestamp level. No default-graph content has ever reached a
        # snapshot.
        START = ""
        on_server = global_name in VECTOR_VALUE_GLOBALS

        def _recurse(subs: list):
            cur = subs[-1] if subs else START
            while True:
                nxt = (
                    iris_obj.nextSubscript(False, global_name, *subs[:-1], cur)
                    if subs
                    else iris_obj.nextSubscript(False, global_name, cur)
                )
                if not nxt:
                    break
                new_subs = (subs[:-1] if subs else []) + [nxt]
                if on_server:
                    value = iris_obj.classMethodValue(
                        "Graph.KG.Snapshot", "NodeValueJSON", global_name, _json.dumps(new_subs)
                    )
                    if value:
                        lines.append(_json.dumps({"k": new_subs, **_json.loads(value)}))
                else:
                    val = iris_obj.get(global_name, *new_subs)
                    if val is not None:
                        lines.append(_json.dumps({"k": new_subs, "v": str(val)}))
                _recurse(new_subs + [START])
                cur = nxt

        seed = prefix_subs + [START] if prefix_subs else [START]
        _recurse(seed)
        return lines

    def _import_global_from_ndjson(self, iris_obj, global_name: str, ndjson: str) -> int:
        import json as _json

        is_kg = global_name == "^KG"
        skipped = set(LEGACY_KG_COUNTERS) | set(DERIVED_KG_CACHE)
        if is_kg and self._adjacency_indexed:
            # spec 252 FR-016: the restored rows rebuilt these through the index; an archived entry with no row
            # behind it would come back as drift
            skipped |= set(INDEX_DERIVED_KG)
        adjacency_graphs: list = []
        count = 0
        for line in ndjson.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = _json.loads(line)
                subs = entry.pop("k")
                if is_kg and subs and subs[0] in skipped:
                    continue
                if "vec" in entry or "list" in entry:
                    iris_obj.classMethodVoid(
                        "Graph.KG.Snapshot", "SetNodeJSON", global_name, _json.dumps(subs), _json.dumps(entry)
                    )
                else:
                    iris_obj.set(entry["v"], global_name, *subs)
                if is_kg and len(subs) > 1 and subs[0] in ("out", "in") and subs[1] not in adjacency_graphs:
                    adjacency_graphs.append(subs[1])
                count += 1
            except Exception as e:
                logger.debug("global import line failed: %s", e)
        # The lines above set `out`/`in` without bumping a generation, so a cache entry stamped with the generation
        # that was current before the restore would still be "valid" for the rows just written. Clear the cache first,
        # then the generation, for every graph written, and only after the last write.
        for graph in adjacency_graphs:
            iris_obj.kill(global_name, "degw", graph)
            iris_obj.kill(global_name, "degc", graph)
            iris_obj.kill(global_name, "gen", graph)
        return count

    def load_obo(
        self,
        path_or_url: str,
        prefix: str = None,
        encoding: str = "utf-8",
        encoding_errors: str = "replace",
        progress_callback=None,
    ) -> dict:
        """Read an OBO ontology (a file path or an http(s) URL) with ``obonet`` and load it with
        ``load_networkx``, each term's ``namespace`` as its label.

        ``prefix`` renames every term ID to ``prefix:ID``. Writes to the default graph and returns
        ``load_networkx``'s counts. Needs ``obonet``.
        """
        try:
            import obonet
        except ImportError:
            raise ImportError("load_obo requires obonet: pip install obonet")
        import io

        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            G = obonet.read_obo(path_or_url)
        else:
            with open(path_or_url, "rb") as raw:
                content = raw.read().decode(encoding, errors=encoding_errors)
            G = obonet.read_obo(io.StringIO(content))
        if prefix:
            import networkx as nx

            mapping = {n: f"{prefix}:{n}" for n in G.nodes()}
            G = nx.relabel_nodes(G, mapping)
        return self.load_networkx(G, label_attr="namespace", progress_callback=progress_callback)

    def import_graph_ndjson(
        self,
        path: str,
        upsert_nodes: bool = True,
        batch_size: int = 10000,
        graph: str = None,
    ) -> dict:
        """Write the nodes, edges and temporal edges an NDJSON file describes, one JSON object a line.

        Lines are ``{"kind": "node"|"edge"|"temporal_edge", ...}``, or the ledger's reconstruction export
        form (``{"type": "node"|"rel", ...}``). Nodes and edges go into ``graph``, else an edge line's own
        ``graph``; temporal edges are written in batches of ``batch_size`` to the default graph, and with
        ``upsert_nodes`` their endpoints are created there first. An edge line names its endpoints ``source``
        and ``target`` (or ``source_id`` and ``target_id``). Malformed lines, lines of an unknown kind, and node or
        edge lines missing their id, source, predicate or target are logged with their line number and skipped.
        Returns ``nodes``, ``edges`` and ``temporal_edges``, the lines processed (not rows created), and
        ``skipped``, the lines skipped.
        """
        _ledger_check(self, "import_graph_ndjson")
        nodes = 0
        edges = 0
        temporal_edges = 0
        skipped = 0
        temporal_batch = []

        with open(path, "r") as f:
            for lineno, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("import_graph_ndjson: skipping malformed NDJSON line %d", lineno)
                    skipped += 1
                    continue
                if not isinstance(event, dict):
                    logger.warning("import_graph_ndjson: skipping line %d, not a JSON object", lineno)
                    skipped += 1
                    continue

                kind = event.get("kind", "")
                # spec 213: reconstruction exports use {"type": "node"|"rel", ...}
                etype = event.get("type", "")
                if not kind and etype == "node":
                    kind = "node"
                    event = {
                        "kind": "node",
                        "id": event.get("id", ""),
                        "labels": event.get("labels", []),
                        "properties": event.get("props", event.get("properties", {})),
                    }
                elif not kind and etype == "rel":
                    kind = "edge"
                    event = {
                        "kind": "edge",
                        "source": event.get("s", ""),
                        "predicate": event.get("p", ""),
                        "target": event.get("o", ""),
                        "graph": event.get("graph"),
                        "qualifiers": event.get("quals") or None,
                    }

                if kind == "node":
                    node_id = event.get("id", "")
                    labels = event.get("labels", [])
                    props = event.get("properties", {})
                    if node_id:
                        # spec 214 FR-015: pass graph to scope import into a named graph
                        self.create_node(node_id, labels=labels, properties=props, graph=graph)
                        nodes += 1
                    else:
                        logger.warning("import_graph_ndjson: skipping line %d, a node with no 'id'", lineno)
                        skipped += 1

                elif kind == "edge":
                    src = event.get("source") or event.get("source_id", "")
                    pred = event.get("predicate", "")
                    tgt = event.get("target") or event.get("target_id", "")
                    if not (src and pred and tgt):
                        # R213: this was skipped without a word, so a file keyed source_id/target_id loaded 0 edges
                        logger.warning(
                            "import_graph_ndjson: skipping line %d, an edge without source, predicate and target", lineno
                        )
                        skipped += 1
                    else:
                        event_graph = event.get("graph")
                        quals = event.get("qualifiers")
                        # spec 214 FR-015: if graph= param supplied, it overrides per-line graph
                        effective_graph = graph if graph is not None else event_graph
                        if effective_graph is not None or quals:
                            self.create_edge(
                                src, pred, tgt, qualifiers=quals or None, graph=effective_graph
                            )
                        else:
                            self.create_edge(src, pred, tgt)
                        edges += 1

                elif kind == "temporal_edge":
                    src = event.get("source", "")
                    pred = event.get("predicate", "")
                    tgt = event.get("target", "")
                    ts = event.get("timestamp", 0)
                    w = event.get("weight", 1.0)
                    attrs = event.get("attrs", {})
                    src_labels = event.get("source_labels", [])
                    tgt_labels = event.get("target_labels", [])
                    if upsert_nodes:
                        if src:
                            self.create_node(src, labels=src_labels)
                        if tgt:
                            self.create_node(tgt, labels=tgt_labels)
                    item = {"s": src, "p": pred, "o": tgt, "ts": ts, "w": w}
                    if attrs:
                        item["attrs"] = {k: str(v) for k, v in attrs.items()}
                    temporal_batch.append(item)
                    if len(temporal_batch) >= batch_size:
                        self.bulk_create_edges_temporal(temporal_batch)
                        temporal_edges += len(temporal_batch)
                        temporal_batch = []
                else:
                    logger.warning("import_graph_ndjson: skipping line %d, unknown NDJSON kind %r", lineno, kind)
                    skipped += 1

        if temporal_batch:
            self.bulk_create_edges_temporal(temporal_batch)
            temporal_edges += len(temporal_batch)

        return {"nodes": nodes, "edges": edges, "temporal_edges": temporal_edges, "skipped": skipped}

    def export_graph_ndjson(self, path: str) -> dict:
        """Write the default graph to ``path`` as NDJSON: a ``{"kind": "node", ...}`` line per node, with its
        labels and properties, then a ``{"kind": "edge", ...}`` line per edge, with its ``qualifiers``.

        ``import_graph_ndjson`` reads the file back into the same graph. Named graphs are not written, nor are
        temporal edges (``export_temporal_edges_ndjson`` writes those). Returns ``{"nodes": n, "edges": m}``,
        the lines written.
        """
        from iris_vector_graph.constants import TEMPORAL_MARKED_ROW

        nodes_written = 0
        edges_written = 0

        cursor = self.conn.cursor()
        try:
            with open(path, "w") as f:
                cursor.execute(
                    f"SELECT node_id FROM {self._t('nodes')} WHERE graph_id = '' ORDER BY node_id"
                )
                for (node_id,) in cursor.fetchall():
                    node_data = self.get_node(node_id)
                    if node_data:
                        event = {
                            "kind": "node",
                            "id": node_id,
                            "labels": node_data.get("labels", []),
                            "properties": {
                                k: v for k, v in node_data.items() if k not in ("id", "labels")
                            },
                        }
                        f.write(json.dumps(event) + "\n")
                        nodes_written += 1

                # R088: the edges. A temporal edge's marked row (ADR-0009) belongs to the temporal export.
                cursor.execute(
                    f"SELECT s, p, o_id, qualifiers FROM {self._t('rdf_edges')} WHERE graph_id = '' "
                    f"AND (qualifiers IS NULL OR NOT ({TEMPORAL_MARKED_ROW})) ORDER BY edge_id"
                )
                for s, p, o, quals in cursor.fetchall():
                    event = {"kind": "edge", "source": s, "predicate": p, "target": o}
                    quals = json.loads(quals) if quals else None
                    if quals:
                        event["qualifiers"] = quals
                    f.write(json.dumps(event) + "\n")
                    edges_written += 1
        finally:
            cursor.close()
        return {"nodes": nodes_written, "edges": edges_written}
