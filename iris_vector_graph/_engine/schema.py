import json
import logging
from typing import Dict, Any, Optional, List

from iris_vector_graph.schema import GraphSchema, _call_classmethod
from iris_vector_graph.cypher.translator import _table
from iris_vector_graph.capabilities import IRISCapabilities

logger = logging.getLogger(__name__)


class SchemaMixin:
    """Schema management and graph initialization mixin for IRISGraphEngine.
    
    Provides schema creation, status checking, graph building, and inference."""

    def is_ready(self) -> bool:
        try:
            cur = self.conn.cursor()
            cur.execute("SELECT COUNT(*) FROM Graph_KG.nodes")
            cur.fetchone()
            return True
        except Exception:
            return False


    def get_labels(self) -> List[str]:
        cur = self.conn.cursor()
        cur.execute("SELECT DISTINCT label FROM Graph_KG.rdf_labels ORDER BY label")
        return [r[0] for r in cur.fetchall()]


    def get_relationship_types(self) -> List[str]:
        cur = self.conn.cursor()
        cur.execute("SELECT DISTINCT p FROM Graph_KG.rdf_edges ORDER BY p")
        return [r[0] for r in cur.fetchall()]


    def get_node_count(self, label: str = None) -> int:
        cur = self.conn.cursor()
        if label:
            cur.execute("SELECT COUNT(*) FROM Graph_KG.rdf_labels WHERE label = ?", [label])
        else:
            cur.execute("SELECT COUNT(*) FROM Graph_KG.nodes")
        return int(cur.fetchone()[0])


    def get_edge_count(self, predicate: str = None) -> int:
        cur = self.conn.cursor()
        if predicate:
            cur.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE p = ?", [predicate])
        else:
            cur.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges")
        return int(cur.fetchone()[0])


    def get_label_distribution(self) -> Dict[str, int]:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT label, COUNT(*) AS cnt FROM Graph_KG.rdf_labels GROUP BY label ORDER BY cnt DESC"
        )
        return {r[0]: int(r[1]) for r in cur.fetchall()}


    def get_property_keys(self, label: str = None) -> List[str]:
        cur = self.conn.cursor()
        if label:
            cur.execute(
                'SELECT DISTINCT rp."key" FROM Graph_KG.rdf_props rp'
                " JOIN Graph_KG.rdf_labels rl ON rl.s = rp.s"
                ' WHERE rl.label = ? ORDER BY rp."key"',
                [label],
            )
        else:
            cur.execute('SELECT DISTINCT "key" FROM Graph_KG.rdf_props ORDER BY "key"')
        return [r[0] for r in cur.fetchall()]


    def node_exists(self, node_id: str) -> bool:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id = ?",
            [node_id],
        )
        row = cur.fetchone()
        return row is not None and int(row[0]) > 0


    def initialize_schema(self, auto_deploy_objectscript: bool = True) -> dict:
        """
        Create the base schema tables in IRIS.

        Returns a status dict with keys:
          - 'tables_created': True/False
          - 'objectscript_deployed': True/False
          - 'kg_built': True/False  
          - 'embedding_dimension': int
          - 'warnings': list[str]

        Safe to call on existing databases — statements that fail with "already exists"
        are silently ignored.  Raises if ``embedding_dimension`` has not been set (either
        via the constructor or prior calls to :meth:`store_embedding`).

        Args:
            auto_deploy_objectscript: When True (default), attempt to load and compile
                the ObjectScript .cls files from iris_src/ into IRIS.  On failure a
                warning is logged and the engine falls back to Python/SQL paths.
                Set to False to skip .cls deployment entirely.
        """
        from iris_vector_graph.utils import _split_sql_statements

        dim = self.embedding_dimension
        if dim is None:
            raise ValueError(
                "embedding_dimension must be set before calling initialize_schema(). "
                "Pass it to IRISGraphEngine(conn, embedding_dimension=<N>) or call "
                "store_embedding() first so the dimension can be inferred."
            )

        cursor = self.conn.cursor()
        try:
            cursor.execute("CREATE SCHEMA Graph_KG")
        except Exception:
            pass  # already exists

        sql = GraphSchema.get_base_schema_sql(embedding_dimension=dim)
        for stmt in _split_sql_statements(sql):
            if not stmt.strip():
                continue
            try:
                cursor.execute(stmt)
            except Exception as e:
                err = str(e).lower()
                _OPTIONAL_DDL_PATTERNS = (
                    "ifind",
                    "json_value",
                    "indextype",
                    "%find",
                    "kg_txt",
                    "kg_rrf",
                    "irisdev",
                    "iris_src",
                )
                if (
                    "already exists" not in err
                    and "already has a" not in err
                    and "already has index" not in err
                ):
                    import re as _re_ddl

                    _sqlcode = _re_ddl.search(
                        r"sqlcode.*?<(-?\d+)>", err
                    ) or _re_ddl.search(r"<(-\d+)>", err)
                    _sqlcode_val = _sqlcode.group(1) if _sqlcode else ""
                    is_index_on_rdf_edges = (
                        _sqlcode_val == "-400"
                        and "rdf_edges" in stmt.lower()
                        and "create index" in stmt.lower()
                    )
                    if (
                        any(
                            p in err or p in stmt.lower()
                            for p in _OPTIONAL_DDL_PATTERNS
                        )
                        or is_index_on_rdf_edges
                    ):
                        logger.debug(
                            "Optional DDL skipped (will retry via ALTER TABLE): %s",
                            stmt[:80],
                        )
                    else:
                        logger.warning(
                            "Schema setup warning: %s | Statement: %.100s", e, stmt
                        )

        # 3. Ensure indexes and run schema migrations (e.g. column size upgrades)
        GraphSchema.ensure_indexes(cursor)

        # 4. Check for dimension mismatch on existing tables; fix untyped vector column
        try:
            db_dim = GraphSchema.get_embedding_dimension(cursor)
            if db_dim is None:
                # Column exists but has no dimension (e.g. created without VECTOR(DOUBLE,N)).
                # ALTER TABLE to add the dimension so procedure compilation succeeds.
                logger.info(
                    "kg_NodeEmbeddings.emb has no dimension — altering to VECTOR(DOUBLE, %d)",
                    dim,
                )
                try:
                    cursor.execute(
                        f"ALTER TABLE Graph_KG.kg_NodeEmbeddings ALTER COLUMN emb VECTOR(DOUBLE, {dim})"
                    )
                    cursor.execute(
                        f"ALTER TABLE Graph_KG.kg_NodeEmbeddings_optimized ALTER COLUMN emb VECTOR(DOUBLE, {dim})"
                    )
                    self.conn.commit()
                    logger.info("ALTER TABLE succeeded — dimension set to %d", dim)
                except Exception as alter_e:
                    logger.warning("Could not alter emb column dimension: %s", alter_e)
            elif db_dim != dim:
                row_count = None
                try:
                    cursor.execute("SELECT COUNT(*) FROM Graph_KG.kg_NodeEmbeddings")
                    row_count = cursor.fetchone()[0]
                except Exception:
                    row_count = None
                if row_count == 0:
                    logger.info(
                        "Embedding dimension mismatch (DB=%d, configured=%d) on EMPTY table — "
                        "altering VECTOR column to %d",
                        db_dim, dim, dim,
                    )
                    for _t in ("Graph_KG.kg_NodeEmbeddings", "Graph_KG.kg_NodeEmbeddings_optimized"):
                        try:
                            cursor.execute(
                                f"ALTER TABLE {_t} ALTER COLUMN emb VECTOR(DOUBLE, {dim})"
                            )
                        except Exception as _ae:
                            logger.warning("Could not ALTER %s to dim %d: %s", _t, dim, _ae)
                    for _t in ("Graph_KG.kg_EdgeEmbeddings",):
                        try:
                            cursor.execute(
                                f"ALTER TABLE {_t} ALTER COLUMN emb VECTOR(DOUBLE, {dim})"
                            )
                        except Exception:
                            pass
                    self.conn.commit()
                else:
                    logger.error(
                        "CRITICAL: Embedding dimension mismatch! DB has %d but engine configured for %d. "
                        "Vector operations will fail. Table is non-empty (%s rows) — drop and recreate "
                        "kg_NodeEmbeddings manually to change dimension.",
                        db_dim,
                        dim,
                        row_count,
                    )
        except Exception as e:
            logger.warning("Could not verify embedding dimension: %s", e)

        # 5. Install stored procedures
        procedure_errors = []
        for stmt in GraphSchema.get_procedures_sql_list(
            table_schema="Graph_KG",
            embedding_dimension=dim,
        ):
            if not stmt.strip():
                continue
            try:
                cursor.execute(stmt)
            except Exception as e:
                err = str(e).lower()
                if "already exists" in err or "already has" in err:
                    continue  # idempotent re-run — schema or procedure already installed
                # Only kg_KNN_VEC is required for server-side vector search;
                # kg_TXT and kg_RRF_FUSE are optional (depend on full-text search feature)
                is_core = "procedure graph_kg.kg_knn_vec" in stmt.lower()
                if is_core:
                    _sqlcode = ""
                    import re as _re_proc
                    m = _re_proc.search(r"sqlcode.*?<(-?\d+)>", err)
                    if m:
                        _sqlcode = m.group(1)
                    if _sqlcode == "-260":
                        logger.debug(
                            "kg_KNN_VEC skipped: vector dimension mismatch in kg_NodeEmbeddings "
                            "(table has mixed-dim vectors from tests). Non-fatal. | Error: %s", e
                        )
                    else:
                        procedure_errors.append((stmt[:80], e))
                        logger.error(
                            "Procedure DDL failed: %s | Error: %s", stmt[:80], e
                        )
                else:
                    logger.debug(
                        "Optional procedure DDL skipped (non-fatal): %s | Error: %s",
                        stmt[:80],
                        e,
                    )

        if procedure_errors:
            raise RuntimeError(
                f"initialize_schema() failed to install {len(procedure_errors)} "
                f"stored procedure(s). Server-side vector search will be unavailable. "
                f"First error: {procedure_errors[0][1]}"
            )

        self.conn.commit()

        # 5b. Create SQLUser views so IVG's Python PPR fallback can use unqualified table names
        for view_sql in [
            "CREATE VIEW SQLUser.nodes AS SELECT node_id, created_at FROM Graph_KG.nodes",
            "CREATE VIEW SQLUser.rdf_edges AS SELECT * FROM Graph_KG.rdf_edges",
            "CREATE VIEW SQLUser.rdf_labels AS SELECT * FROM Graph_KG.rdf_labels",
            "CREATE VIEW SQLUser.rdf_props AS SELECT * FROM Graph_KG.rdf_props",
        ]:
            try:
                cursor.execute(view_sql)
            except Exception:
                pass

        # 6. Deploy ObjectScript .cls layer (best-effort)
        if auto_deploy_objectscript:
            try:
                pkg_dir = Path(__file__).parent.parent / "iris_src"
                if not pkg_dir.exists():
                    pkg_dir = Path(__file__).parent / ".." / "iris_src"
                self.capabilities = GraphSchema.deploy_objectscript_classes(
                    cursor, pkg_dir.resolve(), conn=self.conn
                )
            except Exception as exc:
                logger.debug(
                    "ObjectScript auto-deploy skipped (expected in Docker — use docker cp + LoadDir): %s",
                    exc,
                )
                self.capabilities = IRISCapabilities()
        else:
            self.capabilities = IRISCapabilities()

        # 6b. Always detect capabilities from %Dictionary (deployment may have failed
        # but classes could already be compiled from a prior docker cp + LoadDir)
        if not self.capabilities.objectscript_deployed:
            try:
                cursor.execute(
                    "SELECT COUNT(*) FROM %Dictionary.ClassDefinition "
                    "WHERE Name='Graph.KG.PageRank'"
                )
                row = cursor.fetchone()
                if row and row[0]:
                    self.capabilities.objectscript_deployed = True
                    logger.info("ObjectScript classes detected (pre-compiled)")
            except Exception:
                pass

        if not self.capabilities.objectscript_deployed:
            try:
                iris_obj = self._iris_obj()
                routine_exists = int(iris_obj.classMethodValue("%Routine", "Exists", "Graph.KG.PageRank.1"))
                if routine_exists:
                    cursor.execute(
                        "SELECT COUNT(*) FROM %Dictionary.ClassDefinition "
                        "WHERE Name='Graph.KG.PageRank'"
                    )
                    row = cursor.fetchone()
                    class_registered = int(row[0]) if row else 0
                    if class_registered:
                        self.capabilities.objectscript_deployed = True
                        logger.info("ObjectScript classes detected via %%Routine.Exists fallback")
                    else:
                        logger.warning(
                            "ObjectScript routines compiled but class dictionary missing "
                            "(irishealth ^oddDEF/^rOBJ mapping issue — classes not callable). "
                            "Use iris-community image or Atelier API for class deployment."
                        )
            except Exception:
                pass

        if self.capabilities.objectscript_deployed and not self.capabilities.kg_built:
            try:
                built = GraphSchema.bootstrap_kg_global(cursor, conn=self.conn)
                if built:
                    self.capabilities.kg_built = True
                    self.conn.commit()
            except Exception as exc:
                logger.warning("^KG bootstrap failed: %s", exc)

        status = {
            "tables_created": True,
            "objectscript_deployed": self.capabilities.objectscript_deployed,
            "kg_built": self.capabilities.kg_built,
            "embedding_dimension": dim,
            "warnings": [],
        }
        if not self.capabilities.objectscript_deployed:
            status["warnings"].append(
                "ObjectScript classes not deployed — BFS, Subgraph, PageRank using Python fallbacks. "
                "Run docker cp iris_src/src <container>:/tmp/src && docker exec <container> iris session IRIS "
                "-U USER 'Do $system.OBJ.LoadDir(\"/tmp/src\",\"ck\",,1)' to deploy."
            )
        if not self.capabilities.kg_built:
            status["warnings"].append(
                "^KG adjacency index not built — multi-hop BFS unavailable. "
                "Call BuildKG() after loading data: from iris_vector_graph.schema import _call_classmethod; "
                "_call_classmethod(conn, 'Graph.KG.Traversal', 'BuildKG')"
            )

        if status["warnings"]:
            for w in status["warnings"]:
                logger.warning("IVG setup: %s", w[:120])

        logger.info(
            "initialize_schema() complete — objectscript=%s kg_built=%s dim=%d",
            status["objectscript_deployed"],
            status["kg_built"],
            dim,
        )
        return status


    def get_schema_visualization(self) -> dict:
        cursor = self.conn.cursor()

        cursor.execute("SELECT DISTINCT label FROM Graph_KG.rdf_labels ORDER BY label")
        labels = [r[0] for r in cursor.fetchall()]

        cursor.execute("SELECT DISTINCT p FROM Graph_KG.rdf_edges ORDER BY p")
        rel_types = [r[0] for r in cursor.fetchall()]

        nodes = []
        for i, label in enumerate(labels):
            cursor.execute(
                "SELECT TOP 1 rl.s FROM Graph_KG.rdf_labels rl WHERE rl.label = ?",
                [label],
            )
            row = cursor.fetchone()
            sample_id = row[0] if row else None

            prop_names = []
            if sample_id:
                cursor.execute(
                    'SELECT DISTINCT TOP 20 "key" FROM Graph_KG.rdf_props WHERE s = ? '
                    'ORDER BY "key"',
                    [sample_id],
                )
                prop_names = [r[0] for r in cursor.fetchall()]

            nodes.append(
                {
                    "id": i,
                    "name": label,
                    "labels": [label],
                    "properties": [{"name": p, "type": "String"} for p in prop_names],
                }
            )

        label_to_id = {n["name"]: n["id"] for n in nodes}

        rels = []
        for i, rel_type in enumerate(rel_types):
            cursor.execute(
                "SELECT s, o_id FROM Graph_KG.rdf_edges WHERE p = ?", [rel_type]
            )
            row = cursor.fetchone()
            start_label_id = 0
            end_label_id = 0
            if row:
                src_id, tgt_id = row
                cursor.execute(
                    "SELECT TOP 1 label FROM Graph_KG.rdf_labels WHERE s = ?", [src_id]
                )
                src_row = cursor.fetchone()
                if src_row:
                    start_label_id = label_to_id.get(src_row[0], 0)
                cursor.execute(
                    "SELECT TOP 1 label FROM Graph_KG.rdf_labels WHERE s = ?", [tgt_id]
                )
                tgt_row = cursor.fetchone()
                if tgt_row:
                    end_label_id = label_to_id.get(tgt_row[0], 0)

            rels.append(
                {
                    "id": i,
                    "name": rel_type,
                    "type": rel_type,
                    "properties": [],
                    "startNode": start_label_id,
                    "endNode": end_label_id,
                }
            )

        return {"nodes": nodes, "relationships": rels}


    def sync(self) -> bool:
        """Unified sync of adjacency and acceleration indexes (^KG + ^NKG).

        Idempotent. Chooses Rust accelerator for ^NKG when arno is available.
        Always clears the pending-sync flag after the attempt completes.

        Returns:
            True on success, False if a fatal error prevented completion.
        """
        kg_ok = self._sync_kg()
        nkg_ok = self._sync_nkg()
        self._nkg_dirty = False
        return kg_ok and nkg_ok


    def _sync_kg(self) -> bool:
        try:
            iris_obj = self._iris_obj()
            iris_obj.classMethodVoid("Graph.KG.Traversal", "BuildKG")
            self.capabilities.kg_built = True
            self._nkg_dirty = True
            logger.info("^KG adjacency index rebuilt successfully")
            return True
        except Exception as e:
            logger.warning("_sync_kg failed: %s", e)
            return False


    def _sync_nkg(self) -> bool:
        try:
            iris_obj = self._iris_obj()
            rust_succeeded = False
            if self._detect_arno() and self._arno_capabilities.get("rust_callout"):
                try:
                    import json as _json
                    raw = str(iris_obj.classMethodValue("Graph.KG.NKGAccel", "BuildNKGRust"))
                    result = _json.loads(raw)
                    if "error" not in result:
                        logger.info("^NKG rebuilt via Rust: %s", result)
                        rust_succeeded = True
                    else:
                        logger.warning("BuildNKGRust returned error (%s), falling back to ObjectScript", result["error"])
                except Exception as rust_exc:
                    logger.warning("BuildNKGRust raised (%s), falling back to ObjectScript", rust_exc)
            if not rust_succeeded:
                iris_obj.classMethodVoid("Graph.KG.Traversal", "BuildNKG")
            iris_obj.classMethodValue("Graph.KG.Traversal", "Build2HopStats")
            try:
                iris_obj.classMethodVoid("Graph.KG.NKGAccel", "InvalidateAdjCache")
            except Exception:
                pass
            self._nkg_dirty = False
            return True
        except Exception as e:
            logger.warning("_sync_nkg failed: %s", e)
            return False


    def rebuild_kg(self) -> bool:
        """Deprecated: use ``engine.sync()`` instead."""
        import warnings
        warnings.warn(
            "rebuild_kg() is deprecated. Use engine.sync() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self._sync_kg()


    def rebuild_nkg(self) -> bool:
        """Deprecated: use ``engine.sync()`` instead."""
        import warnings
        warnings.warn(
            "rebuild_nkg() is deprecated. Use engine.sync() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self._sync_nkg()


    def backfill_degp(self) -> int:
        try:
            result = self._iris_obj().classMethodValue("Graph.KG.Traversal", "BackfillDegp")
            return int(result)
        except Exception as e:
            logger.warning("backfill_degp failed: %s", e)
            return 0


    def backfill_deg2p_exact(self) -> int:
        try:
            result = self._iris_obj().classMethodValue("Graph.KG.Traversal", "Build2HopExactStats")
            return int(result)
        except Exception as e:
            logger.warning("backfill_deg2p_exact failed: %s", e)
            return 0


    def materialize_inference(
        self, rules: str = "rdfs", graph: Optional[str] = None
    ) -> Dict[str, int]:
        RDFS_SUBCLASSOF = "http://www.w3.org/2000/01/rdf-schema#subClassOf"
        RDFS_SUBPROPOF = "http://www.w3.org/2000/01/rdf-schema#subPropertyOf"
        RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
        RDFS_DOMAIN = "http://www.w3.org/2000/01/rdf-schema#domain"
        RDFS_RANGE = "http://www.w3.org/2000/01/rdf-schema#range"
        OWL_EQUIV_CLASS = "http://www.w3.org/2002/07/owl#equivalentClass"
        OWL_EQUIV_PROP = "http://www.w3.org/2002/07/owl#equivalentProperty"
        OWL_INVERSE = "http://www.w3.org/2002/07/owl#inverseOf"
        OWL_SAME_AS = "http://www.w3.org/2002/07/owl#sameAs"
        OWL_TRANS_PROP = "http://www.w3.org/2002/07/owl#TransitiveProperty"
        OWL_SYM_PROP = "http://www.w3.org/2002/07/owl#SymmetricProperty"
        INFERRED_JSON = '{"inferred":true}'

        cursor = self.conn.cursor()
        inferred_count = 0

        graph_filter_sql = " AND graph_id = ?" if graph else " AND (graph_id IS NULL)"
        graph_filter_params = [graph] if graph else []

        def _fetch_edges(predicate):
            cursor.execute(
                "SELECT s, o_id FROM Graph_KG.rdf_edges WHERE p = ? "
                "AND (qualifiers IS NULL OR qualifiers NOT LIKE '%\"inferred\"%')"
                + graph_filter_sql,
                [predicate] + graph_filter_params,
            )
            return set((r[0], r[1]) for r in cursor.fetchall())

        def _exists(s, p, o):
            if graph:
                cursor.execute(
                    "SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE s=? AND p=? AND o_id=? AND graph_id=?",
                    [s, p, o, graph],
                )
            else:
                cursor.execute(
                    "SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE s=? AND p=? AND o_id=? AND (graph_id IS NULL)",
                    [s, p, o],
                )
            row = cursor.fetchone()
            return row is not None and int(row[0]) > 0

        def _insert_inferred(triples):
            nonlocal inferred_count
            for s, p, o in triples:
                if not _exists(s, p, o):
                    try:
                        if graph:
                            cursor.execute(
                                "INSERT INTO Graph_KG.rdf_edges (s, p, o_id, qualifiers, graph_id) VALUES (?, ?, ?, ?, ?)",
                                [s, p, o, INFERRED_JSON, graph],
                            )
                        else:
                            cursor.execute(
                                "INSERT INTO Graph_KG.rdf_edges (s, p, o_id, qualifiers) VALUES (?, ?, ?, ?)",
                                [s, p, o, INFERRED_JSON],
                            )
                        inferred_count += 1
                    except Exception:
                        pass
            try:
                self.conn.commit()
            except Exception:
                pass

        def _transitive_closure(direct_edges):
            closure = set(direct_edges)
            changed = True
            while changed:
                changed = False
                new = set()
                for a, b in closure:
                    for b2, c in closure:
                        if b == b2 and (a, c) not in closure and a != c:
                            new.add((a, c))
                if new:
                    closure |= new
                    changed = True
            return closure - direct_edges

        subclass_direct = _fetch_edges(RDFS_SUBCLASSOF)
        subprop_direct = _fetch_edges(RDFS_SUBPROPOF)

        inferred = set()
        inferred |= {
            (a, RDFS_SUBCLASSOF, c) for a, c in _transitive_closure(subclass_direct)
        }
        inferred |= {
            (a, RDFS_SUBPROPOF, c) for a, c in _transitive_closure(subprop_direct)
        }

        rdf_type_edges = _fetch_edges(RDF_TYPE)
        all_subclass = subclass_direct | {
            (a, c) for a, _, c in inferred if _ == RDFS_SUBCLASSOF
        }
        for x, cls_a in list(rdf_type_edges):
            for a, b in all_subclass:
                if a == cls_a:
                    inferred.add((x, RDF_TYPE, b))

        domain_edges = _fetch_edges(RDFS_DOMAIN)
        range_edges = _fetch_edges(RDFS_RANGE)
        all_predicate_edges = {}
        cursor.execute(
            "SELECT s, p, o_id FROM Graph_KG.rdf_edges WHERE p NOT IN (?, ?, ?, ?, ?) LIMIT 50000",
            [RDFS_SUBCLASSOF, RDFS_SUBPROPOF, RDF_TYPE, RDFS_DOMAIN, RDFS_RANGE],
        )
        for s, p, o in cursor.fetchall():
            all_predicate_edges.setdefault(p, []).append((s, o))

        for p, domain in domain_edges:
            for s, _ in all_predicate_edges.get(p, []):
                inferred.add((s, RDF_TYPE, domain))

        for p, rng in range_edges:
            for _, o in all_predicate_edges.get(p, []):
                inferred.add((o, RDF_TYPE, rng))

        if rules == "owl":
            equiv_class = _fetch_edges(OWL_EQUIV_CLASS)
            for a, b in equiv_class:
                inferred.add((a, RDFS_SUBCLASSOF, b))
                inferred.add((b, RDFS_SUBCLASSOF, a))

            equiv_prop = _fetch_edges(OWL_EQUIV_PROP)
            for p, q in equiv_prop:
                inferred.add((p, RDFS_SUBPROPOF, q))
                inferred.add((q, RDFS_SUBPROPOF, p))

            inverse_edges = _fetch_edges(OWL_INVERSE)
            for p, q in inverse_edges:
                for x, y in all_predicate_edges.get(p, []):
                    inferred.add((y, q, x))
                for x, y in all_predicate_edges.get(q, []):
                    inferred.add((y, p, x))

            cursor.execute(
                "SELECT s FROM Graph_KG.rdf_edges WHERE p=? AND o_id=?",
                [RDF_TYPE, OWL_TRANS_PROP],
            )
            trans_props = {r[0] for r in cursor.fetchall()}
            for tp in trans_props:
                tp_edges = _fetch_edges(tp)
                inferred |= {(a, tp, c) for a, c in _transitive_closure(tp_edges)}

            cursor.execute(
                "SELECT s FROM Graph_KG.rdf_edges WHERE p=? AND o_id=?",
                [RDF_TYPE, OWL_SYM_PROP],
            )
            sym_props = {r[0] for r in cursor.fetchall()}
            for sp in sym_props:
                for x, y in all_predicate_edges.get(sp, []):
                    inferred.add((y, sp, x))

        _insert_inferred(inferred)
        return {"inferred": inferred_count}


    def retract_inference(self, graph: Optional[str] = None) -> int:
        cursor = self.conn.cursor()
        if graph:
            cursor.execute(
                "DELETE FROM Graph_KG.rdf_edges WHERE qualifiers LIKE '%\"inferred\":\"true\"%' AND graph_id = ?",
                [graph],
            )
        else:
            cursor.execute(
                "DELETE FROM Graph_KG.rdf_edges WHERE qualifiers LIKE '%\"inferred\":\"true\"%'"
            )
        deleted = cursor.rowcount or 0
        try:
            self.conn.commit()
        except Exception:
            pass
        return deleted




    def reify_edge(
        self,
        edge_id: int,
        reifier_id: str = None,
        label: str = "Reification",
        props: Dict[str, str] = None,
    ) -> Optional[str]:
        if reifier_id is None:
            reifier_id = f"reif:{edge_id}"
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                f"SELECT edge_id FROM {_table('rdf_edges')} WHERE edge_id = ?",
                [edge_id],
            )
            if not cursor.fetchone():
                logger.warning(f"reify_edge: edge_id={edge_id} not found")
                return None
            self.create_node(reifier_id)
            cursor.execute(
                f"INSERT INTO {_table('rdf_labels')} (s, label) "
                f"SELECT ?, ? WHERE NOT EXISTS "
                f"(SELECT 1 FROM {_table('rdf_labels')} WHERE s = ? AND label = ?)",
                [reifier_id, label, reifier_id, label],
            )
            cursor.execute(
                f"INSERT INTO {_table('rdf_reifications')} (reifier_id, edge_id) VALUES (?, ?)",
                [reifier_id, edge_id],
            )
            if props:
                for key, val in props.items():
                    cursor.execute(
                        f'INSERT INTO {_table("rdf_props")} (s, "key", val) '
                        f"SELECT ?, ?, ? WHERE NOT EXISTS "
                        f'(SELECT 1 FROM {_table("rdf_props")} WHERE s = ? AND "key" = ?)',
                        [reifier_id, key, str(val), reifier_id, key],
                    )
            self.conn.commit()
            return reifier_id
        except Exception as e:
            self.conn.rollback()
            logger.warning(f"reify_edge({edge_id}) failed: {e}")
            return None
        finally:
            cursor.close()


    def get_reifications(self, edge_id: int) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                f'SELECT r.reifier_id, p."key", p.val '
                f"FROM {_table('rdf_reifications')} r "
                f"LEFT JOIN {_table('rdf_props')} p ON p.s = r.reifier_id "
                f"WHERE r.edge_id = ?",
                [edge_id],
            )
            rows = cursor.fetchall()
            result: Dict[str, dict] = {}
            for reifier_id, key, val in rows:
                if reifier_id not in result:
                    result[reifier_id] = {"reifier_id": reifier_id, "properties": {}}
                if key is not None:
                    result[reifier_id]["properties"][key] = val
            return list(result.values())
        except Exception as e:
            logger.warning(f"get_reifications({edge_id}) failed: {e}")
            return []
        finally:
            cursor.close()


    def delete_reification(self, reifier_id: str) -> bool:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                f"DELETE FROM {_table('rdf_reifications')} WHERE reifier_id = ?",
                [reifier_id],
            )
            cursor.execute(
                f"DELETE FROM {_table('rdf_props')} WHERE s = ?", [reifier_id]
            )
            cursor.execute(
                f"DELETE FROM {_table('rdf_labels')} WHERE s = ?", [reifier_id]
            )
            cursor.execute(
                f"DELETE FROM {_table('nodes')} WHERE node_id = ?", [reifier_id]
            )
            self.conn.commit()
            return True
        except Exception as e:
            self.conn.rollback()
            logger.warning(f"delete_reification({reifier_id}) failed: {e}")
            return False
        finally:
            cursor.close()
