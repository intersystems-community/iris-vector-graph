from typing import List, Dict, Any, Optional
import json
import logging

from iris_vector_graph.schema import GraphSchema
from iris_vector_graph.cypher.translator import _table

logger = logging.getLogger(__name__)


class EmbeddingsMixin:
    """Embedding and vector storage mixin for IRISGraphEngine.
    
    Provides text embedding, node/edge embedding, vector search integration,
    and asynchronous embedding queue management."""

    def embed_text(self, text: str) -> List[float]:
        """
        Converts text to a vector embedding using the best available method.
        Order of preference:
        1. Native IRIS EMBEDDING() if embedding_config is set.
        2. Configured Python embedder.
        3. Default SentenceTransformer fallback.
        """
        # 1. Native IRIS embedding if available
        if self.embedding_config and self._probe_embedding_support():
            cursor = self.conn.cursor()
            try:
                # Call SQL EMBEDDING function
                cursor.execute("SELECT EMBEDDING(?, ?)", [text, self.embedding_config])
                result = cursor.fetchone()
                if result:
                    # IRIS returns vector as string or list depending on driver version
                    val = result[0]
                    if isinstance(val, str):
                        return [float(x) for x in val.strip("[]").split(",")]
                    return list(val)
            except Exception as e:
                logger.warning(
                    f"Native IRIS EMBEDDING failed for config '{self.embedding_config}': {e}. Falling back to Python."
                )
            finally:
                cursor.close()

        # 2. Python-side embedding
        if not self.embedder:
            try:
                import logging as _logging
                try:
                    import transformers as _tf
                    _tf.logging.set_verbosity_error()
                    _logging.getLogger("safetensors").setLevel(_logging.ERROR)
                except Exception:
                    pass
                self.embedder = _load_sentence_transformer("all-MiniLM-L6-v2")
                logger.info("Auto-initialized SentenceTransformer('all-MiniLM-L6-v2')")
            except ImportError:
                raise RuntimeError(
                    "No embedder or embedding_config configured, and 'sentence-transformers' not installed. "
                    "Pass an embedder/embedding_config to IRISGraphEngine or install sentence-transformers."
                )

        if hasattr(self.embedder, "encode"):
            return self.embedder.encode(text).tolist()
        if hasattr(self.embedder, "embed"):
            return self.embedder.embed(text)
        if callable(self.embedder):
            return self.embedder(text)

        raise TypeError(
            f"Configured embedder {type(self.embedder)} is not a supported type (must have encode/embed or be callable)"
        )


    def _get_embedding_dimension(self) -> int:
        """
        Get the vector embedding dimension, either from initialization or auto-detection.
        Prioritizes database detection if the schema exists.
        """
        cursor = self.conn.cursor()

        # 1. Try to detect from DB first
        dim = GraphSchema.get_embedding_dimension(cursor)
        if dim:
            return int(dim)

        # 2. Fallback to instance variable if DB detection fails or table doesn't exist
        if self.embedding_dimension is not None:
            return self.embedding_dimension

        raise ValueError(
            "Embedding dimension could not be determined. Please provide it during IRISGraphEngine initialization."
        )


    def _probe_embedding_support(self) -> bool:
        """Probe whether the IRIS EMBEDDING() SQL function is available (IRIS 2024.3+).

        Result is cached per engine instance. Probe strategy:
        - Execute ``SELECT EMBEDDING('__ivg_probe__', '__nonexistent_config__')``
        - If the error message contains 'not found' / 'does not exist' → function absent → False
        - Any other error (e.g. config missing) → function present → True
        - No error → True
        """
        if self._embedding_function_available is not None:
            return self._embedding_function_available
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT EMBEDDING('__ivg_probe__', '__nonexistent_config__')"
            )
            self._embedding_function_available = True
        except Exception as e:
            err = str(e).lower()
            if "unknown function" in err or "not a recognized" in err:
                self._embedding_function_available = False
            else:
                self._embedding_function_available = True
        finally:
            try:
                cursor.close()
            except Exception:
                pass
        return bool(self._embedding_function_available)


    def _probe_native_vec(self) -> bool:
        if self._native_vec_available is not None:
            return self._native_vec_available
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT TOP 1 VECTOR_COSINE(emb, TO_VECTOR('[0]', DOUBLE)) "
                f"FROM {_table('kg_NodeEmbeddings')} WHERE 1=0"
            )
            self._native_vec_available = True
        except Exception as e:
            err = str(e).lower()
            self._native_vec_available = not (
                "unknown function" in err
                or "not a recognized" in err
                or "not found" in err
                or "no such" in err
            )
        finally:
            try:
                cursor.close()
            except Exception:
                pass
        return bool(self._native_vec_available)


    def get_unembedded_nodes(self) -> List[str]:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT n.node_id FROM Graph_KG.nodes n "
                "LEFT JOIN Graph_KG.kg_NodeEmbeddings e ON e.id = n.node_id "
                "WHERE e.id IS NULL"
            )
            return [row[0] for row in cursor.fetchall()]
        except Exception:
            return []


    def store_embedding(
        self,
        node_id: str,
        embedding: List[float],
        metadata: Optional[Dict[str, Any]] = None,
        dtype: Optional[str] = None,
    ) -> bool:
        _dtype = (dtype or self.vector_dtype).upper()
        self._assert_node_exists(node_id)

        try:
            dim = self._get_embedding_dimension()
        except ValueError:
            # Infer dimension from input if auto-detection fails
            dim = len(embedding)
            self.embedding_dimension = dim
            logger.warning(
                f"Embedding dimension auto-detection failed. Inferred dimension {dim} from input."
            )

        if len(embedding) != dim:
            raise ValueError(
                f"Embedding dimension mismatch: expected {dim}, got {len(embedding)}"
            )

        cursor = self.conn.cursor()
        emb_str = ",".join(str(x) for x in embedding)
        meta_json = json.dumps(metadata) if metadata else None

        try:
            cursor.execute(
                f"DELETE FROM {_table('kg_NodeEmbeddings')} WHERE id = ?", [node_id]
            )
        except Exception:
            pass
        cursor.execute(
            f"INSERT INTO {_table('kg_NodeEmbeddings')} (id, emb, metadata) VALUES (?, TO_VECTOR('{emb_str}', {self.vector_dtype}), ?)",
            [node_id, meta_json],
        )
        self.conn.commit()
        return True


    def store_embeddings(self, items: List[Dict[str, Any]], dtype: Optional[str] = None) -> bool:
        _dtype = (dtype or self.vector_dtype).upper()
        if not items:
            return True

        try:
            dim = self._get_embedding_dimension()
        except ValueError:
            # Infer dimension from first item if auto-detection fails
            dim = len(items[0]["embedding"])
            self.embedding_dimension = dim
            logger.warning(
                f"Embedding dimension auto-detection failed. Inferred dimension {dim} from input."
            )

        for item in items:
            node_id = item["node_id"]
            embedding = item["embedding"]
            if len(embedding) != dim:
                raise ValueError(
                    f"Embedding dimension mismatch: expected {dim}, got {len(embedding)}"
                )
            self._assert_node_exists(node_id)

        cursor = self.conn.cursor()
        cursor.execute("START TRANSACTION")
        try:
            for item in items:
                node_id = item["node_id"]
                embedding = item["embedding"]
                metadata = item.get("metadata")

                emb_str = ",".join(str(x) for x in embedding)
                meta_json = json.dumps(metadata) if metadata else None

                try:
                    cursor.execute(
                        f"DELETE FROM {_table('kg_NodeEmbeddings')} WHERE id = ?", [node_id]
                    )
                except Exception:
                    pass
                cursor.execute(
            f"INSERT INTO {_table('kg_NodeEmbeddings')} (id, emb, metadata) VALUES (?, TO_VECTOR('{emb_str}', {_dtype}), ?)",
                    [node_id, meta_json],
                )
            cursor.execute("COMMIT")
            return True
        except Exception:
            cursor.execute("ROLLBACK")
            raise


    def embed_nodes(
        self,
        model=None,
        text_fn=None,
        batch_size: int = 500,
        force: bool = False,
        progress_callback=None,
        label: str = None,
        node_ids: List[str] = None,
        exclude_pattern: str = None,
        missing_only: bool = False,
    ) -> dict:
        from iris_vector_graph.embed_selector import EmbedSelector, build_node_where
        from iris_vector_graph.cypher import get_schema_prefix

        sel = EmbedSelector(
            label=label,
            node_ids=node_ids,
            exclude_pattern=exclude_pattern,
            missing_only=missing_only,
        )
        where = build_node_where(sel, schema_prefix=get_schema_prefix())

        orig_embedder = self.embedder
        if model is not None:
            if isinstance(model, str):
                self.embedder = _load_sentence_transformer(model)
            else:
                self.embedder = model

        try:
            cursor = self.conn.cursor()

            where_clause = f"WHERE {where}" if where else ""
            cursor.execute(f"SELECT node_id FROM {_table('nodes')} {where_clause}")
            all_node_ids = [row[0] for row in cursor.fetchall()]
            n_total = len(all_node_ids)

            if not force and not missing_only:
                cursor.execute(f"SELECT id FROM {_table('kg_NodeEmbeddings')}")
                already_embedded = {row[0] for row in cursor.fetchall()}
                to_embed = [nid for nid in all_node_ids if nid not in already_embedded]
            else:
                to_embed = all_node_ids

            n_to_embed = len(to_embed)
            embedded = skipped = errors = 0

            for batch_start in range(0, n_to_embed, batch_size):
                batch_ids = to_embed[batch_start : batch_start + batch_size]

                placeholders = ", ".join("?" * len(batch_ids))
                cursor.execute(
                    f'SELECT s, "key", val FROM {_table("rdf_props")} WHERE s IN ({placeholders})',
                    batch_ids,
                )
                props_by_node: Dict[str, Dict[str, Any]] = {}
                for row in cursor.fetchall():
                    node_id, key, val = row[0], row[1], row[2]
                    props_by_node.setdefault(node_id, {})[key] = val

                texts: List[str] = []
                valid_ids: List[str] = []
                for node_id in batch_ids:
                    props = props_by_node.get(node_id, {})
                    if text_fn is not None:
                        try:
                            text = text_fn(node_id, props)
                        except Exception as ex:
                            logger.warning(
                                f"embed_nodes: text_fn raised for {node_id}: {ex}"
                            )
                            errors += 1
                            continue
                    else:
                        text = node_id

                    if not text:
                        skipped += 1
                        continue

                    texts.append(text)
                    valid_ids.append(node_id)

                if not texts:
                    self.conn.commit()
                    n_done = batch_start + len(batch_ids)
                    if progress_callback:
                        progress_callback(n_done, n_to_embed)
                    continue

                try:
                    use_batch = False
                    if not self.embedding_config and self.embedder is not None:
                        try:
                            use_batch = _is_sentence_transformer(self.embedder)
                        except ImportError:
                            pass
                    if use_batch:
                        raw = self.embedder.encode(
                            texts, batch_size=min(64, len(texts)), show_progress_bar=False
                        )
                        embeddings = [row.tolist() for row in raw]
                    else:
                        embeddings = [self.embed_text(t) for t in texts]
                except Exception as ex:
                    logger.warning(f"embed_nodes: batch encode failed, falling back per-node: {ex}")
                    embeddings = []
                    for t in texts:
                        try:
                            embeddings.append(self.embed_text(t))
                        except Exception as ex2:
                            logger.warning(f"embed_nodes: embed_text failed: {ex2}")
                            embeddings.append(None)

                insert_params = []
                for node_id, emb in zip(valid_ids, embeddings):
                    if emb is None:
                        errors += 1
                        continue
                    emb_str = ",".join(str(x) for x in emb)
                    insert_params.append((node_id, emb_str))

                if insert_params:
                    ids_to_delete = [p[0] for p in insert_params]
                    del_placeholders = ", ".join("?" * len(ids_to_delete))
                    try:
                        cursor.execute(
                            f"DELETE FROM {_table('kg_NodeEmbeddings')} WHERE id IN ({del_placeholders})",
                            ids_to_delete,
                        )
                    except Exception:
                        pass
                    for node_id, emb_str in insert_params:
                        try:
                            cursor.execute(
                                f"INSERT INTO {_table('kg_NodeEmbeddings')} (id, emb) VALUES (?, TO_VECTOR('{emb_str}', {self.vector_dtype}))",
                                [node_id],
                            )
                            embedded += 1
                        except Exception as ex2:
                            logger.warning(f"embed_nodes: insert failed for {node_id}: {ex2}")
                            errors += 1

                self.conn.commit()
                n_done = batch_start + len(batch_ids)
                logger.info(
                    f"embed_nodes: {n_done}/{n_to_embed} processed ({embedded} embedded)"
                )
                if progress_callback:
                    progress_callback(n_done, n_to_embed)

            skipped += n_total - n_to_embed
            return {
                "embedded": embedded,
                "skipped": skipped,
                "errors": errors,
                "total": n_total,
            }
        finally:
            self.embedder = orig_embedder


    def embed_edges(
        self,
        model=None,
        text_fn=None,
        batch_size: int = 500,
        force: bool = False,
        progress_callback=None,
        predicate: str = None,
        source_label: str = None,
        target_label: str = None,
        exclude_pattern: str = None,
        missing_only: bool = False,
    ) -> dict:
        from iris_vector_graph.embed_selector import EmbedSelector, build_edge_where
        from iris_vector_graph.cypher import get_schema_prefix

        sel = EmbedSelector(
            predicate=predicate,
            source_label=source_label,
            target_label=target_label,
            exclude_pattern=exclude_pattern,
            missing_only=missing_only,
        )
        where = build_edge_where(sel, schema_prefix=get_schema_prefix())

        orig_embedder = self.embedder
        if model is not None:
            if isinstance(model, str):
                self.embedder = _load_sentence_transformer(model)
            else:
                self.embedder = model

        try:
            cursor = self.conn.cursor()

            where_clause = f"WHERE {where}" if where else ""
            cursor.execute(
                f"SELECT s, p, o_id FROM {_table('rdf_edges')} {where_clause}"
            )
            all_edges = [(row[0], row[1], row[2]) for row in cursor.fetchall()]
            n_total = len(all_edges)

            if not force:
                cursor.execute(
                    f"SELECT s, p, o_id FROM {_table('kg_EdgeEmbeddings')}"
                )
                already_embedded = {
                    (row[0], row[1], row[2]) for row in cursor.fetchall()
                }
                to_embed = [e for e in all_edges if e not in already_embedded]
            else:
                to_embed = all_edges

            n_to_embed = len(to_embed)
            embedded = skipped = errors = 0

            for batch_start in range(0, n_to_embed, batch_size):
                batch = to_embed[batch_start : batch_start + batch_size]

                texts: List[str] = []
                valid_edges: List[tuple] = []
                for s, p, o_id in batch:
                    if text_fn is not None:
                        try:
                            text = text_fn(s, p, o_id)
                        except Exception as ex:
                            logger.warning(
                                "embed_edges: text_fn raised for (%s, %s, %s): %s",
                                s, p, o_id, ex,
                            )
                            errors += 1
                            continue
                    else:
                        text = f"{s} {p} {o_id}"

                    if not text:
                        skipped += 1
                        continue

                    texts.append(text)
                    valid_edges.append((s, p, o_id))

                if not texts:
                    self.conn.commit()
                    n_done = batch_start + len(batch)
                    if progress_callback:
                        progress_callback(n_done, n_to_embed)
                    continue

                try:
                    use_batch = False
                    if not self.embedding_config and self.embedder is not None:
                        try:
                            use_batch = _is_sentence_transformer(self.embedder)
                        except ImportError:
                            pass
                    if use_batch:
                        raw = self.embedder.encode(texts, batch_size=min(64, len(texts)), show_progress_bar=False)
                        embeddings = [row.tolist() for row in raw]
                    else:
                        embeddings = [self.embed_text(t) for t in texts]
                except Exception as ex:
                    logger.warning(f"embed_edges: batch encode failed, falling back per-edge: {ex}")
                    embeddings = []
                    for t in texts:
                        try:
                            embeddings.append(self.embed_text(t))
                        except Exception as ex2:
                            logger.warning(f"embed_edges: embed_text failed: {ex2}")
                            embeddings.append(None)

                insert_params = []
                for (s, p, o_id), emb in zip(valid_edges, embeddings):
                    if emb is None:
                        errors += 1
                        continue
                    emb_str = ",".join(str(x) for x in emb)
                    insert_params.append((s, p, o_id, emb_str))

                if insert_params:
                    try:
                        del_params = [(p[0], p[1], p[2]) for p in insert_params]
                        for s, p, o_id in del_params:
                            try:
                                cursor.execute(
                                    f"DELETE FROM {_table('kg_EdgeEmbeddings')} "
                                    "WHERE s=? AND p=? AND o_id=?",
                                    [s, p, o_id],
                                )
                            except Exception:
                                pass
                    except Exception:
                        pass
                    for s, p, o_id, emb_str in insert_params:
                        try:
                            cursor.execute(
                                f"INSERT INTO {_table('kg_EdgeEmbeddings')} "
                                f"(s, p, o_id, emb) VALUES (?, ?, ?, TO_VECTOR('{emb_str}', {self.vector_dtype}))",
                                [s, p, o_id],
                            )
                            embedded += 1
                        except Exception as ex2:
                            logger.warning(
                                "embed_edges: insert failed for (%s, %s, %s): %s",
                                s, p, o_id, ex2,
                            )
                            errors += 1

                self.conn.commit()
                n_done = batch_start + len(batch)
                logger.info(
                    "embed_edges: %d/%d processed (%d embedded)",
                    n_done, n_to_embed, embedded,
                )
                if progress_callback:
                    progress_callback(n_done, n_to_embed)

            skipped += n_total - n_to_embed
            return {
                "embedded": embedded,
                "skipped": skipped,
                "errors": errors,
                "total": n_total,
            }
        finally:
            self.embedder = orig_embedder


    def get_embedding(self, node_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve embedding for a node.

        Args:
            node_id: The node ID to get embedding for

        Returns:
            Dict with 'id', 'embedding' (as list of floats), and 'metadata' (if present)
            None if node has no embedding
        """
        cursor = self.conn.cursor()
        cursor.execute(
            f"SELECT id, emb, metadata FROM {_table('kg_NodeEmbeddings')} WHERE id = ?",
            [node_id],
        )
        row = cursor.fetchone()
        if not row:
            return None

        node_id, emb_csv, metadata_json = row
        embedding = [float(x) for x in emb_csv.split(",")] if emb_csv else []
        metadata = json.loads(metadata_json) if metadata_json else None

        result = {"id": node_id, "embedding": embedding}
        if metadata:
            result["metadata"] = metadata
        return result


    def get_embeddings(self, node_ids: List[str]) -> List[Dict[str, Any]]:
        """
        Retrieve embeddings for multiple nodes.

        Args:
            node_ids: List of node IDs

        Returns:
            List of dicts with 'id', 'embedding', and 'metadata' for nodes that have embeddings
        """
        if not node_ids:
            return []

        cursor = self.conn.cursor()
        placeholders = ",".join(["?"] * len(node_ids))
        cursor.execute(
            f"SELECT id, emb, metadata FROM {_table('kg_NodeEmbeddings')} WHERE id IN ({placeholders})",
            node_ids,
        )

        results = []
        for row in cursor.fetchall():
            node_id, emb_csv, metadata_json = row
            embedding = [float(x) for x in emb_csv.split(",")] if emb_csv else []
            metadata = json.loads(metadata_json) if metadata_json else None

            result = {"id": node_id, "embedding": embedding}
            if metadata:
                result["metadata"] = metadata
            results.append(result)

        return results


    def enqueue_for_embedding(
        self,
        node_ids: List[str],
        embedding_config: str = "",
    ) -> int:
        try:
            import json as _json
            ids_json = _json.dumps(node_ids)
            result = self._call_classmethod(
                "Graph.KG.EmbedQueue", "BulkEnqueue",
                ids_json, embedding_config,
            )
            return int(str(result))
        except Exception as e:
            logger.warning("enqueue_for_embedding failed: %s", e)
            return 0


    def process_embed_queue(self, batch_size: int = 100) -> dict:
        try:
            import json as _json
            result_json = str(self._call_classmethod(
                "Graph.KG.EmbedQueue", "ProcessBatch",
                batch_size, 30,
            ))
            return _json.loads(result_json)
        except Exception as e:
            logger.warning("process_embed_queue failed: %s", e)
            return {"processed": 0, "errors": 0}


    def embed_queue_pending(self) -> int:
        try:
            return int(str(self._call_classmethod("Graph.KG.EmbedQueue", "PendingCount")))
        except Exception:
            return 0


    def start_background_embedding(self, batch_size: int = 100) -> str:
        try:
            return str(self._call_classmethod("Graph.KG.EmbedQueue", "StartBackgroundTask", batch_size))
        except Exception as e:
            logger.warning("start_background_embedding failed: %s", e)
            return ""

    # ── BM25Index: pure ObjectScript lexical search ──


    def embedding_count(self) -> int:
        cursor = self.conn.cursor()
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {_table('kg_NodeEmbeddings')}")
            row = cursor.fetchone()
            return int(row[0]) if row else 0
        except Exception:
            return 0
        finally:
            cursor.close()
