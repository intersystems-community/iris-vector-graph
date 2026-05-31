import json
import logging
from typing import Optional, Dict, Any, List

from iris_vector_graph.cypher.translator import _table

logger = logging.getLogger(__name__)


class FhirMixin:
    """FHIR and SQL table bridge mixin for IRISGraphEngine.
    
    Provides bidirectional mapping between clinical FHIR data and the knowledge graph,
    enabling hybrid queries across standard clinical SQL tables and the KG.
    """

    def get_table_mapping(self, label: str) -> Optional[dict]:
        if self._table_mapping_cache is None:
            self._load_table_mapping_cache()
        return (
            self._table_mapping_cache.get(label) if self._table_mapping_cache else None
        )

    def _load_table_mapping_cache(self) -> None:
        self._table_mapping_cache = {}
        try:
            cur = self.conn.cursor()
            cur.execute(
                "SELECT label, sql_table, id_column, prop_columns FROM Graph_KG.table_mappings"
            )
            for row in cur.fetchall():
                self._table_mapping_cache[row[0]] = {
                    "label": row[0],
                    "sql_table": row[1],
                    "id_column": row[2],
                    "prop_columns": row[3],
                }
        except Exception:
            self._table_mapping_cache = {}

    def get_rel_mapping(
        self, source_label: str, predicate: str, target_label: str
    ) -> Optional[dict]:
        if self._rel_mapping_cache is None:
            self._load_rel_mapping_cache()
        key = (source_label, predicate, target_label)
        return self._rel_mapping_cache.get(key) if self._rel_mapping_cache else None

    def _load_rel_mapping_cache(self) -> None:
        self._rel_mapping_cache = {}
        try:
            cur = self.conn.cursor()
            cur.execute(
                "SELECT source_label, predicate, target_label, target_fk, "
                "via_table, via_source, via_target FROM Graph_KG.relationship_mappings"
            )
            for row in cur.fetchall():
                key = (row[0], row[1], row[2])
                self._rel_mapping_cache[key] = {
                    "source_label": row[0],
                    "predicate": row[1],
                    "target_label": row[2],
                    "target_fk": row[3],
                    "via_table": row[4],
                    "via_source": row[5],
                    "via_target": row[6],
                }
        except Exception:
            self._rel_mapping_cache = {}

    class TableNotMappedError(ValueError):
        pass

    def map_sql_table(
        self, table: str, id_column: str, label: str, property_columns=None
    ) -> dict:
        from iris_vector_graph.security import sanitize_identifier

        sanitize_identifier(table)
        sanitize_identifier(id_column)
        sanitize_identifier(label)
        cur = self.conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? AND COLUMN_NAME = ?",
            [
                table.split(".")[0] if "." in table else "USER",
                table.split(".")[-1],
                id_column,
            ],
        )
        row = cur.fetchone()
        if not row or int(row[0]) == 0:
            raise ValueError(
                f"Table '{table}' or column '{id_column}' not found. "
                f"Verify the table exists and id_column is correct."
            )
        prop_json = json.dumps(property_columns) if property_columns else None
        cur.execute(
            "UPDATE Graph_KG.table_mappings SET sql_table=?, id_column=?, prop_columns=? WHERE label=?",
            [table, id_column, prop_json, label],
        )
        if cur.rowcount == 0:
            cur.execute(
                "INSERT INTO Graph_KG.table_mappings (label, sql_table, id_column, prop_columns) VALUES (?,?,?,?)",
                [label, table, id_column, prop_json],
            )
        self.conn.commit()
        self._invalidate_mapping_cache()
        return {
            "label": label,
            "sql_table": table,
            "id_column": id_column,
            "prop_columns": property_columns,
        }

    def map_sql_relationship(
        self,
        source_label: str,
        predicate: str,
        target_label: str,
        target_fk: str = None,
        via_table: str = None,
        via_source: str = None,
        via_target: str = None,
    ) -> dict:
        if not target_fk and not via_table:
            raise ValueError("Either target_fk or via_table must be provided.")
        if not self.get_table_mapping(source_label):
            raise ValueError(
                f"Source label '{source_label}' is not registered. Call map_sql_table first."
            )
        if not self.get_table_mapping(target_label):
            raise ValueError(
                f"Target label '{target_label}' is not registered. Call map_sql_table first."
            )
        cur = self.conn.cursor()
        cur.execute(
            "UPDATE Graph_KG.relationship_mappings SET target_fk=?, via_table=?, via_source=?, via_target=? "
            "WHERE source_label=? AND predicate=? AND target_label=?",
            [
                target_fk,
                via_table,
                via_source,
                via_target,
                source_label,
                predicate,
                target_label,
            ],
        )
        if cur.rowcount == 0:
            cur.execute(
                "INSERT INTO Graph_KG.relationship_mappings "
                "(source_label, predicate, target_label, target_fk, via_table, via_source, via_target) "
                "VALUES (?,?,?,?,?,?,?)",
                [
                    source_label,
                    predicate,
                    target_label,
                    target_fk,
                    via_table,
                    via_source,
                    via_target,
                ],
            )
        self.conn.commit()
        self._invalidate_mapping_cache()
        return {
            "source_label": source_label,
            "predicate": predicate,
            "target_label": target_label,
            "target_fk": target_fk,
            "via_table": via_table,
        }

    def list_table_mappings(self) -> dict:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT label, sql_table, id_column, prop_columns, registered_at FROM Graph_KG.table_mappings"
        )
        nodes = [
            {
                "label": r[0],
                "sql_table": r[1],
                "id_column": r[2],
                "prop_columns": r[3],
                "registered_at": str(r[4]),
            }
            for r in cur.fetchall()
        ]
        cur.execute(
            "SELECT source_label, predicate, target_label, target_fk, via_table, via_source, via_target "
            "FROM Graph_KG.relationship_mappings"
        )
        rels = [
            {
                "source_label": r[0],
                "predicate": r[1],
                "target_label": r[2],
                "target_fk": r[3],
                "via_table": r[4],
                "via_source": r[5],
                "via_target": r[6],
            }
            for r in cur.fetchall()
        ]
        return {"nodes": nodes, "relationships": rels}

    def remove_table_mapping(self, label: str) -> None:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM Graph_KG.table_mappings WHERE label=?", [label]
        )
        if int(cur.fetchone()[0]) == 0:
            raise ValueError(f"Label '{label}' not found in table_mappings.")
        cur.execute("DELETE FROM Graph_KG.table_mappings WHERE label=?", [label])
        cur.execute(
            "DELETE FROM Graph_KG.relationship_mappings WHERE source_label=? OR target_label=?",
            [label, label],
        )
        self.conn.commit()
        self._invalidate_mapping_cache()

    def reload_table_mappings(self) -> None:
        self._invalidate_mapping_cache()
        self._load_table_mapping_cache()
        self._load_rel_mapping_cache()

    def attach_embeddings_to_table(
        self,
        label: str,
        text_columns: list,
        batch_size: int = 1000,
        force: bool = False,
        progress_callback=None,
    ) -> dict:
        mapping = self.get_table_mapping(label)
        if not mapping:
            raise IRISGraphEngine.TableNotMappedError(
                f"Label '{label}' is not registered. Call map_sql_table first."
            )
        sql_table = mapping["sql_table"]
        id_col = mapping["id_column"]
        cur = self.conn.cursor()
        cur.execute(f"SELECT {id_col}, {', '.join(text_columns)} FROM {sql_table}")
        all_rows = cur.fetchall()
        n_total = len(all_rows)
        embedded = 0
        skipped = 0
        for batch_start in range(0, n_total, batch_size):
            batch = all_rows[batch_start : batch_start + batch_size]
            for row in batch:
                row_id = row[0]
                node_id = f"{label}:{row_id}"
                if not force:
                    cur.execute(
                        "SELECT COUNT(*) FROM Graph_KG.kg_NodeEmbeddings WHERE id=?",
                        [node_id],
                    )
                    if int(cur.fetchone()[0]) > 0:
                        skipped += 1
                        continue
                text = " ".join(
                    str(row[i + 1])
                    for i in range(len(text_columns))
                    if row[i + 1] is not None
                )
                try:
                    emb = self.embed_text(text)
                    emb_str = ",".join(str(x) for x in emb)
                    cur.execute(
                        "DELETE FROM Graph_KG.kg_NodeEmbeddings WHERE id=?", [node_id]
                    )
                    cur.execute(
                        f"INSERT INTO Graph_KG.kg_NodeEmbeddings (id, emb) VALUES (?, TO_VECTOR(?, {self.vector_dtype}))",
                        [node_id, emb_str],
                    )
                    embedded += 1
                except Exception as ex:
                    logger.warning(
                        f"attach_embeddings_to_table: failed to embed {node_id}: {ex}"
                    )
            self.conn.commit()
            n_done = batch_start + len(batch)
            logger.info(
                f"attach_embeddings_to_table: {n_done}/{n_total} rows processed"
            )
            if progress_callback:
                progress_callback(n_done, n_total)
        return {"embedded": embedded, "skipped": skipped, "total": n_total}

    def get_kg_anchors(
        self, icd_codes: List[str], bridge_type: str = "icd10_to_mesh"
    ) -> List[str]:
        if not icd_codes:
            return []
        _IN_CHUNK = 499
        results: list = []
        cursor = self.conn.cursor()
        try:
            for i in range(0, len(icd_codes), _IN_CHUNK):
                chunk = icd_codes[i : i + _IN_CHUNK]
                placeholders = ", ".join(["?"] * len(chunk))
                sql = (
                    f"SELECT DISTINCT b.kg_node_id "
                    f"FROM {_table('fhir_bridges')} b "
                    f"JOIN {_table('nodes')} n ON n.node_id = b.kg_node_id "
                    f"WHERE b.fhir_code IN ({placeholders}) "
                    f"AND b.bridge_type = ?"
                )
                cursor.execute(sql, chunk + [bridge_type])
                results.extend(row[0] for row in cursor.fetchall())
            return results
        except Exception as e:
            logger.warning(f"get_kg_anchors failed: {e}")
            return []
        finally:
            cursor.close()

