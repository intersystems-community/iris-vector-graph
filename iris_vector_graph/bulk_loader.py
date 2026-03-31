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

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 5000
SCHEMA = "Graph_KG"


class BulkLoader:
    """High-performance bulk graph loader for IVG."""

    def __init__(self, conn, schema: str = SCHEMA, batch_size: int = DEFAULT_BATCH_SIZE):
        self.conn = conn
        self.schema = schema
        self.batch_size = batch_size
        self._stats: Dict[str, Any] = {}

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
        """Execute INSERT in batches with progress logging."""
        total = len(params)
        inserted = 0
        errors = 0
        t0 = time.time()

        for batch_start in range(0, total, self.batch_size):
            batch = params[batch_start : batch_start + self.batch_size]
            try:
                cursor.executemany(sql, batch)
                if commit_per_batch:
                    self.conn.commit()
                inserted += len(batch)
            except Exception as e:
                if commit_per_batch:
                    self.conn.rollback()
                err_str = str(e)
                # UNIQUE violation: fall back to row-by-row for this batch
                if "-119" in err_str or "UNIQUE" in err_str:
                    for row in batch:
                        try:
                            cursor.execute(sql, row)
                            if commit_per_batch:
                                self.conn.commit()
                            inserted += 1
                        except Exception:
                            if commit_per_batch:
                                self.conn.rollback()
                            errors += 1
                else:
                    logger.error(f"{label} batch at {batch_start} failed: {e}")
                    errors += len(batch)

            elapsed = time.time() - t0
            if inserted > 0 and (inserted % (self.batch_size * 4) == 0 or batch_start + self.batch_size >= total):
                rate = inserted / elapsed if elapsed > 0 else 0
                logger.info(
                    f"{label}: {inserted:,}/{total:,} ({rate:,.0f} rows/s, "
                    f"{errors} errors, {elapsed:.1f}s)"
                )

        return inserted

    def _rebuild_indices(self, cursor, class_name: str) -> bool:
        """Rebuild all indices for a class using %BuildIndices."""
        try:
            cursor.execute(
                f"SELECT %SYSTEM_SQL.BuildIndices('{class_name}')"
            )

            return True
        except Exception as e:
            logger.warning(f"Index rebuild via SQL failed for {class_name}: {e}")

            try:
                cursor.execute(f"TUNE TABLE {self.schema}.{class_name.split('.')[-1]}")
                return True
            except Exception as e2:
                logger.error(f"TUNE TABLE also failed for {class_name}: {e2}")
                return False

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

        logger.info(f"Phase 1: Loading {len(nodes):,} nodes (noindex={use_noindex})...")
        if skip_existing:
            cursor.execute(f"SELECT node_id FROM {self._table('nodes')}")
            existing = set(r[0] for r in cursor.fetchall())
            new_nodes = [(nid, attrs) for nid, attrs in nodes if nid not in existing]
            logger.info(f"  {len(existing):,} existing, {len(new_nodes):,} new")
        else:
            new_nodes = nodes
            existing = set()

        node_params = [[nid] for nid, _ in new_nodes]
        node_sql = f"INSERT{hint} INTO {self._table('nodes')} (node_id) VALUES (?)"
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
                    label_params.append([nid, lbl[:128]])

        n_labels = 0
        if label_params:
            logger.info(f"Phase 2: Loading {len(label_params):,} labels (noindex={use_noindex})...")
            label_sql = f"INSERT{hint} INTO {self._table('rdf_labels')} (s, label) VALUES (?, ?)"
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
                prop_params.append([nid, k, str(v)])

        n_props = 0
        if prop_params:
            logger.info(f"Phase 3: Loading {len(prop_params):,} properties (noindex={use_noindex})...")
            prop_sql = f"INSERT{hint} INTO {self._table('rdf_props')} (s, \"key\", val) VALUES (?, ?, ?)"
            n_props = self._executemany_batched(cursor, prop_sql, prop_params, "Props")
        else:
            logger.info(f"Phase 3: No new properties to load")

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
            cursor.execute(f"SELECT s, p, o_id FROM {self._table('rdf_edges')}")
            existing_edges = set((r[0], r[1], r[2]) for r in cursor.fetchall())
            new_edges = [(s, p, t, q) for s, p, t, q in deduped if (s, p, t) not in existing_edges]
            logger.info(f"  {len(existing_edges):,} existing edges, {len(new_edges):,} new")
            deduped = new_edges

        logger.info(f"Loading {len(deduped):,} edges (noindex={use_noindex})...")

        edge_params = []
        for src, pred, tgt, quals in deduped:
            qual_json = json.dumps(quals) if quals else None
            edge_params.append([src, pred, tgt, qual_json])

        if use_noindex:
            sql = f"INSERT %NOINDEX %NOCHECK INTO {self._table('rdf_edges')} (s, p, o_id, qualifiers) VALUES (?, ?, ?, ?)"
        else:
            sql = f"INSERT INTO {self._table('rdf_edges')} (s, p, o_id, qualifiers) VALUES (?, ?, ?, ?)"

        n_edges = self._executemany_batched(cursor, sql, edge_params, "Edges")

        elapsed = time.time() - t0
        stats = {
            "edges": n_edges,
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
        import intersystems_iris
        iris_obj = intersystems_iris.createIRIS(self.conn)
        results = {}

        for cls in ["Graph.KG.rdfedges", "Graph.KG.rdflabels", "Graph.KG.rdfprops", "Graph.KG.nodes"]:
            try:
                t0 = time.time()
                iris_obj.classMethodVoid(cls, "%BuildIndices")
                dt = time.time() - t0
                results[cls] = True
                logger.info(f"  Rebuilt indices for {cls} ({dt:.1f}s)")
            except Exception as e:
                logger.warning(f"  %BuildIndices failed for {cls}: {e}")
                results[cls] = False

        return results

    def build_graph_globals(self) -> bool:
        """
        Build ^KG and ^NKG traversal globals from SQL tables.

        Calls Graph.KG.Traversal.BuildKG() which reads from rdf_edges/rdf_labels/rdf_props
        and populates ^KG (adjacency lists) and ^NKG (NICHE-encoded index).

        Requires Graph.KG.Traversal and Graph.KG.GraphIndex to be deployed.
        Returns True if successful.
        """
        try:
            import intersystems_iris
            iris_obj = intersystems_iris.createIRIS(self.conn)
            logger.info("Building ^KG + ^NKG globals from SQL tables...")
            t0 = time.time()
            iris_obj.classMethodVoid("Graph.KG.Traversal", "BuildKG")
            dt = time.time() - t0

            node_count = iris_obj.get("^NKG", "$meta", "nodeCount")
            edge_count = iris_obj.get("^NKG", "$meta", "edgeCount")
            logger.info(f"BuildKG completed in {dt:.1f}s: {node_count} nodes, {edge_count} edges in ^NKG")
            return True
        except Exception as e:
            logger.error(f"BuildKG failed: {e}")
            logger.info(
                "Ensure Graph.KG.Traversal and Graph.KG.GraphIndex classes "
                "are deployed. BFS/PPR will not work without ^KG globals."
            )
            return False

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

        if build_globals:
            t_globals = time.time()
            stats["globals_built"] = self.build_graph_globals()
            stats["globals_build_s"] = round(time.time() - t_globals, 1)

        stats["total_elapsed_s"] = round(time.time() - t_total, 1)

        cursor = self.conn.cursor()
        for table in ["nodes", "rdf_edges", "rdf_labels", "rdf_props"]:
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {self._table(table)}")
                stats[f"final_{table}_count"] = cursor.fetchone()[0]
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
    args = parser.parse_args()


    logger.info(f"Loading {args.pickle_path}...")
    with open(args.pickle_path, "rb") as f:
        G = pickle.load(f)
    logger.info(f"Graph: {G.number_of_nodes():,} nodes, {G.number_of_edges():,} edges")


    from intersystems_iris.dbapi._DBAPI import connect
    conn = connect(args.host, args.port, args.namespace, args.user, args.password)
    logger.info(f"Connected to IRIS {args.host}:{args.port}/{args.namespace}")


    loader = BulkLoader(conn, batch_size=args.batch_size)
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
