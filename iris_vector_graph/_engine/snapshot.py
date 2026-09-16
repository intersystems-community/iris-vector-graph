import json
import logging
from typing import Any, Dict, List, Optional

from iris_vector_graph._engine.ledger import ledger_check as _ledger_check

logger = logging.getLogger(__name__)


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
    "Graph_KG.kg_NodeEmbeddings_optimized": "derived",
    # ^KG — structural adjacency and its counters
    '^KG("out")': "global",
    '^KG("in")': "global",
    '^KG("deg")': "global",
    '^KG("degp")': "global",
    # ^KG — the temporal index, all five subtrees
    '^KG("tout")': "global",
    '^KG("tin")': "global",
    '^KG("bucket")': "global",
    '^KG("tagg")': "global",
    '^KG("edgeprop")': "global",
    # ^KG — the subtrees with no graph dimension, exported whole
    '^KG("label")': "global",
    '^KG("prop")': "global",
    '^KG("labelset")': "global",
    # Other globals
    "^NKG": "global",
    '^IVG.Ledger("tuple")': "global",
    '^IVG.Ledger("stmt")': "global",
    "^ArnoKG": "derived",
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
}


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
        for node_id, data in G.nodes(data=True):
            labels = []
            if label_attr and label_attr in data:
                val = data[label_attr]
                labels = [val] if isinstance(val, str) else list(val)
            elif "namespace" in data:
                labels = [data["namespace"]]
            props = {}
            for k, v in data.items():
                if k in (label_attr, "namespace") or v is None:
                    continue
                s = str(v) if not isinstance(v, str) else v
                if len(s) > 60000:
                    s = s[:60000]
                props[k] = s
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
        for src, dst, data in G.edges(data=True):
            predicate = data.get("predicate", data.get("label", data.get("key", "is_a")))
            qualifiers = {k: v for k, v in data.items() if k not in ("predicate", "label", "key")}
            if self.create_edge(
                source_id=str(src),
                predicate=str(predicate),
                target_id=str(dst),
                qualifiers=qualifiers or None,
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

        def _ensure_node(nid):
            try:
                cursor.execute(
                    f"INSERT INTO {self._t('nodes')} (node_id) SELECT ? WHERE NOT EXISTS (SELECT 1 FROM {self._t('nodes')} WHERE node_id = ?)",
                    [nid, nid],
                )
                return True
            except Exception:
                return False

        batch_nodes: set = set()
        batch_edges: List = []
        batch_props: List = []

        def _flush():
            nonlocal nodes_inserted, edges_inserted, props_inserted
            for nid in batch_nodes:
                if _ensure_node(nid):
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
                except Exception:
                    pass
            for s, k, v in batch_props:
                try:
                    cursor.execute(
                        f'INSERT INTO {self._t("rdf_props")} (s, "key", val) VALUES (?, ?, ?)',
                        [s, k, v[:64000]],
                    )
                    props_inserted += 1
                except Exception:
                    pass
            try:
                self.conn.commit()
            except Exception:
                pass
            batch_nodes.clear()
            batch_edges.clear()
            batch_props.clear()

        triples_iter = g.quads() if is_quads else ((s, p, o, None) for s, p, o in g)

        for s, p, o, graph_ctx in triples_iter:
            triple_count += 1
            s_id = _node_id(s)
            p_str = _node_id(p)
            batch_nodes.add(s_id)

            effective_graph = graph
            if graph_ctx is not None:
                ctx_str = str(graph_ctx)
                if ctx_str and ctx_str not in ("", "DEFAULT", "urn:x-rdflib:default"):
                    effective_graph = ctx_str

            if isinstance(o, RDFLiteral):
                key = p_str.rsplit("/", 1)[-1].rsplit("#", 1)[-1][:128]
                val = str(o)
                lang = getattr(o, "language", None)
                if lang:
                    batch_props.append((s_id, key, val))
                    batch_props.append((s_id, f"{key}_lang", lang))
                else:
                    batch_props.append((s_id, key, val))
            elif isinstance(o, (URIRef, BNode)):
                o_id = _node_id(o)
                batch_nodes.add(o_id)
                batch_edges.append((s_id, p_str, o_id, effective_graph))
            else:
                batch_props.append((s_id, p_str[:128], str(o)[:64000]))

            if triple_count % batch_size == 0:
                _flush()
                if progress:
                    progress(triple_count, 0)

        _flush()

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

    def save_snapshot(
        self,
        path: str,
        layers: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
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
        }

        sql_data: Dict[str, str] = {}
        globals_data: Dict[str, bytes] = {}

        SQL_TABLES_EXPORT = [
            ("Graph_KG.nodes", "node_id"),
            ("Graph_KG.rdf_edges", "s"),
            ("Graph_KG.rdf_labels", "s"),
            ("Graph_KG.rdf_props", "s"),
            ("Graph_KG.rdf_reifications", "subject_s"),
        ]
        VECTOR_TABLE = "Graph_KG.kg_NodeEmbeddings"

        if "sql" in layers:
            cursor = self.conn.cursor()
            for table, _ in SQL_TABLES_EXPORT:
                try:
                    cursor.execute(f"SELECT * FROM {table}")
                    all_desc = cursor.description
                    rows = cursor.fetchall()
                    _ROWID_NAMES = {"edge_id", "reification_id", "label_id", "prop_id"}
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
                    logger.debug("Snapshot: skipping table %s: %s", table, e)
                    metadata["tables"][table] = 0

            try:
                cursor.execute(f"SELECT id, emb, metadata FROM {VECTOR_TABLE}")
                cols = ["id", "emb", "metadata"]
                rows = cursor.fetchall()
                lines = []
                for row in rows:
                    nid, emb_val, meta_val = row[0], row[1], row[2]
                    emb_str = str(emb_val) if emb_val is not None else None
                    lines.append(_json.dumps({"id": nid, "emb": emb_str, "metadata": meta_val}))
                sql_data[VECTOR_TABLE] = "\n".join(lines)
                metadata["tables"][VECTOR_TABLE] = len(rows)
                metadata["has_vector_sql"] = True
            except Exception as e:
                logger.debug("Snapshot: kg_NodeEmbeddings not available: %s", e)
                metadata["has_vector_sql"] = False

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
                logger.debug("Snapshot: kg_EdgeEmbeddings not available: %s", e)

        if "globals" in layers:
            GLOBALS_EXPORT = [
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
                # are the default storage of the two persistent ledger classes.
                ("IVG.Ledger", [[]]),
                ("Graph.KG.LedgerRevisionD", [[]]),
                ("Graph.KG.LedgerRevisionI", [[]]),
                ("Graph.KG.LedgerStatsD", [[]]),
            ]
            try:
                iris_obj = self._iris_obj()
                for gname, subscript_prefixes in GLOBALS_EXPORT:
                    lines = []
                    for prefix_subs in subscript_prefixes:
                        try:
                            lines.extend(
                                self._export_global_to_ndjson(iris_obj, f"^{gname}", prefix_subs)
                            )
                        except Exception as eg:
                            logger.debug(
                                "Snapshot: skip global ^%s%s: %s",
                                gname,
                                prefix_subs,
                                eg,
                            )
                    if lines:
                        content = "\n".join(lines).encode("utf-8")
                        globals_data[gname] = content
                        metadata["globals"][gname] = {
                            "format": "ndjson",
                            "subscripts": [s for sl in subscript_prefixes for s in sl],
                            "size": len(content),
                        }
            except Exception as e:
                logger.warning("Snapshot: global export failed (globals layer skipped): %s", e)
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

    def restore_snapshot(
        self,
        path: str,
        merge: bool = False,
    ) -> Dict[str, Any]:
        _ledger_check(self, "restore_snapshot")
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

        TABLE_ORDER = [
            "Graph_KG_nodes.ndjson",
            "Graph_KG_rdf_edges.ndjson",
            "Graph_KG_rdf_labels.ndjson",
            "Graph_KG_rdf_props.ndjson",
            "Graph_KG_rdf_reifications.ndjson",
        ]
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
            cols = list(row.keys())
            vals = list(row.values())
            placeholders = ", ".join(["?"] * len(cols))
            col_list = ", ".join(cols)
            try:
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
                    cols = list(row.keys())
                    vals = list(row.values())
                    placeholders = ", ".join(["?"] * len(cols))
                    col_list = ", ".join(cols)
                    if merge:
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
            try:
                self.conn.commit()
            except Exception:
                pass
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
                    nid = row.get("id")
                    emb_str = row.get("emb")
                    meta_val = row.get("metadata")
                    if nid and emb_str:
                        try:
                            # `save_snapshot` exports the metadata column, so the
                            # restore writes it. It used to read it into a local and
                            # insert (id, emb) only, which lost every embedding's
                            # provenance without a symptom — the vectors came back, so
                            # nothing looked wrong.
                            if merge:
                                cursor.execute(
                                    "INSERT INTO Graph_KG.kg_NodeEmbeddings (id, emb, metadata) "
                                    f"SELECT ?, TO_VECTOR('{emb_str}', {self.vector_dtype}), ? "
                                    "WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.kg_NodeEmbeddings WHERE id = ?)",
                                    [nid, meta_val, nid],
                                )
                            else:
                                cursor.execute(
                                    "INSERT INTO Graph_KG.kg_NodeEmbeddings (id, emb, metadata) "
                                    f"VALUES (?, TO_VECTOR('{emb_str}', {self.vector_dtype}), ?)",
                                    [nid, meta_val],
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
            try:
                self.conn.commit()
            except Exception:
                pass
            restored_tables["Graph_KG.kg_NodeEmbeddings"] = count

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
                            if merge:
                                cursor.execute(
                                    "INSERT INTO Graph_KG.kg_EdgeEmbeddings (s, p, o_id, emb) "
                                    f"SELECT ?, ?, ?, TO_VECTOR('{emb_str}', {self.vector_dtype}) "
                                    "WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.kg_EdgeEmbeddings "
                                    "WHERE s=? AND p=? AND o_id=?)",
                                    [s_val, p_val, o_val, s_val, p_val, o_val],
                                )
                            else:
                                cursor.execute(
                                    "INSERT INTO Graph_KG.kg_EdgeEmbeddings (s, p, o_id, emb) "
                                    f"VALUES (?, ?, ?, TO_VECTOR('{emb_str}', {self.vector_dtype}))",
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
            try:
                self.conn.commit()
            except Exception:
                pass
            restored_tables["Graph_KG.kg_EdgeEmbeddings"] = count

        if global_files:
            try:
                iris_obj = self._iris_obj()
                for gfile_path, content in global_files.items():
                    gname = (
                        gfile_path.replace("globals/", "")
                        .replace(".ndjson", "")
                        .replace(".xml", "")
                        .replace(".gof", "")
                    )
                    ndjson = content.decode("utf-8", errors="replace")
                    count = self._import_global_from_ndjson(iris_obj, f"^{gname}", ndjson)
                    if count > 0:
                        restored_globals.append(gname)
            except Exception as e:
                logger.warning("restore: global import failed: %s", e)

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

        count = 0
        for line in ndjson.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = _json.loads(line)
                subs = entry["k"]
                val = entry["v"]
                iris_obj.set(val, global_name, *subs)
                count += 1
            except Exception as e:
                logger.debug("global import line failed: %s", e)
        return count

    def load_obo(
        self,
        path_or_url: str,
        prefix: str = None,
        encoding: str = "utf-8",
        encoding_errors: str = "replace",
        progress_callback=None,
    ) -> dict:
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
        _ledger_check(self, "import_graph_ndjson")
        nodes = 0
        edges = 0
        temporal_edges = 0
        temporal_batch = []

        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Skipping malformed NDJSON line")
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

                elif kind == "edge":
                    src = event.get("source", "")
                    pred = event.get("predicate", "")
                    tgt = event.get("target", "")
                    if src and pred and tgt:
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
                    logger.warning(f"Skipping unknown NDJSON kind: {kind}")

        if temporal_batch:
            self.bulk_create_edges_temporal(temporal_batch)
            temporal_edges += len(temporal_batch)

        return {"nodes": nodes, "edges": edges, "temporal_edges": temporal_edges}

    def export_graph_ndjson(self, path: str) -> dict:
        nodes_written = 0
        edges_written = 0

        cursor = self.conn.cursor()
        with open(path, "w") as f:
            cursor.execute(f"SELECT node_id FROM {self._t('nodes')}")
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

        cursor.close()
        return {"nodes": nodes_written, "edges": edges_written}
