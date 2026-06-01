import json
import logging
from typing import Dict, Any, List, Optional, Tuple

from iris_vector_graph.cypher.translator import _table
from iris_vector_graph.result import IVGResult
from iris_vector_graph.security import validate_table_name
from iris_vector_graph._validate import (
    IVFBuildInput, VectorSearchInput, BM25BuildInput, BM25SearchInput, VecSearchInput,
)

logger = logging.getLogger(__name__)


class VectorMixin:
    def _detect_stored_vector_dtype(self) -> str:
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                f"SELECT TOP 1 emb FROM {_table('kg_NodeEmbeddings')} WHERE emb IS NOT NULL"
            )
            row = cursor.fetchone()
            cursor.close()
            if row is None:
                return "DOUBLE"
            emb_csv = str(row[0])
            sample = ",".join(emb_csv.split(",")[:2])
            for dtype in ("FLOAT", "DOUBLE"):
                try:
                    c2 = self.conn.cursor()
                    c2.execute(
                        f"SELECT VECTOR_COSINE(emb, TO_VECTOR(?, {dtype})) FROM {_table('kg_NodeEmbeddings')} WHERE emb IS NOT NULL LIMIT 1",
                        [sample],
                    )
                    c2.fetchone()
                    c2.close()
                    logger.info("Auto-detected stored vector dtype: %s", dtype)
                    return dtype
                except Exception:
                    pass
        except Exception:
            pass
        return "DOUBLE"
    def _build_index_registry(self) -> Dict[str, str]:
        registry: Dict[str, str] = {}
        try:
            import iris as _iris_pkg
            if not callable(getattr(_iris_pkg, "gref", None)):
                raise AttributeError("iris.gref not available")
            for global_name, type_str in (
                ("^IVF",      "ivf"),
                ("^VecIdx",   "vec"),
                ("^BM25Idx",  "bm25"),
                ("^PLAID",    "plaid"),
            ):
                gref = _iris_pkg.gref(global_name)
                name = ""
                for _ in range(10000):
                    name = gref.order([name])
                    if not isinstance(name, str) or name == "":
                        break
                    registry[name] = type_str
        except Exception:
            pass
        if not registry:
            try:
                from iris_vector_graph.schema import _call_classmethod
                for cls_name, type_str in (
                    ("Graph.KG.IVFIndex",   "ivf"),
                    ("Graph.KG.BM25Index",  "bm25"),
                    ("Graph.KG.PLAIDSearch", "plaid"),
                ):
                    raw = str(_call_classmethod(self.conn, cls_name, "List"))
                    for name in (n.strip() for n in raw.split(",") if n.strip()):
                        registry[name] = type_str
            except Exception:
                pass
                for sql_query, type_str in (
                    ("SELECT DISTINCT name FROM Graph_KG.ivf_indexes", "ivf"),
                    ("SELECT DISTINCT name FROM Graph_KG.bm25_indexes", "bm25"),
                    ("SELECT DISTINCT name FROM Graph_KG.plaid_indexes", "plaid"),
                ):
                    try:
                        cur.execute(sql_query)
                        for row in cur.fetchall():
                            registry[str(row[0])] = type_str
                    except Exception:
                        pass
            except Exception:
                pass
        if self._probe_native_vec():
            registry["hnsw"] = "hnsw"
        return registry
    def index(self, name: str) -> "Index":
        from iris_vector_graph.index_protocol import Index
        from iris_vector_graph.errors import IndexNotFoundError
        if name not in self._index_registry:
            raise IndexNotFoundError(name, known=list(self._index_registry))
        concept = self._LEGACY_TO_CONCEPT.get(
            self._index_registry[name], self._index_registry[name]
        )
        return Index(name=name, type=concept, engine=self)
    def create_index(self, config, replace: bool = False) -> "Index":
        from iris_vector_graph.index_protocol import Index
        if config.name in self._index_registry:
            if not replace:
                raise ValueError(
                    f"Index '{config.name}' already exists; pass replace=True to recreate."
                )
            self.index(config.name).drop()
        self._pending_index_config[config.name] = config
        self._index_registry[config.name] = config.type
        return Index(name=config.name, type=config.type, engine=self)
    def list_indexes(self) -> "List[Index]":
        return [self.index(n) for n in sorted(self._index_registry)]
    def _index_config(self, name: str):
        return self._pending_index_config.get(name)
    def _build_vector_index(self, name: str, **kw) -> dict:
        cfg = self._index_config(name)
        if cfg is not None and getattr(cfg, "method", "ivf") == "vec":
            self.vec_create_index(name, dim=kw.get("dim") or cfg.dim, metric=cfg.metric)
            return self.vec_build(name)
        nlist = kw.get("nlist", getattr(cfg, "nlist", 256))
        metric = kw.get("metric", getattr(cfg, "metric", "cosine"))
        return self.ivf_build(name, nlist=nlist, metric=metric, node_ids=kw.get("node_ids"))
    def _search_vector_index(self, name: str, q, k: int = 10, **kw) -> list:
        cfg = self._index_config(name)
        if cfg is not None and getattr(cfg, "method", "ivf") == "vec":
            return self.vec_search(name, q, k, **kw)
        return self.ivf_search(name, q, k, **kw)
    def _vector_index_insert(self, name: str, id_: str, vec) -> None:
        cfg = self._index_config(name)
        if cfg is not None and getattr(cfg, "method", "ivf") == "vec":
            self.vec_insert(name, id_, vec)
        else:
            self.ivf_insert(name, id_, vec)
    def _vector_index_drop(self, name: str) -> None:
        cfg = self._index_config(name)
        if cfg is not None and getattr(cfg, "method", "ivf") == "vec":
            self.vec_drop(name)
        else:
            self.ivf_drop(name)
    def _vector_index_info(self, name: str) -> dict:
        cfg = self._index_config(name)
        if cfg is not None and getattr(cfg, "method", "ivf") == "vec":
            return self.vec_info(name)
        return self.ivf_info(name)
    def _build_fulltext_index(self, name: str, **kw) -> dict:
        cfg = self._index_config(name)
        props = kw.get("properties") or (cfg.properties if cfg else ["name"])
        k1 = kw.get("k1", getattr(cfg, "k1", 1.5))
        b = kw.get("b", getattr(cfg, "b", 0.75))
        info = self.bm25_build(name, props, k1=k1, b=b)
        from iris_vector_graph.index_protocol import _rows_of
        from iris_vector_graph.errors import IndexNotBuiltError
        if _rows_of(info or {}) == 0:
            raise IndexNotBuiltError(name, rows=0)
        return info
    def _build_multivector_index(self, name: str, **kw) -> dict:
        docs = kw.get("docs")
        if not docs:
            from iris_vector_graph.errors import IndexNotBuiltError
            raise IndexNotBuiltError(name, rows=0)
        cfg = self._index_config(name)
        return self.plaid_build(
            name, docs,
            n_clusters=kw.get("n_clusters", getattr(cfg, "n_clusters", None)),
            dim=kw.get("dim", getattr(cfg, "dim", 128)),
        )
    def _build_neighborhood_index(self, name: str, **kw) -> dict:
        raise NotImplementedError(
            "neighborhood_vector index build lands in spec 181; "
            "config registered but build not yet wired."
        )
    def _search_neighborhood_index(self, name: str, q, k: int = 10, **kw) -> list:
        raise NotImplementedError("neighborhood_vector search lands in spec 181.")
    def _neighborhood_index_drop(self, name: str) -> None:
        self._iris_obj().kill("^NKG", "q")
    def _neighborhood_index_info(self, name: str) -> dict:
        return {"type": "neighborhood_vector", "rows": 0}
    def edge_vector_search(
        self,
        query_embedding,
        top_k: int = 10,
        score_threshold: float = None,
    ) -> List[dict]:
        if isinstance(query_embedding, list):
            import json as _json
            query_vec_str = _json.dumps(query_embedding)
            dim = len(query_embedding)
        else:
            query_vec_str = query_embedding
            dim = str(query_embedding).count(",") + 1

        query_cast = f"TO_VECTOR(?, {self.vector_dtype}, {dim})"

        having = (
            f"HAVING score >= {score_threshold}" if score_threshold is not None else ""
        )
        sql = (
            f"SELECT TOP {int(top_k)} s, p, o_id, "
            f"VECTOR_COSINE(emb, {query_cast}) AS score "
            f"FROM {_table('kg_EdgeEmbeddings')} "
            f"ORDER BY score DESC "
            f"{having}"
        )

        cursor = self.conn.cursor()
        try:
            cursor.execute(sql, [query_vec_str])
        except Exception as e:
            if "-30" in str(e) or "not found" in str(e).lower() or "empty" in str(e).lower():
                return []
            raise
        rows = cursor.fetchall()
        if not rows:
            return []
        return [
            {"s": row[0], "p": row[1], "o_id": row[2], "score": float(row[3])}
            for row in rows
        ]
    def _validate_k(self, k: Any) -> int:
        """
        Validates and caps the 'k' parameter (TOP clause limit)
        1 <= k <= 1000, defaults to 50.
        Handles non-numeric strings by failing safe to 50.
        """
        try:
            k = int(k or 50)
        except (ValueError, TypeError):
            return 50
        return min(max(1, k), 1000)
    def kg_KNN_VEC(
        self, query_vector: str, k: int = 50, label_filter: Optional[str] = None,
        dtype: Optional[str] = None,
    ) -> List[Tuple[str, float]]:
        _dtype = (dtype or self.vector_dtype).upper()
        cursor = self.conn.cursor()
        try:
            emb_table = _table("kg_NodeEmbeddings")
            labels_table = _table("rdf_labels")

            qv = query_vector.strip() if isinstance(query_vector, str) else query_vector
            exclude_id: Optional[str] = None
            if isinstance(qv, str) and not qv.startswith("["):
                exclude_id = qv
                cursor.execute(
                    f"SELECT emb FROM {emb_table} WHERE id = ?", [exclude_id]
                )
                row = cursor.fetchone()
                if not row:
                    return []
                query_vector = f"[{str(row[0])}]"

            if label_filter and exclude_id:
                cursor.execute(
                    f"SELECT TOP ? n.id, VECTOR_COSINE(n.emb, TO_VECTOR(?, {_dtype})) AS score"
                    f" FROM {emb_table} n"
                    f" LEFT JOIN {labels_table} L ON L.s = n.id"
                    f" WHERE L.label = ? AND n.id != ?"
                    f" ORDER BY score DESC",
                    [k, query_vector, label_filter, exclude_id],
                )
            elif label_filter:
                cursor.execute(
                    f"SELECT TOP ? n.id, VECTOR_COSINE(n.emb, TO_VECTOR(?, {_dtype})) AS score"
                    f" FROM {emb_table} n"
                    f" LEFT JOIN {labels_table} L ON L.s = n.id"
                    f" WHERE L.label = ?"
                    f" ORDER BY score DESC",
                    [k, query_vector, label_filter],
                )
            elif exclude_id:
                cursor.execute(
                    f"SELECT TOP ? n.id, VECTOR_COSINE(n.emb, TO_VECTOR(?, {_dtype})) AS score"
                    f" FROM {emb_table} n"
                    f" WHERE n.id != ?"
                    f" ORDER BY score DESC",
                    [k, query_vector, exclude_id],
                )
            else:
                cursor.execute(
                    f"SELECT TOP ? n.id, VECTOR_COSINE(n.emb, TO_VECTOR(?, {_dtype})) AS score"
                    f" FROM {emb_table} n"
                    f" ORDER BY score DESC",
                    [k, query_vector],
                )
            results = cursor.fetchall()
            return [(entity_id, float(similarity)) for entity_id, similarity in results]
        except Exception as e:
            logger.warning(
                f"Server-side kg_KNN_VEC failed: {e}. Falling back to client-side logic."
            )
            return self._kg_KNN_VEC_python_optimized(query_vector, k, label_filter)
    def search_nodes_by_vector(
        self,
        query: "Union[List[float], str]",
        k: int = 10,
        label_filter: Optional[str] = None,
        ivf_name: Optional[str] = None,
        nprobe: int = 8,
    ) -> List[Tuple[str, float]]:
        if not isinstance(query, str):
            VecSearchInput(query=list(query), k=k, nprobe=nprobe)
        if self._probe_native_vec():
            query_json = json.dumps([float(v) for v in query]) if not isinstance(query, str) else query
            return self.kg_KNN_VEC(query_json, k=k, label_filter=label_filter)
        if ivf_name is not None:
            query_list = json.loads(query) if isinstance(query, str) else query
            return self.ivf_search(ivf_name, query_list, k=k, nprobe=nprobe)
        query_list = json.loads(query) if isinstance(query, str) else query
        return self.ivf_search("default", query_list, k=k, nprobe=nprobe)
    def _kg_KNN_VEC_python_optimized(
        self, query_vector: str, k: int = 50, label_filter: Optional[str] = None
    ) -> List[Tuple[str, float]]:
        _dtype = getattr(self, 'vector_dtype', 'DOUBLE')
        emb_table = _table("kg_NodeEmbeddings")
        labels_table = _table("rdf_labels")

        if label_filter:
            sql = (
                f"SELECT TOP {int(k)} n.id, VECTOR_COSINE(n.emb, TO_VECTOR(?, {_dtype})) AS score"
                f" FROM {emb_table} n"
                f" LEFT JOIN {labels_table} L ON L.s = n.id"
                f" WHERE L.label = ?"
                f" ORDER BY score DESC"
            )
            params = [query_vector, label_filter]
        else:
            sql = (
                f"SELECT TOP {int(k)} n.id, VECTOR_COSINE(n.emb, TO_VECTOR(?, {_dtype})) AS score"
                f" FROM {emb_table} n"
                f" ORDER BY score DESC"
            )
            params = [query_vector]

        try:
            from iris_vector_graph.embedded import _sql_statement_execute, _is_ddtab_error
            rs = _sql_statement_execute(sql, params)
            results = [(row[0], float(row[1])) for row in rs if row[0] is not None]
            return results
        except Exception:
            pass

        try:
            cursor = self.conn.cursor()
            cursor.execute(sql, params)
            results = [(row[0], float(row[1])) for row in cursor.fetchall()]
            cursor.close()
            return results
        except Exception:
            pass

        return self._kg_KNN_VEC_client_side(query_vector, k, label_filter)
    def _kg_KNN_VEC_client_side(
        self, query_vector: str, k: int = 50, label_filter: Optional[str] = None
    ) -> List[Tuple[str, float]]:
        cursor = self.conn.cursor()
        try:
            import numpy as np

            query_array = np.array(json.loads(query_vector))

            emb_table = _table("kg_NodeEmbeddings")
            labels_table = _table("rdf_labels")
            if label_filter is None:
                cursor.execute(f"SELECT n.id, n.emb FROM {emb_table} n WHERE n.emb IS NOT NULL")
            else:
                cursor.execute(
                    f"SELECT n.id, n.emb FROM {emb_table} n"
                    f" LEFT JOIN {labels_table} L ON L.s = n.id"
                    f" WHERE n.emb IS NOT NULL AND L.label = ?",
                    [label_filter],
                )

            similarities = []
            while True:
                batch = cursor.fetchmany(1000)
                if not batch:
                    break
                for entity_id, emb_csv in batch:
                    try:
                        emb_array = np.fromstring(str(emb_csv), dtype=float, sep=",")
                        dot_product = np.dot(query_array, emb_array)
                        query_norm = np.linalg.norm(query_array)
                        emb_norm = np.linalg.norm(emb_array)
                        if query_norm > 0 and emb_norm > 0:
                            cos_sim = dot_product / (query_norm * emb_norm)
                            similarities.append((entity_id, float(cos_sim)))
                    except Exception:
                        continue

            similarities.sort(key=lambda x: x[1], reverse=True)
            return similarities[:k]

        except Exception as e:
            logger.error(f"Client-side kg_KNN_VEC failed: {e}")
            raise
        finally:
            cursor.close()
    def kg_TXT(
        self, query_text: str, k: int = 50, min_confidence: int = 0
    ) -> List[Tuple[str, float]]:
        """
        Enhanced text search using server-side SQL procedure

        Args:
            query_text: Text query string
            k: Number of results to return
            min_confidence: Minimum confidence score (0-1000 scale)

        Returns:
            List of (entity_id, relevance_score) tuples
        """
        cursor = self.conn.cursor()
        try:
            # Call server-side procedure for unified logic
            # Signature: (queryText, k, minConfidence)
            cursor.execute(
                "CALL iris_vector_graph.kg_TXT(?, ?, ?)",
                [query_text, k, min_confidence],
            )
            results = cursor.fetchall()
            return [(entity_id, float(score)) for entity_id, score in results]

        except Exception as e:
            logger.error(f"kg_TXT failed: {e}")
            raise
        finally:
            cursor.close()
    def kg_NEIGHBORHOOD_EXPANSION(
        self,
        entity_list: List[str],
        expansion_depth: int = 1,
        confidence_threshold: int = 500,
    ) -> List[Dict[str, Any]]:
        """
        Efficient neighborhood expansion for multiple entities using JSON_TABLE filtering

        Args:
            entity_list: List of seed entity IDs
            expansion_depth: Number of hops to expand (1-3 recommended)
            confidence_threshold: Minimum confidence for edges (0-1000 scale)

        Returns:
            List of expanded entities with metadata
        """
        if not entity_list:
            return []

        cursor = self.conn.cursor()
        try:
            # Build parameterized query for multiple entities
            entity_placeholders = ",".join(["?" for _ in entity_list])

            sql = f"""
                SELECT DISTINCT e.s, e.p, e.o_id, jt.confidence
                FROM rdf_edges e,
                     JSON_TABLE(e.qualifiers, '$' COLUMNS(confidence INTEGER PATH '$.confidence')) jt
                WHERE e.s IN ({entity_placeholders}) AND jt.confidence >= ?
                ORDER BY confidence DESC, e.s, e.p
            """

            params = entity_list + [confidence_threshold]
            cursor.execute(sql, params)

            results = []
            for row in cursor.fetchall():
                results.append(
                    {
                        "source": row[0],
                        "predicate": row[1],
                        "target": row[2],
                        "confidence": row[3],
                    }
                )

            return results

        except Exception as e:
            logger.error(f"kg_NEIGHBORHOOD_EXPANSION failed: {e}")
            raise
        finally:
            cursor.close()
    def validate_vector_table(self, table: str, vector_col: str) -> dict:
        from iris_vector_graph.security import sanitize_identifier

        sanitize_identifier(table)
        sanitize_identifier(vector_col)
        schema, tbl = (table.split(".", 1) + [""])[:2] if "." in table else ("", table)
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? AND COLUMN_NAME = ?",
                [schema or "USER", tbl or table, vector_col],
            )
            row = cursor.fetchone()
            if not row or int(row[0]) == 0:
                raise ValueError(f"Column '{vector_col}' not found in table '{table}'")
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            row_count = int(cursor.fetchone()[0])
            cursor.execute(f"SELECT TOP 1 {vector_col} FROM {table}")
            sample = cursor.fetchone()
            dimension = None
            if sample and sample[0]:
                try:
                    import json

                    v = (
                        json.loads(sample[0])
                        if isinstance(sample[0], str)
                        else sample[0]
                    )
                    dimension = len(v)
                except Exception:
                    pass
            return {
                "table": table,
                "vector_col": vector_col,
                "dimension": dimension,
                "row_count": row_count,
            }
        finally:
            cursor.close()
    def vector_search(
        self,
        table: str,
        vector_col: str,
        query_embedding,
        top_k: int = 10,
        id_col: str = "id",
        return_cols: List[str] = None,
        score_threshold: float = None,
    ) -> List[dict]:
        from iris_vector_graph.security import sanitize_identifier

        sanitize_identifier(table)
        sanitize_identifier(vector_col)
        sanitize_identifier(id_col)
        if return_cols:
            for col in return_cols:
                sanitize_identifier(col)

        if isinstance(query_embedding, list):
            import json

            query_vec_str = json.dumps(query_embedding)
        else:
            query_vec_str = query_embedding

        extra = ", ".join(
            sanitize_identifier(c) for c in (return_cols or []) if c != id_col
        )

        dim = None
        if isinstance(query_embedding, list):
            dim = len(query_embedding)
        elif isinstance(query_embedding, str):
            dim = query_embedding.count(",") + 1

        if dim:
            query_cast = f"TO_VECTOR(?, {self.vector_dtype}, {dim})"
        else:
            query_cast = f"TO_VECTOR(?, {self.vector_dtype})"

        select_cols = (
            f"t.{id_col}, VECTOR_COSINE(t.{vector_col}, {query_cast}) AS score"
        )
        if extra:
            select_cols += f", {extra}"

        having = (
            f"HAVING score >= {score_threshold}" if score_threshold is not None else ""
        )
        sql = (
            f"SELECT TOP {int(top_k)} {select_cols} "
            f"FROM {table} t "
            f"ORDER BY score DESC "
            f"{having}"
        )

        cursor = self.conn.cursor()
        try:
            cursor.execute(sql, [query_vec_str])
            cols = [d[0].lower() for d in cursor.description]
            results = []
            for row in cursor.fetchall():
                r = dict(zip(cols, row))
                r["id"] = r.pop(id_col.lower(), r.get("id"))
                results.append(r)
            return results
        except Exception as ex:
            raise ValueError(
                f"vector_search failed on {table}.{vector_col}: {ex}. "
                f"Ensure the column is a VECTOR type and query_embedding has the correct dimension."
            ) from ex
        finally:
            cursor.close()
    def multi_vector_search(
        self,
        sources: List[dict],
        query_embedding,
        top_k: int = 10,
        fusion: str = "rrf",
        rrf_k: int = 60,
    ) -> List[dict]:
        if isinstance(query_embedding, list):
            import json

            query_vec_str = json.dumps(query_embedding)
        else:
            query_vec_str = query_embedding

        per_source_k = top_k * 2

        all_results: List[dict] = []
        for source in sources:
            tbl = source["table"]
            col = source.get("col") or source.get("vector_col", "emb")
            id_c = source.get("id_col", "id")
            weight = float(source.get("weight", 1.0))
            return_c = source.get("return_cols")
            try:
                rows = self.vector_search(
                    table=tbl,
                    vector_col=col,
                    query_embedding=query_vec_str,
                    top_k=per_source_k,
                    id_col=id_c,
                    return_cols=return_c,
                )
                for i, r in enumerate(rows):
                    r["source_table"] = tbl
                    r["_rank"] = i + 1
                    r["_weight"] = weight
                all_results.extend(rows)
            except Exception as ex:
                logger.warning(f"multi_vector_search: skipping {tbl}: {ex}")

        if not all_results:
            return []

        if fusion == "rrf":
            scores: Dict[str, float] = {}
            meta: Dict[str, dict] = {}
            for r in all_results:
                node_id = str(r["id"])
                weight = r["_weight"]
                rank = r["_rank"]
                rrf_score = weight * (1.0 / (rrf_k + rank))
                scores[node_id] = scores.get(node_id, 0.0) + rrf_score
                if node_id not in meta:
                    meta[node_id] = {
                        k: v for k, v in r.items() if not k.startswith("_")
                    }
            ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
            results = []
            for rank_i, (node_id, score) in enumerate(ranked, 1):
                row = meta[node_id].copy()
                row["score"] = round(score, 6)
                row["rank"] = rank_i
                results.append(row)
            return results
        else:
            seen: set = set()
            merged = []
            for r in sorted(all_results, key=lambda x: x.get("score", 0), reverse=True):
                nid = str(r["id"])
                if nid not in seen:
                    seen.add(nid)
                    clean = {k: v for k, v in r.items() if not k.startswith("_")}
                    merged.append(clean)
                    if len(merged) >= top_k:
                        break
            return merged
    def kg_RRF_FUSE(
        self, k: int, k1: int, k2: int, c: int, query_vector: str, query_text: str
    ) -> List[Tuple[str, float, float, float]]:
        vec_results: List[Tuple[str, float]] = []
        txt_results: List[Tuple[str, float]] = []

        import json as _json
        vec_list = _json.loads(query_vector) if isinstance(query_vector, str) else query_vector

        try:
            for idx_name in self._index_registry:
                if self._index_registry[idx_name] == "ivf":
                    raw = self.ivf_search(idx_name, vec_list, k=k1)
                    vec_results = [(r["id"], float(r.get("score", 0))) for r in raw]
                    break
            for idx_name in self._index_registry:
                if self._index_registry[idx_name] == "bm25":
                    txt_results = self.bm25_search(idx_name, query_text, k=k2)
                    break
        except Exception as e:
            logger.error(f"kg_RRF_FUSE index search failed: {e}")

        vec_rank = {nid: i + 1 for i, (nid, _) in enumerate(vec_results)}
        txt_rank = {nid: i + 1 for i, (nid, _) in enumerate(txt_results)}
        all_ids = set(vec_rank) | set(txt_rank)

        fused = []
        for nid in all_ids:
            v_r = vec_rank.get(nid, len(vec_results) + c)
            t_r = txt_rank.get(nid, len(txt_results) + c)
            rrf = 1.0 / (c + v_r) + 1.0 / (c + t_r)
            v_score = dict(vec_results).get(nid, 0.0)
            t_score = dict(txt_results).get(nid, 0.0)
            fused.append((nid, rrf, v_score, t_score))

        fused.sort(key=lambda x: -x[1])
        return fused[:k]
    def kg_VECTOR_GRAPH_SEARCH(
        self,
        query_vector: str,
        query_text: str = None,
        k: int = 15,
        expansion_depth: int = 1,
        min_confidence: float = 0.5,
    ) -> List[Dict[str, Any]]:
        """
        Multi-modal search combining vector similarity, graph expansion, and text relevance

        Args:
            query_vector: Vector query as JSON string
            query_text: Optional text query
            k: Number of final results
            expansion_depth: Graph expansion depth
            min_confidence: Minimum confidence threshold

        Returns:
            List of ranked entities with combined scores
        """
        try:
            # Step 1: Vector search for semantic similarity
            k_vector = min(k * 2, 50)  # Get more candidates for fusion
            vector_results = self.kg_KNN_VEC(query_vector, k=k_vector)
            vector_entities = [entity_id for entity_id, _ in vector_results]

            # Step 2: Graph expansion around vector results
            if vector_entities:
                graph_expansion = self.kg_NEIGHBORHOOD_EXPANSION(
                    vector_entities, expansion_depth, int(min_confidence * 1000)
                )
                expanded_entities = list(
                    set([item["target"] for item in graph_expansion])
                )
            else:
                expanded_entities = []

            # Step 3: Combine with text search if provided
            if query_text:
                text_results = self.kg_TXT(
                    query_text,
                    k=k_vector * 2,
                    min_confidence=int(min_confidence * 1000),
                )
                text_entities = [entity_id for entity_id, _ in text_results]
                all_entities = list(
                    set(vector_entities + expanded_entities + text_entities)
                )
            else:
                all_entities = list(set(vector_entities + expanded_entities))

            # Step 4: Score combination (simplified)
            combined_results = []
            for entity_id in all_entities[:k]:
                # Get scores from different sources
                vector_sim = next(
                    (score for eid, score in vector_results if eid == entity_id), 0.0
                )

                # Simple weighted combination
                combined_score = (
                    vector_sim  # Can be enhanced with graph centrality, text relevance
                )

                combined_results.append(
                    {
                        "entity_id": entity_id,
                        "combined_score": combined_score,
                        "vector_similarity": vector_sim,
                        "in_graph_expansion": entity_id in expanded_entities,
                    }
                )

            # Sort by combined score
            combined_results.sort(key=lambda x: x["combined_score"], reverse=True)
            return combined_results[:k]

        except Exception as e:
            logger.error(f"kg_VECTOR_GRAPH_SEARCH failed: {e}")
            raise
    def vec_create_index(
        self,
        name: str,
        dim: int,
        metric: str = "cosine",
        num_trees: int = 4,
        leaf_size: int = 50,
    ) -> dict:
        result = self._iris_obj().classMethodValue(
            "Graph.KG.VecIndex",
            "Create",
            name,
            str(dim),
            metric,
            str(num_trees),
            str(leaf_size),
        )
        info = json.loads(str(result))
        self._index_registry[name] = "vec"
        return info
    def vec_insert(self, index_name: str, doc_id: str, embedding) -> None:
        vec_json = json.dumps([float(v) for v in embedding])
        self._iris_obj().classMethodVoid(
            "Graph.KG.VecIndex", "InsertJSON", index_name, doc_id, vec_json
        )
    def vec_bulk_insert(self, index_name: str, items: list) -> int:
        batch = [
            {"id": item["id"], "vec": [float(v) for v in item["embedding"]]}
            for item in items
        ]
        batch_json = json.dumps(batch)
        result = self._iris_obj().classMethodValue(
            "Graph.KG.VecIndex", "InsertBatchJSON", index_name, batch_json
        )
        return json.loads(str(result)).get("inserted", 0)
    def vec_build(self, index_name: str) -> dict:
        result = self._iris_obj().classMethodValue(
            "Graph.KG.VecIndex", "Build", index_name
        )
        return json.loads(str(result))
    def vec_search(
        self, index_name: str, query_embedding, k: int = 10, nprobe: int = 8
    ) -> list:
        vec_json = json.dumps([float(v) for v in query_embedding])
        result = self._iris_obj().classMethodValue(
            "Graph.KG.VecIndex", "SearchJSON", index_name, vec_json, k, nprobe
        )
        return json.loads(str(result))
    def vec_search_multi(
        self, index_name: str, query_embeddings: list, k: int = 10, nprobe: int = 8
    ) -> list:
        queries_json = json.dumps([[float(v) for v in q] for q in query_embeddings])
        result = self._iris_obj().classMethodValue(
            "Graph.KG.VecIndex", "SearchMultiJSON", index_name, queries_json, k, nprobe
        )
        return json.loads(str(result))
    def vec_info(self, index_name: str) -> dict:
        result = self._iris_obj().classMethodValue(
            "Graph.KG.VecIndex", "Info", index_name
        )
        info = json.loads(str(result))
        info.setdefault("type", "vec")
        return info
    def vec_drop(self, index_name: str) -> None:
        self._iris_obj().classMethodVoid("Graph.KG.VecIndex", "Drop", index_name)
    def vec_expand(self, index_name: str, seed_id: str, k: int = 5) -> list:
        result = self._iris_obj().classMethodValue(
            "Graph.KG.VecIndex", "SeededVectorExpand", seed_id, index_name, k
        )
        return json.loads(str(result))
    def plaid_build(
        self, name: str, docs: list, n_clusters: int = None, dim: int = 128
    ) -> dict:
        try:
            import numpy as np
            from sklearn.cluster import KMeans
        except ImportError:
            raise ImportError(
                "plaid_build requires numpy and sklearn: pip install numpy scikit-learn"
            )

        all_tokens = []
        doc_token_map = []
        for doc in docs:
            tokens = doc["tokens"]
            for tok_pos, tok in enumerate(tokens):
                all_tokens.append(tok)
                doc_token_map.append(
                    {"docId": doc["id"], "tokPos": tok_pos, "centroid": 0}
                )

        all_tokens_np = np.array(all_tokens, dtype=np.float64)
        K = n_clusters or max(1, int(np.sqrt(len(all_tokens_np))))
        K = min(K, len(all_tokens_np))

        kmeans = KMeans(n_clusters=K, n_init=1, max_iter=20, random_state=42).fit(
            all_tokens_np
        )
        labels = kmeans.labels_.tolist()

        for i, label in enumerate(labels):
            doc_token_map[i]["centroid"] = int(label)

        centroids_json = json.dumps(kmeans.cluster_centers_.tolist())
        docs_json = json.dumps([
            {
                "id": doc["id"],
                "tokens": [[float(v) for v in tok] for tok in doc["tokens"]],
            }
            for doc in docs
        ])
        assignments_json = json.dumps(doc_token_map)

        result = self._iris_obj().classMethodValue(
            "Graph.KG.PLAIDSearch", "Build", name,
            centroids_json, docs_json, assignments_json
        )
        info = json.loads(str(result))
        self._index_registry[name] = "plaid"
        return info
    def plaid_search(
        self, name: str, query_tokens: list, k: int = 10, nprobe: int = 4
    ) -> list:
        tokens_json = json.dumps([[float(v) for v in tok] for tok in query_tokens])
        result = self._iris_obj().classMethodValue(
            "Graph.KG.PLAIDSearch", "Search", name, tokens_json, k, nprobe
        )
        return json.loads(str(result))
    def plaid_insert(self, name: str, doc_id: str, token_embeddings: list) -> None:
        tokens_json = json.dumps([[float(v) for v in tok] for tok in token_embeddings])
        self._iris_obj().classMethodVoid(
            "Graph.KG.PLAIDSearch", "Insert", name, doc_id, tokens_json
        )
    def plaid_info(self, name: str) -> dict:
        result = self._iris_obj().classMethodValue("Graph.KG.PLAIDSearch", "Info", name)
        return json.loads(str(result))
    def plaid_drop(self, name: str) -> None:
        self._iris_obj().classMethodVoid("Graph.KG.PLAIDSearch", "Drop", name)
    def bm25_build(
        self, name: str, text_props: list, k1: float = 1.5, b: float = 0.75
    ) -> dict:
        BM25BuildInput(name=name, text_props=text_props, k1=k1, b=b)
        props_csv = ",".join(text_props)
        result = self._iris_obj().classMethodValue(
            "Graph.KG.BM25Index", "Build", name, props_csv, k1, b
        )
        info = json.loads(str(result))
        self._index_registry[name] = "bm25"
        return info
    def bm25_search(self, name: str, query: str, k: int = 10) -> list:
        BM25SearchInput(name=name, query=query, k=k)
        result = self._iris_obj().classMethodValue(
            "Graph.KG.BM25Index", "Search", name, query, k
        )
        import re as _re
        raw = str(result)
        raw = _re.sub(r'(?<=[:\[,])(\.\d)', r'0\1', raw)
        rows = json.loads(raw)
        return [(r["id"], float(r["score"])) for r in rows]
    def bm25_insert(self, name: str, doc_id: str, text: str) -> bool:
        result = self._iris_obj().classMethodValue(
            "Graph.KG.BM25Index", "Insert", name, doc_id, text
        )
        return bool(int(str(result)))
    def bm25_drop(self, name: str) -> None:
        self._iris_obj().classMethodVoid("Graph.KG.BM25Index", "Drop", name)
    def bm25_info(self, name: str) -> dict:
        result = self._iris_obj().classMethodValue("Graph.KG.BM25Index", "Info", name)
        info = json.loads(str(result))
        info.setdefault("type", "bm25")
        return info
    def ivf_build(
        self,
        name: str,
        nlist: int = 256,
        metric: str = "cosine",
        batch_size: int = 10000,
        build_batch_size: int = 500,
        node_ids: Optional[List[str]] = None,
    ) -> dict:
        IVFBuildInput(name=name, nlist=nlist, metric=metric, batch_size=batch_size, build_batch_size=build_batch_size)
        import sys as _sys
        try:
            import numpy as np
            from sklearn.cluster import MiniBatchKMeans
        except ImportError as _ie:
            np = _sys.modules.get("numpy")
            MiniBatchKMeans = _sys.modules.get("sklearn.cluster.MiniBatchKMeans") or (
                _sys.modules.get("sklearn.cluster") and
                getattr(_sys.modules["sklearn.cluster"], "MiniBatchKMeans", None)
            )
            if np is None or MiniBatchKMeans is None:
                raise ImportError(
                    "ivf_build requires numpy and sklearn: pip install numpy scikit-learn"
                )

        import base64
        import json as _json
        import struct

        cursor = self.conn.cursor()
        if node_ids is not None:
            if not node_ids:
                raise ValueError("ivf_build: node_ids list is empty")
            placeholders = ",".join(["?"] * len(node_ids))
            cursor.execute(
                f"SELECT id, emb FROM Graph_KG.kg_NodeEmbeddings WHERE id IN ({placeholders})",
                node_ids,
            )
        else:
            cursor.execute("SELECT id, emb FROM Graph_KG.kg_NodeEmbeddings")
        rows = cursor.fetchall()
        if not rows:
            raise ValueError("ivf_build: no vectors found in kg_NodeEmbeddings")

        node_ids = []
        vecs = []
        for row in rows:
            nid, emb_val = row[0], row[1]
            if emb_val is None:
                continue
            emb_str = str(emb_val)
            if "," in emb_str:
                vec = [float(v) for v in emb_str.split(",")]
            else:
                raw = base64.b64decode(emb_str)
                dim = len(raw) // 4
                vec = list(struct.unpack(f"{dim}f", raw))
            node_ids.append(nid)
            vecs.append(vec)

        X = np.array(vecs, dtype=np.float32)
        n_nodes, dim = X.shape
        effective_nlist = min(nlist, n_nodes)

        km = MiniBatchKMeans(
            n_clusters=effective_nlist,
            batch_size=batch_size,
            random_state=42,
            n_init=3,
        ).fit(X)

        centroids = km.cluster_centers_.tolist()
        labels = km.labels_.tolist()

        iris_obj = self._iris_obj()

        result = iris_obj.classMethodValue(
            "Graph.KG.IVFIndex",
            "Build",
            name,
            _json.dumps(effective_nlist),
            _json.dumps(metric),
            _json.dumps(centroids),
            "[]",
        )

        for batch_start in range(0, n_nodes, build_batch_size):
            batch = []
            for i in range(batch_start, min(batch_start + build_batch_size, n_nodes)):
                batch.append(
                    {"nodeId": node_ids[i], "cellIdx": int(labels[i]), "vec": vecs[i]}
                )
            iris_obj.classMethodValue(
                "Graph.KG.IVFIndex", "AddBatch", name, _json.dumps(batch)
            )

        iris_obj.classMethodValue("Graph.KG.IVFIndex", "FinalizeIndex", name)
        info = iris_obj.classMethodValue("Graph.KG.IVFIndex", "Info", name)
        result = _json.loads(str(info))
        self._index_registry[name] = "ivf"
        return result
    def ivf_search(self, name: str, query: list, k: int = 10, nprobe: int = 8) -> list:
        VectorSearchInput(name=name, query=query, k=k, nprobe=nprobe)
        query_json = json.dumps([float(v) for v in query])
        result = self._iris_obj().classMethodValue(
            "Graph.KG.IVFIndex", "Search", name, query_json, k, nprobe
        )
        rows = json.loads(str(result))
        return [(r["id"], float(r["score"])) for r in rows]
    def ivf_insert(self, name: str, node_id: str, vector: list) -> int:
        vec_json = json.dumps([float(v) for v in vector])
        cell = int(self._iris_obj().classMethodValue(
            "Graph.KG.IVFIndex", "Insert", name, node_id, vec_json
        ))
        if cell < 0:
            raise ValueError(f"ivf_insert: index '{name}' not found — call ivf_build first")
        return cell
    def ivf_delete(self, name: str, node_id: str) -> bool:
        removed = int(self._iris_obj().classMethodValue(
            "Graph.KG.IVFIndex", "Delete", name, node_id
        ))
        return bool(removed)
    def ivf_drop(self, name: str) -> None:
        self._iris_obj().classMethodVoid("Graph.KG.IVFIndex", "Drop", name)
    def ivf_info(self, name: str) -> dict:
        result = self._iris_obj().classMethodValue("Graph.KG.IVFIndex", "Info", name)
        info = json.loads(str(result))
        if info:
            info.setdefault("type", "ivf")
        return info
