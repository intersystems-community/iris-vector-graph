"""
High-performance bulk loader for IVG graphs.

Bypasses per-row overhead (functional index, FK checks, subquery dedup)
to achieve 5,000-60,000 rows/s depending on configuration.

Architecture:
  1. Phase 1 — Nodes:   INSERT %NOINDEX %NOCHECK into nodes table
  2. Phase 2 — Labels:  INSERT %NOINDEX %NOCHECK into rdf_labels
  3. Phase 3 — Props:   INSERT %NOINDEX %NOCHECK into rdf_props
  4. Phase 4 — Edges:   INSERT %NOINDEX %NOCHECK into rdf_edges
  5. Phase 5 — Rebuild: %BuildIndices on all tables
  6. Phase 6 — Globals: BuildKG() + BuildNKG() for ^KG/^NKG traversal globals

Usage:
    from iris_vector_graph.bulk_loader import BulkLoader
    loader = BulkLoader(conn)
    stats = loader.load_networkx(G, label_attr="namespace")
    print(stats)

For standalone use:
    python -m iris_vector_graph.bulk_loader /tmp/graph.pkl
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from iris_vector_graph.exceptions import BulkLoadError
from iris_vector_graph.schema import _call_classmethod

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 5000
SCHEMA = "Graph_KG"


class BulkLoader:
    """High-performance bulk graph loader for IVG."""

    def __init__(
        self,
        conn,
        schema: str = SCHEMA,
        batch_size: int = DEFAULT_BATCH_SIZE,
        graph: Optional[str] = None,
    ):
        """One loader loads one graph.

        `graph` is a property of the loader rather than of each call: a dedupe scan
        and the inserts it guards have to agree about which graph they are in, and a
        per-call parameter is an invitation for them not to. `None` and `''` both mean
        the default graph, as everywhere else in IVG, and the default graph is written
        explicitly — never left to the column default, which on a database upgraded
        rather than created is still a nullable column with no default at all
        (spec 230, FR-003).
        """
        self.conn = conn
        self.schema = schema
        self.batch_size = batch_size
        self.graph = graph or ""
        self._stats: Dict[str, Any] = {}
        self._skipped = 0

    def _table(self, name: str) -> str:
        return f"{self.schema}.{name}"

    def _executemany_batched(
        self,
        cursor,
        sql: str,
        params: List[List],
        label: str = "",
        commit_per_batch: bool = True,
    ) -> int:
        """Execute INSERT in batches with progress logging.

        Returns the number of rows inserted. A rejected row is either a duplicate,
        which is skipped and counted in `self._skipped` because re-loading the same
        data has to stay idempotent, or a real failure, which raises `BulkLoadError`.
        The old code counted both into a local `errors` variable and logged the second
        kind at ERROR level, so a wholesale `SQLCODE -121` came back as a stats dict
        saying zero rows and no exception — indistinguishable from an empty load.
        """
        total = len(params)
        inserted = 0
        skipped = 0
        t0 = time.time()

        def _is_duplicate(err: str) -> bool:
            return "-119" in err or "unique" in err.lower()

        for batch_start in range(0, total, self.batch_size):
            batch = params[batch_start : batch_start + self.batch_size]
            try:
                cursor.executemany(sql, batch)
                if commit_per_batch:
                    self.conn.commit()
                inserted += len(batch)
            except Exception as e:
                if commit_per_batch:
                    self._rollback()
                err_str = str(e)
                if not _is_duplicate(err_str):
                    logger.error(f"{label} batch at {batch_start} failed: {e}")
                    raise BulkLoadError(label or "Batch", inserted, len(batch), err_str)
                for row in batch:
                    try:
                        cursor.execute(sql, row)
                        if commit_per_batch:
                            self.conn.commit()
                        inserted += 1
                    except Exception as row_err:
                        if commit_per_batch:
                            self._rollback()
                        row_str = str(row_err)
                        # One duplicate in the batch used to put every remaining row
                        # on this path, where each error was counted and dropped —
                        # a `-121` among them looked exactly like a re-load.
                        if not _is_duplicate(row_str):
                            logger.error(f"{label} row at {batch_start} failed: {row_err}")
                            raise BulkLoadError(label or "Batch", inserted, 1, row_str)
                        skipped += 1

            elapsed = time.time() - t0
            if inserted > 0 and (inserted % (self.batch_size * 4) == 0 or batch_start + self.batch_size >= total):
                rate = inserted / elapsed if elapsed > 0 else 0
                logger.info(
                    f"{label}: {inserted:,}/{total:,} ({rate:,.0f} rows/s, "
                    f"{skipped} duplicates skipped, {elapsed:.1f}s)"
                )

        self._skipped = skipped
        return inserted

    def _rollback(self) -> None:
        try:
            self.conn.rollback()
        except Exception:
            pass

    def _graph_predicate(self) -> str:
        """`COALESCE` on the stored side, on both scans (spec 230, FR-005).

        A row this loader wrote itself spells the default graph `''`; a row written by
        an older version of this loader, or by any SQL writer that never knew about
        `graph_id`, spells it NULL. They are the same graph, and a dedupe scan that
        sees only one of the two spellings writes a second copy of everything it
        cannot see — with `%NOCHECK`, without complaint.
        """
        return "%NOINDEX COALESCE(graph_id, '') = ?"

    def _known_nodes_sql(self) -> Tuple[str, List]:
        """Node IDs this loader can see, `%NOINDEX` rows included, in its own graph.

        A `%NOINDEX` insert leaves the row in the table and out of the index, and an
        ordinary equality read goes through the index: measured on the enterprise
        container, `WHERE node_id = 'x'` returns 0 rows for a node inserted that way
        and `WHERE %NOINDEX node_id = 'x'` returns 1. Without the hint every
        existence check here believes the previous bulk load never happened, and
        `%NOCHECK` means the second copy lands without complaint.

        Returns the statement and its parameters together, so a caller cannot run the
        scan and forget the graph it was scoped to.
        """
        return (
            f"SELECT node_id FROM {self._table('nodes')} "
            f"WHERE {self._graph_predicate()}",
            [self.graph],
        )

    def _register_endpoints(self, cursor, endpoints) -> int:
        """Make sure every edge endpoint exists as a node before the edges land.

        4.0.0's `rdf_edges` carries `fk_edges_source` and `fk_edges_dest` onto
        `nodes (graph_id, node_id)`. `create_edge` registers both endpoints; this
        loader did not, and its default `INSERT %NOINDEX %NOCHECK` skips the check,
        so a bulk load could write edges no reader can join — with `%NOCHECK` off the
        same load was rejected wholesale with `SQLCODE -121`.

        The insert is deliberately not `%NOINDEX`: an endpoint that is invisible to
        the index is an endpoint the foreign key cannot find.

        The endpoint lands in the loader's graph, because that is the graph the edge
        referencing it lands in and the foreign key names `(graph_id, node_id)`.
        """
        sql, params = self._known_nodes_sql()
        cursor.execute(sql, params)
        known = set(r[0] for r in cursor.fetchall())
        missing = sorted(e for e in endpoints if e not in known)
        if not missing:
            return 0
        logger.info(f"Registering {len(missing):,} edge endpoints not yet in nodes...")
        return self._executemany_batched(
            cursor,
            f"INSERT INTO {self._table('nodes')} (graph_id, node_id) VALUES (?, ?)",
            [[self.graph, nid] for nid in missing],
            "Edge endpoints",
        )

    def _rebuild_indices(self, cursor, class_name: str) -> bool:
        """Rebuild all indices for a class using %BuildIndices.

        `cursor` is unused and kept for callers that already hold one; the call
        goes through the native bridge because there is no SQL form of it. The
        previous `SELECT %SYSTEM_SQL.BuildIndices(...)` is not a function IRIS
        has — it answers SQLCODE -359 — so phase 5 never ran and every
        `%NOINDEX` row stayed invisible to SQL while `BuildKG`'s unpredicated
        cursor still read it into `^KG`.
        """
        try:
            status = _call_classmethod(self.conn, class_name, "%BuildIndices")
        except Exception as e:
            logger.warning(f"%BuildIndices failed for {class_name}: {e}")
            return False
        if not status:
            logger.warning(
                f"%BuildIndices returned a failure status for {class_name}: {status!r}"
            )
            return False
        return True

    def load_nodes(
        self,
        nodes: List[Tuple[str, Dict[str, Any]]],
        label_attr: str = "namespace",
        skip_existing: bool = True,
        use_noindex: bool = True,
    ) -> Dict[str, int]:
        """
        Load nodes with labels and properties.

        Args:
            nodes: List of (node_id, attrs_dict) tuples
            label_attr: Attribute name to extract as label (default: "namespace")
            skip_existing: If True, skip nodes that already exist
            use_noindex: If True, use %NOINDEX %NOCHECK for faster loading

        Returns:
            Dict with counts: nodes, labels, props, errors, elapsed_s
        """
        cursor = self.conn.cursor()
        t0 = time.time()
        hint = " %NOINDEX %NOCHECK" if use_noindex else ""
        n_nodes = 0
        n_labels = 0
        n_props = 0
        _failed = False
        try:
            logger.info(f"Phase 1: Loading {len(nodes):,} nodes (noindex={use_noindex})...")
            if skip_existing:
                known_sql, known_params = self._known_nodes_sql()
                cursor.execute(known_sql, known_params)
                existing = set(r[0] for r in cursor.fetchall())
                new_nodes = [(nid, attrs) for nid, attrs in nodes if nid not in existing]
                logger.info(f"  {len(existing):,} existing, {len(new_nodes):,} new")
            else:
                new_nodes = nodes
                existing = set()

            node_params = [[self.graph, nid] for nid, _ in new_nodes]
            node_sql = (
                f"INSERT{hint} INTO {self._table('nodes')} (graph_id, node_id) VALUES (?, ?)"
            )
            n_nodes = self._executemany_batched(cursor, node_sql, node_params, "Nodes")

            nodes_to_process = new_nodes if skip_existing else nodes

            label_params = []
            for nid, attrs in nodes_to_process:
                labels = []
                if label_attr and label_attr in attrs:
                    val = attrs[label_attr]
                    labels = [val] if isinstance(val, str) else list(val)
                for lbl in labels:
                    if lbl and isinstance(lbl, str):
                        label_params.append([self.graph, nid, lbl[:128]])

            if label_params:
                logger.info(f"Phase 2: Loading {len(label_params):,} labels (noindex={use_noindex})...")
                label_sql = (
                    f"INSERT{hint} INTO {self._table('rdf_labels')} "
                    "(graph_id, s, label) VALUES (?, ?, ?)"
                )
                n_labels = self._executemany_batched(cursor, label_sql, label_params, "Labels")
            else:
                logger.info(f"Phase 2: No new labels to load")

            prop_params = []
            for nid, attrs in nodes_to_process:
                props = {"id": nid}
                for k, v in attrs.items():
                    if k in (label_attr, "namespace") or v is None:
                        continue
                    s = json.dumps(v) if isinstance(v, (dict, list)) else str(v)
                    if len(s) > 60000:
                        s = s[:60000]
                    props[k] = s
                for k, v in props.items():
                    prop_params.append([self.graph, nid, k, str(v)])

            if prop_params:
                logger.info(f"Phase 3: Loading {len(prop_params):,} properties (noindex={use_noindex})...")
                prop_sql = (
                    f"INSERT{hint} INTO {self._table('rdf_props')} "
                    "(graph_id, s, \"key\", val) VALUES (?, ?, ?, ?)"
                )
                n_props = self._executemany_batched(cursor, prop_sql, prop_params, "Props")
            else:
                logger.info(f"Phase 3: No new properties to load")
        except Exception:
            _failed = True
            raise
        finally:
            if _failed:
                try:
                    self.conn.rollback()
                except Exception:
                    pass
            try:
                cursor.close()
            except Exception:
                pass

        elapsed = time.time() - t0
        stats = {
            "nodes": n_nodes,
            "labels": n_labels,
            "props": n_props,
            "elapsed_s": round(elapsed, 1),
        }
        logger.info(f"Node loading complete: {stats}")
        return stats

    def load_edges(
        self,
        edges: List[Tuple[str, str, str, Optional[Dict]]],
        use_noindex: bool = True,
        skip_existing: bool = True,
    ) -> Dict[str, int]:
        """
        Load edges in bulk.

        Args:
            edges: List of (source_id, predicate, target_id, qualifiers_dict) tuples.
                   qualifiers can be None.
            use_noindex: If True, use %NOINDEX %NOCHECK (faster but needs index rebuild).
                         Default True — 450x faster than plain INSERT at scale.
                         Call rebuild_all_indices() after loading.
            skip_existing: If True, filter out edges already in DB (avoids UNIQUE violations).

        Returns:
            Dict with counts: edges, errors, elapsed_s
        """
        cursor = self.conn.cursor()
        t0 = time.time()
        n_edges = 0
        n_endpoints = 0
        _failed = False
        try:
            # Deduplicate in-memory first (MultiDiGraph can have duplicates)
            seen = set()
            deduped = []
            for src, pred, tgt, quals in edges:
                key = (src, pred, tgt)
                if key not in seen:
                    seen.add(key)
                    deduped.append((src, pred, tgt, quals))
            if len(deduped) < len(edges):
                logger.info(f"Deduped {len(edges):,} -> {len(deduped):,} edges ({len(edges)-len(deduped):,} duplicates)")

            if skip_existing:
                cursor.execute(
                    f"SELECT s, p, o_id FROM {self._table('rdf_edges')} "
                    f"WHERE {self._graph_predicate()}",
                    [self.graph],
                )
                existing_edges = set((r[0], r[1], r[2]) for r in cursor.fetchall())
                new_edges = [(s, p, t, q) for s, p, t, q in deduped if (s, p, t) not in existing_edges]
                logger.info(f"  {len(existing_edges):,} existing edges, {len(new_edges):,} new")
                deduped = new_edges

            logger.info(f"Loading {len(deduped):,} edges (noindex={use_noindex})...")

            endpoints = set()
            for src, _pred, tgt, _quals in deduped:
                endpoints.add(src)
                endpoints.add(tgt)
            if endpoints:
                n_endpoints = self._register_endpoints(cursor, endpoints)

            edge_params = []
            for src, pred, tgt, quals in deduped:
                qual_json = json.dumps(quals) if quals else None
                edge_params.append([self.graph, src, pred, tgt, qual_json])

            edge_hint = " %NOINDEX %NOCHECK" if use_noindex else ""
            sql = (
                f"INSERT{edge_hint} INTO {self._table('rdf_edges')} "
                "(graph_id, s, p, o_id, qualifiers) VALUES (?, ?, ?, ?, ?)"
            )

            n_edges = self._executemany_batched(cursor, sql, edge_params, "Edges")
        except Exception:
            _failed = True
            raise
        finally:
            if _failed:
                self._rollback()
            try:
                cursor.close()
            except Exception:
                pass

        elapsed = time.time() - t0
        stats = {
            "edges": n_edges,
            "endpoints_registered": n_endpoints,
            "elapsed_s": round(elapsed, 1),
            "noindex": use_noindex,
        }
        logger.info(f"Edge loading complete: {stats}")
        return stats

    def rebuild_all_indices(self) -> Dict[str, bool]:
        """
        Rebuild all SQL indexes after %NOINDEX loading.

        Must be called after load_edges(use_noindex=True).
        Also rebuilds bitmap extent indexes for correct COUNT(*).
        """
        results = {}
        cursor = self.conn.cursor()
        try:
            for cls in ["Graph.KG.rdfedges", "Graph.KG.rdflabels", "Graph.KG.rdfprops", "Graph.KG.nodes"]:
                t0 = time.time()
                ok = self._rebuild_indices(cursor, cls)
                dt = time.time() - t0
                results[cls] = ok
                if ok:
                    logger.info(f"  Rebuilt indices for {cls} ({dt:.1f}s)")
                else:
                    logger.warning(f"  %BuildIndices failed for {cls}")
        finally:
            try:
                cursor.close()
            except Exception:
                pass

        return results

    def build_graph_globals(self) -> bool:
        """
        Build ^KG and ^NKG traversal globals from SQL tables.

        Calls Graph.KG.Traversal.BuildKG() which reads from rdf_edges/rdf_labels/rdf_props
        and populates ^KG (adjacency lists) and ^NKG (NICHE-encoded index).

        Requires Graph.KG.Traversal and Graph.KG.GraphIndex to be deployed.
        Returns True if successful.
        """
        cursor = self.conn.cursor()
        try:
            logger.info("Building ^KG + ^NKG globals from SQL tables...")
            t0 = time.time()
            cursor.execute("Do ##class(Graph.KG.Traversal).BuildKG()")
            dt = time.time() - t0
            try:
                self.conn.commit()
            except Exception:
                pass
            logger.info(f"BuildKG completed in {dt:.1f}s")
            return True
        except Exception as e:
            logger.error(f"BuildKG failed: {e}")
            logger.info(
                "Ensure Graph.KG.Traversal and Graph.KG.GraphIndex classes "
                "are deployed. BFS/PPR will not work without ^KG globals."
            )
            return False
        finally:
            try:
                cursor.close()
            except Exception:
                pass

    def load_networkx(
        self,
        G,
        label_attr: str = "namespace",
        skip_existing_nodes: bool = True,
        use_noindex: bool = True,
        build_globals: bool = True,
    ) -> Dict[str, Any]:
        """
        Load an entire NetworkX graph into IVG.

        This is the main entry point. Handles nodes, labels, properties,
        edges, index rebuilding, and graph global construction.

        Args:
            G: NetworkX Graph/DiGraph/MultiDiGraph
            label_attr: Node attribute to use as label (default: "namespace")
            skip_existing_nodes: Skip nodes already in the database
            use_noindex: Use %NOINDEX for faster loading (default True, 450x faster)
            build_globals: Build ^KG/^NKG traversal globals after loading

        Every row lands in the graph this loader was constructed with, and the
        `final_*_count` figures below count that graph rather than the namespace.

        Returns:
            Dict with comprehensive statistics
        """
        t_total = time.time()
        stats: Dict[str, Any] = {
            "input_nodes": G.number_of_nodes(),
            "input_edges": G.number_of_edges(),
        }

        node_list = [(str(nid), dict(data)) for nid, data in G.nodes(data=True)]
        node_stats = self.load_nodes(node_list, label_attr=label_attr, skip_existing=skip_existing_nodes, use_noindex=use_noindex)
        stats.update({f"loaded_{k}": v for k, v in node_stats.items()})

        edge_list = []
        for src, dst, data in G.edges(data=True):
            predicate = str(data.get("predicate", data.get("label", data.get("key", "is_a"))))
            qualifiers = {k: v for k, v in data.items() if k not in ("predicate", "label", "key")}
            edge_list.append((str(src), predicate, str(dst), qualifiers if qualifiers else None))

        edge_stats = self.load_edges(edge_list, use_noindex=use_noindex)
        stats.update({f"loaded_{k}": v for k, v in edge_stats.items()})

        if use_noindex:
            logger.info("Rebuilding SQL indexes...")
            t_idx = time.time()
            idx_results = self.rebuild_all_indices()
            stats["index_rebuild_s"] = round(time.time() - t_idx, 1)
            stats["index_rebuild"] = idx_results
            failed = sorted(cls for cls, ok in idx_results.items() if not ok)
            if failed:
                # The rows are already written and unselectable — a caller told
                # nothing would go on to query a table that cannot see them.
                raise RuntimeError(
                    "%NOINDEX load left these classes without their indices: "
                    + ", ".join(failed)
                    + ". The rows are in the database and invisible to SQL; run "
                    "##class(<class>).%BuildIndices() before reading the tables."
                )

        if build_globals:
            t_globals = time.time()
            stats["globals_built"] = self.build_graph_globals()
            stats["globals_build_s"] = round(time.time() - t_globals, 1)

        stats["total_elapsed_s"] = round(time.time() - t_total, 1)

        cursor = self.conn.cursor()
        try:
            for table in ["nodes", "rdf_edges", "rdf_labels", "rdf_props"]:
                try:
                    # Scoped: an unscoped COUNT reports the namespace, so in a shared
                    # namespace the summary of a tenant's load is mostly other
                    # tenants' rows. All four tables carry graph_id.
                    cursor.execute(
                        f"SELECT COUNT(*) FROM {self._table(table)} "
                        "WHERE COALESCE(graph_id, '') = ?",
                        [self.graph],
                    )
                    stats[f"final_{table}_count"] = cursor.fetchone()[0]
                except Exception:
                    pass
        finally:
            try:
                cursor.close()
            except Exception:
                pass

        logger.info(f"Bulk load complete: {stats}")
        return stats


def main():
    """CLI entry point for standalone bulk loading."""
    import argparse
    import pickle
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Bulk load a NetworkX graph into IVG")
    parser.add_argument("pickle_path", help="Path to NetworkX pickle file")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=1972)
    parser.add_argument("--namespace", default="USER")
    parser.add_argument("--user", default="test")
    parser.add_argument("--password", default="test")
    parser.add_argument("--label-attr", default="namespace", help="Node attribute for labels")
    parser.add_argument("--noindex", action="store_true", default=True, help="Use %%NOINDEX for faster loading (default: on)")
    parser.add_argument("--no-noindex", dest="noindex", action="store_false", help="Disable %%NOINDEX (use plain INSERT)")
    parser.add_argument("--no-globals", action="store_true", help="Skip building ^KG/^NKG globals")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument(
        "--graph",
        default="",
        help="Named graph to load into (default: the default graph)",
    )
    args = parser.parse_args()


    logger.info(f"Loading {args.pickle_path}...")
    with open(args.pickle_path, "rb") as f:
        G = pickle.load(f)
    logger.info(f"Graph: {G.number_of_nodes():,} nodes, {G.number_of_edges():,} edges")


    from iris.dbapi._DBAPI import connect  # intersystems_iris.dbapi = iris.dbapi
    conn = connect(args.host, args.port, args.namespace, args.user, args.password)
    logger.info(f"Connected to IRIS {args.host}:{args.port}/{args.namespace}")


    loader = BulkLoader(conn, batch_size=args.batch_size, graph=args.graph)
    stats = loader.load_networkx(
        G,
        label_attr=args.label_attr,
        use_noindex=args.noindex,
        build_globals=not args.no_globals,
    )


    print("\n=== Bulk Load Summary ===")
    for k, v in sorted(stats.items()):
        print(f"  {k}: {v}")

    conn.close()


if __name__ == "__main__":
    main()
