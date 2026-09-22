import json
import logging
from typing import Dict, Any, List, Optional

from iris_vector_graph.status import (
    EngineStatus,
    TableCounts,
    AdjacencyStatus,
    ObjectScriptStatus,
    ArnoStatus,
    IndexInventory,
    SyncReport,
)
from iris_vector_graph.result import IVGResult

logger = logging.getLogger(__name__)


class AdminMixin:
    """Administrative/system operations mixin for IRISGraphEngine.

    Provides schema introspection, query management, index visibility,
    and diagnostic warning aggregation.
    """

    def _handle_show_command(self, cmd: str) -> Dict[str, Any]:
        if "DATABASES" in cmd:
            return IVGResult(
                columns=[
                    "name",
                    "type",
                    "aliases",
                    "access",
                    "address",
                    "role",
                    "writer",
                    "requestedStatus",
                    "currentStatus",
                    "statusMessage",
                    "default",
                    "home",
                    "constituents",
                ],
                rows=[
                    [
                        "neo4j",
                        "standard",
                        [],
                        "read-write",
                        "localhost:7687",
                        "primary",
                        True,
                        "online",
                        "online",
                        "",
                        True,
                        True,
                        [],
                    ]
                ],
            )
        if "PROCEDURES" in cmd:
            procs = self._try_system_procedure(
                type("P", (), {"procedure_name": "dbms.procedures"})()
            )
            if procs:
                return IVGResult(
                    columns=["name", "description", "signature"],
                    rows=[[r[0], r[2], r[1]] for r in procs.rows],
                )
            return IVGResult(columns=["name", "description", "signature"], rows=[])
        if "FUNCTIONS" in cmd:
            fns = self._try_system_procedure(type("P", (), {"procedure_name": "dbms.functions"})())
            if fns:
                return IVGResult(
                    columns=["name", "description", "signature"],
                    rows=[[r[0], r[2], r[1]] for r in fns.rows],
                )
            return IVGResult(columns=["name", "description", "signature"], rows=[])
        if "INDEXES" in cmd:
            return self._show_indexes()
        if "CONSTRAINTS" in cmd:
            return self._show_constraints()
        return IVGResult(columns=["value"], rows=[])

    def _hnsw_indexes(self, table_name: str) -> List[tuple]:
        """The HNSW indexes IRIS actually holds on ``table_name``, as ``(name, properties)``.

        Delegates to :meth:`GraphSchema.hnsw_indexes`, which owns the dictionary read. The
        name is the index's ``SqlName`` — what the operator typed in ``CREATE INDEX`` and
        what they would type to drop it.
        """
        from iris_vector_graph.schema import GraphSchema

        cursor = self.conn.cursor()
        try:
            return GraphSchema.hnsw_indexes(cursor, table_name)
        finally:
            try:
                cursor.close()
            except Exception:
                pass

    def _show_indexes(self) -> "IVGResult":
        cols = ["name", "type", "entityType", "labelsOrTypes", "properties", "state"]
        rows = []
        cursor = self.conn.cursor()

        def _try(sql, default=None):
            try:
                cursor.execute(sql)
                return cursor.fetchall()
            except Exception:
                return default or []

        # HNSW vector indexes, read from the class dictionary. Nothing is emitted when no
        # index exists (spec 226, FR-018): this used to append a `hnsw_node_embeddings` row
        # unconditionally and derive its state from the row count of
        # `kg_NodeEmbeddings_optimized` — `ONLINE` with rows, `BUILDING` without. A row
        # count is not an index, and `BUILDING` told operators to wait for a build that was
        # never running. On the shipped schema it could not run: both embedding tables key
        # on a VARCHAR `id`, and IRIS refuses ANN indices there (ERROR #7222).
        for table_name in ("kg_NodeEmbeddings", "kg_NodeEmbeddings_optimized"):
            table = self._t(table_name)
            for index_name, properties in self._hnsw_indexes(table):
                rows.append(
                    [
                        index_name,
                        "VECTOR(HNSW)",
                        "NODE",
                        [table],
                        [p for p in str(properties).split(",") if p] or ["emb"],
                        "ONLINE",
                    ]
                )

        for (name,) in _try(f"SELECT DISTINCT name FROM {self._t('kg_IVFMeta')}"):
            rows.append([name, "VECTOR(IVF)", "NODE", ["*"], ["emb"], "ONLINE"])

        for (name,) in _try(f"SELECT DISTINCT name FROM {self._t('kg_BM25Meta')}"):
            rows.append([name, "FULLTEXT(BM25)", "NODE", ["*"], ["*"], "ONLINE"])

        for (idx_name,) in _try(f"SELECT DISTINCT idx_name FROM {self._t('kg_PlaidMeta')}"):
            rows.append([idx_name, "VECTOR(PLAID)", "NODE", ["*"], ["emb"], "ONLINE"])

        try:
            native = self._iris_obj()
            nkg_ok = bool(int(native.classMethodValue("Graph.KG.Traversal", "NKGPopulated")))
        except Exception:
            nkg_ok = False
        rows.append(
            [
                "nkg_adjacency",
                "ADJACENCY(^NKG)",
                "RELATIONSHIP",
                ["*"],
                ["*"],
                "ONLINE" if nkg_ok else "NOT_BUILT",
            ]
        )

        kg_ok = False
        try:
            native = self._iris_obj()
            kg_ok = int(native.classMethodValue("Graph.KG.Traversal", "KGEdgeCount", 1)) > 0
        except Exception:
            pass
        rows.append(
            [
                "kg_adjacency",
                "ADJACENCY(^KG)",
                "RELATIONSHIP",
                ["*"],
                ["*"],
                "ONLINE" if kg_ok else "NOT_BUILT",
            ]
        )

        rows.append(["pk_nodes", "UNIQUE", "NODE", ["*"], ["node_id"], "ONLINE"])
        rows.append(["pk_rdf_edges", "UNIQUE", "RELATIONSHIP", ["*"], ["s", "p", "o_id"], "ONLINE"])

        return IVGResult(columns=cols, rows=rows)

    def _show_constraints(self) -> "IVGResult":
        cols = ["name", "type", "entityType", "labelsOrTypes", "properties", "ownedIndex"]
        rows = [
            ["node_id_unique", "UNIQUENESS", "NODE", ["*"], ["node_id"], "pk_nodes"],
            [
                "edge_spo_unique",
                "UNIQUENESS",
                "RELATIONSHIP",
                ["*"],
                ["s", "p", "o_id"],
                "pk_rdf_edges",
            ],
        ]
        try:
            cursor = self.conn.cursor()
            # Check table existence via %Dictionary before querying it — the IRIS
            # Python driver segfaults on SELECT against a non-existent table.
            cursor.execute(
                "SELECT COUNT(*) FROM %Dictionary.CompiledClass " "WHERE Name='Graph.KG.FHIRBridge'"
            )
            if int((cursor.fetchone() or [0])[0]) > 0:
                rows.append(
                    [
                        "fhir_bridge_unique",
                        "UNIQUENESS",
                        "NODE",
                        ["*"],
                        ["external_id", "bridge_type", "node_id"],
                        "pk_fhir_bridges",
                    ]
                )
        except Exception:
            pass
        return IVGResult(columns=cols, rows=rows)

    def _embedding_tables(self, cursor, kind: str) -> List[str]:
        """Every table this install can hold ``kind`` vectors in (spec 230, FR-006).

        The legacy table first — it is the default route, and on an install that has
        never routed anything it is the whole answer — then every route the registry
        names of that kind.

        Kind comes off the registry row, not off a prefix test: `kg_eemb_` and `kg_emb_`
        are unreachable from each other by ``startswith``, but the name is a hash and the
        row is the record of what was created (FR-011). A row with no kind is a node
        route, which is what every row written before 4.0.0 is.

        ``kg_NodeEmbeddings_optimized`` is deliberately not in the node list: it mirrors
        `kg_NodeEmbeddings`'s rows for the ANN index, so adding it would double-count an
        install that built one.
        """
        from iris_vector_graph._engine.schema import ROUTE_KIND_EDGE, ROUTE_KIND_NODE
        from iris_vector_graph.security import (
            is_routed_edge_embedding_table,
            is_routed_embedding_table,
        )

        is_route = (
            is_routed_edge_embedding_table
            if kind == ROUTE_KIND_EDGE
            else is_routed_embedding_table
        )
        legacy = self._legacy_table_for(kind)
        tables = [legacy]
        seen = {legacy.lower()}
        try:
            cursor.execute(
                "SELECT DISTINCT table_name, COALESCE(kind, 'node')"
                f" FROM {self._registry_table()}"
            )
            rows = cursor.fetchall() or []
        except Exception:
            # No registry: a 3.2.0 database, where the legacy table is every table
            # there is. Not an error on the report — nothing is missing.
            return tables
        for row in rows:
            name = str(row[0]) if row and row[0] else ""
            row_kind = str(row[1]) if len(row) > 1 and row[1] else ROUTE_KIND_NODE
            if row_kind != kind or not is_route(name) or name.lower() in seen:
                continue
            seen.add(name.lower())
            tables.append(name)
        return tables

    def _embedding_row_count(self, cursor, kind: str, errors: list) -> int:
        """Rows of ``kind`` across every table that holds them (spec 230, FR-006).

        Counting one table name reported zero for an install whose vectors are all
        routed — a number an operator reads as "the embeddings are gone" while a search
        over the same rows answers fine.

        A table the registry names but the database has lost is reported and skipped:
        the sum is then short, and saying so is the difference between a count that can
        be acted on and a wrong one.
        """
        total = 0
        for table in self._embedding_tables(cursor, kind):
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {self._t(table)}")
                row = cursor.fetchone()
                total += int(row[0]) if row else 0
            except Exception as e:
                errors.append(f"count probe failed for {table}: {e}")
        return total

    def status(self, internals: bool = False) -> "EngineStatus":
        import time as _time

        from iris_vector_graph._engine.schema import ROUTE_KIND_EDGE, ROUTE_KIND_NODE

        t0 = _time.perf_counter()
        errors: list = []
        cursor = self.conn.cursor()

        def _count(sql):
            try:
                cursor.execute(sql)
                row = cursor.fetchone()
                return int(row[0]) if row else 0
            except Exception as e:
                errors.append(f"count probe failed: {e}")
                return 0

        tables = TableCounts(
            nodes=_count(f"SELECT COUNT(*) FROM {self._t('nodes')}"),
            edges=_count(f"SELECT COUNT(*) FROM {self._t('rdf_edges')}"),
            labels=_count(f"SELECT COUNT(*) FROM {self._t('rdf_labels')}"),
            props=_count(f"SELECT COUNT(*) FROM {self._t('rdf_props')}"),
            node_embeddings=self._embedding_row_count(cursor, ROUTE_KIND_NODE, errors),
            edge_embeddings=self._embedding_row_count(cursor, ROUTE_KIND_EDGE, errors),
        )

        kg_count = 0
        kg_capped = False
        kg_populated = False
        nkg_populated = False
        try:
            native = self._iris_obj()
            kg_count = int(native.classMethodValue("Graph.KG.Traversal", "KGEdgeCount", 10000))
            kg_populated = kg_count > 0
            kg_capped = kg_count >= 10000
            nkg_populated = bool(int(native.classMethodValue("Graph.KG.Traversal", "NKGPopulated")))
        except Exception as e:
            try:
                iris_native = self._iris_obj()
                kg_populated = bool(iris_native.isDefined(["KG", "out"]))
            except Exception:
                errors.append(f"adjacency probe failed: {e}")

        kg_predicates_consistent = True
        if kg_populated and tables.edges > 0:
            try:
                native = self._iris_obj()
                kg_pred = str(native.get(["KG", "out", "", 0, ""])) or ""
                if not kg_pred:
                    s_val = ""
                    kg_pred_node = native.orderAll(["KG", "out", "", 0, s_val])
                    if kg_pred_node:
                        kg_pred = str(
                            native.orderAll(["KG", "out", "", 0, str(kg_pred_node), ""]) or ""
                        )
            except Exception:
                kg_pred = ""

            if kg_pred:
                try:
                    cursor.execute(
                        f"SELECT COUNT(*) FROM {self._t('rdf_edges')} WHERE p = ?",
                        [kg_pred],
                    )
                    row = cursor.fetchone()
                    if row and int(row[0]) == 0:
                        kg_predicates_consistent = False
                        errors.append(
                            "Index mismatch: adjacency index predicate does not match current edges. "
                            "Call engine.sync() after reloading graph data."
                        )
                except Exception:
                    pass

        adjacency = AdjacencyStatus(
            kg_populated=kg_populated,
            kg_edge_count=kg_count,
            kg_edge_count_capped=kg_capped,
            nkg_populated=nkg_populated,
            kg_predicates_consistent=kg_predicates_consistent,
            bfs_path="none",
        )

        os_classes = []
        os_deployed = self.capabilities.objectscript_deployed
        _known_classes = [
            "Graph.KG.Traversal",
            "Graph.KG.PageRank",
            "Graph.KG.IVFIndex",
            "Graph.KG.BM25Index",
            "Graph.KG.ArnoAccel",
            "Graph.KG.Snapshot",
            "Graph.KG.Dijkstra",
        ]
        if os_deployed:
            for cls in _known_classes:
                try:
                    cursor.execute(
                        "SELECT COUNT(*) FROM %Dictionary.ClassDefinition WHERE Name = ?",
                        [cls],
                    )
                    row = cursor.fetchone()
                    if row and int(row[0]) > 0:
                        os_classes.append(cls)
                except Exception:
                    pass

        objectscript = ObjectScriptStatus(deployed=os_deployed, classes=os_classes)

        self._detect_arno()
        arno = ArnoStatus(
            loaded=bool(self._arno_available),
            capabilities=dict(self._arno_capabilities),
        )

        hnsw_built = _count(f"SELECT COUNT(*) FROM {self._t('kg_NodeEmbeddings_optimized')}") > 0

        def _list_index(sql):
            try:
                cursor.execute(sql)
                return [row[0] for row in cursor.fetchall() if row[0]]
            except Exception:
                return []

        ivf = _list_index(f"SELECT DISTINCT name FROM {self._t('kg_IVFMeta')}")
        bm25 = _list_index(f"SELECT DISTINCT name FROM {self._t('kg_BM25Meta')}")
        plaid = _list_index(f"SELECT DISTINCT idx_name FROM {self._t('kg_PlaidMeta')}")

        indexes = IndexInventory(
            hnsw_built=hnsw_built,
            ivf_indexes=ivf,
            bm25_indexes=bm25,
            plaid_indexes=plaid,
        )

        if arno.loaded and arno.capabilities.get("bfs") and adjacency.nkg_populated:
            adjacency.bfs_path = "arno"
        elif objectscript.deployed and adjacency.kg_populated:
            adjacency.bfs_path = "objectscript"

        probe_ms = (_time.perf_counter() - t0) * 1000
        try:
            ledger_status = self._ledger_status()  # spec 213 FR-050
        except Exception:
            ledger_status = None
        return EngineStatus(
            ledger=ledger_status,
            tables=tables,
            adjacency=adjacency,
            objectscript=objectscript,
            arno=arno,
            indexes=indexes,
            embedding_dimension=self.embedding_dimension,
            probe_ms=probe_ms,
            errors=errors,
            pending_sync=self._nkg_dirty,
            internals=(
                {"^KG_populated": adjacency.kg_populated, "^NKG_populated": adjacency.nkg_populated}
                if internals
                else None
            ),
        )

    def verify_sync(self, heal: bool = False) -> "SyncReport":
        """Check whether the ^KG/^NKG adjacency indexes agree with the SQL tables.

        The authoritative graph state lives in ``Graph_KG.rdf_edges``. The ^KG/^NKG
        globals are a derived acceleration index, maintained inline by
        ``create_edge`` / ``bulk_*`` / ``WriteAdjacency`` but NOT by every write
        path. BYPASS paths — ``drop_graph``, ``delete_node``, raw SQL inserts, the
        ``map_sql_table`` bridge, or an interrupted bulk load — mutate the table
        without touching the globals, leaving BFS / centrality / var-length Cypher
        silently stale. This compares the SQL edge count against the ^NKG edge
        count and reports the divergence.

        Args:
            heal: When True, run ``sync()`` to rebuild the globals if drift is found.

        Returns:
            SyncReport. ``report.in_sync`` is False when counts diverge OR the
            in-process ``_nkg_dirty`` flag is set. ``bool(report)`` mirrors
            ``in_sync`` for ``if not engine.verify_sync(): engine.sync()`` usage.
        """
        sql_edges = 0
        global_edges = 0
        global_nodes = 0
        detail = None
        indeterminate = False

        cursor = self.conn.cursor()
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {self._t('rdf_edges')}")
            row = cursor.fetchone()
            sql_edges = int(row[0]) if row else 0
        except Exception as e:
            indeterminate = True
            detail = f"SQL edge count failed: {str(e)[:120]}"

        try:
            iris_obj = self._iris_obj()
            global_edges = int(iris_obj.classMethodValue("Graph.KG.Traversal", "NKGEdgeCount"))
            global_nodes = int(iris_obj.classMethodValue("Graph.KG.Traversal", "NKGNodeCount"))
        except Exception as e:
            indeterminate = True
            detail = f"NKG count failed (globals likely unbuilt): {str(e)[:120]}"

        pending = bool(getattr(self, "_nkg_dirty", False))

        # Drift oracle. ``_nkg_dirty`` is the authoritative in-process signal: a
        # write path that bypassed adjacency maintenance set it. The count
        # comparison is a SECONDARY heuristic and is deliberately one-directional:
        # we only flag drift when SQL edges *exceed* indexed edges (the
        # unambiguous "rows exist in the table but not in the index" case).
        #
        # We do NOT flag the reverse (globals > SQL), because ^NKG's meta
        # edgeCount is known to over-count: InternNode/InternLabel are
        # append-only (never decremented on delete) and NKG interning ignores
        # graph_id while the SQL UNIQUE is on (s,p,o,graph_id). Treating
        # globals>SQL as drift would produce false positives on any DB that has
        # seen deletes. A full rebuild (sync()) is the only way to reconcile
        # the meta counter, and verify_sync(heal=True) does exactly that.
        sql_exceeds = sql_edges > global_edges
        in_sync = (not sql_exceeds) and (not pending) and (not indeterminate)

        if not in_sync and detail is None:
            if pending and not sql_exceeds:
                detail = "pending_sync flag set — writes occurred without sync()"
            else:
                detail = (
                    f"drift: {sql_edges} SQL edges vs {global_edges} indexed edges. "
                    "A write path bypassed adjacency maintenance — call engine.sync()."
                )

        report = SyncReport(
            in_sync=in_sync,
            sql_edges=sql_edges,
            global_edges=global_edges,
            global_nodes=global_nodes,
            pending_sync=pending,
            healed=False,
            detail=detail,
        )

        if heal and not in_sync:
            try:
                self.sync()
                report.healed = True
                report.in_sync = True
                report.detail = (detail or "") + " | healed via sync()"
            except Exception as e:
                report.detail = (detail or "") + f" | heal failed: {str(e)[:120]}"

        return report

    def verify_graph(self, graph: Optional[str] = None) -> Dict[str, Any]:
        """Check whether one graph's ^KG adjacency still agrees with its rows.

        Additive alongside ``verify_sync``, which cannot see the drift that
        matters here. ``verify_sync`` compares one SQL count against ^NKG's
        ``edgeCount``: graph-blind, and one-directional by design, because ^NKG
        interning is append-only and ignores ``graph_id`` so its counter
        over-counts. It flags ``sql_edges > global_edges`` and stays silent on
        the reverse — which is precisely what every deletion path produces.
        ``drop_graph`` deletes the rows and leaves the adjacency behind, so
        afterwards ``sql_edges`` is *below* ``global_edges`` and ``verify_sync``
        reports in-sync over a graph that still answers traversals.

        This one is scoped to a single graph, bidirectional, and reads ^KG
        directly.

        Args:
            graph: The graph to check. ``None`` and ``""`` both mean the default
                graph. Names that cannot be represented as a subscript raise
                ``ValueError`` before the round trip (see ADR-0003).

        Returns:
            The report as a dict, with keys ``graph``, ``graphKey``, ``ok``,
            ``counts``, ``drift``, ``checked``, ``unverified`` and ``unscoped``.

            A dict rather than a dataclass on purpose: ``Graph.KG.GraphVerify``
            produces the report and is its single owner. A Python class mirroring
            the shape would be a second declaration of it, free to drift — the
            same failure the one-inventory rule exists to remove.

            ``ok`` answers only "does this graph's adjacency agree with its
            rows?". ``unscoped`` lists stores that hold graph content but carry
            no graph column at all; that is a standing schema gap rather than
            per-graph drift, so it does not move ``ok``.
        """
        from iris_vector_graph._validate import validate_graph_name

        canonical = validate_graph_name(graph)
        raw = self._iris_obj().classMethodValue(
            "Graph.KG.GraphVerify", "VerifyGraph", canonical
        )
        return json.loads(str(raw))

    def list_active_queries(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return active IRIS SQL queries.

        Requires IRIS Enterprise — returns [] on Community Edition.
        The IRIS Python driver segfaults when %SYS.ProcessQuery is queried
        on Community (insufficient privilege causes a C-level crash, not a
        Python exception). We detect Community via GetISCProduct() == 4.
        """
        try:
            iris_obj = self._iris_obj()
            product = int(str(iris_obj.classMethodValue("%SYSTEM.Version", "GetISCProduct")))
            if product == 4:  # Community Edition — unsafe to query %SYS.ProcessQuery
                return []
        except Exception:
            return []  # cannot determine edition — play safe
        cursor = self.conn.cursor()
        try:
            # Inline limit — IRIS Python driver (ARM64) segfaults on
            # FETCH FIRST ? ROWS ONLY with a parameterized placeholder.
            safe_limit = max(1, int(limit))
            cursor.execute(
                f"SELECT ID, State, ClientName, Command FROM %SYS.ProcessQuery "
                f"WHERE Command IS NOT NULL FETCH FIRST {safe_limit} ROWS ONLY",
            )
            return [
                {
                    "id": str(r[0]),
                    "state": str(r[1]),
                    "client": str(r[2]),
                    "command": str(r[3])[:200],
                }
                for r in cursor.fetchall()
            ]
        except Exception as e:
            logger.warning("list_active_queries failed: %s", e)
            return []

    def kill_query(self, query_id: str) -> bool:
        cursor = self.conn.cursor()
        try:
            cursor.execute("SELECT %SYSTEM.SYS.KillProcess(?)", [int(query_id)])
            return True
        except Exception as e:
            logger.warning("kill_query %s failed: %s", query_id, e)
            return False

    def get_centrality_warnings(self, max_entries: int = 50) -> List[Dict[str, Any]]:
        try:
            import iris as _iris

            iris_inst = self._iris_obj()
        except Exception as e:
            logger.debug("get_centrality_warnings: createIRIS failed: %s", e)
            return []

        warnings_list: List[Dict[str, Any]] = []
        try:
            ts = iris_inst.nextSubscript(False, "^IVG.warnings", "centrality", "")
            while ts is not None and ts != "":
                src = iris_inst.nextSubscript(False, "^IVG.warnings", "centrality", ts, "")
                while src is not None and src != "":
                    reason = iris_inst.get("^IVG.warnings", "centrality", ts, src)
                    warnings_list.append(
                        {
                            "timestamp": str(ts),
                            "source": str(src),
                            "reason": str(reason) if reason is not None else "",
                        }
                    )
                    if len(warnings_list) >= max_entries:
                        return warnings_list
                    src = iris_inst.nextSubscript(False, "^IVG.warnings", "centrality", ts, src)
                ts = iris_inst.nextSubscript(False, "^IVG.warnings", "centrality", ts)
        except Exception as e:
            logger.debug("get_centrality_warnings: ^IVG.warnings traversal failed: %s", e)
        return warnings_list

    def get_community_warnings(self, max_entries: int = 50) -> List[Dict[str, Any]]:
        try:
            import iris as _iris

            iris_inst = self._iris_obj()
        except Exception:
            return []
        warnings_list: List[Dict[str, Any]] = []
        try:
            ts = iris_inst.nextSubscript(False, "^IVG.warnings", "communities", "")
            while ts is not None and ts != "":
                src = iris_inst.nextSubscript(False, "^IVG.warnings", "communities", ts, "")
                while src is not None and src != "":
                    reason = iris_inst.get("^IVG.warnings", "communities", ts, src)
                    warnings_list.append(
                        {
                            "timestamp": str(ts),
                            "source": str(src),
                            "reason": str(reason) if reason is not None else "",
                        }
                    )
                    if len(warnings_list) >= max_entries:
                        return warnings_list
                    src = iris_inst.nextSubscript(False, "^IVG.warnings", "communities", ts, src)
                ts = iris_inst.nextSubscript(False, "^IVG.warnings", "communities", ts)
        except Exception as e:
            logger.debug("get_community_warnings: ^IVG.warnings traversal failed: %s", e)
        return warnings_list
