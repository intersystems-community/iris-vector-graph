import os
import time
import uuid
import pytest
from unittest.mock import MagicMock, patch, call

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
PREFIX = f"BT_{uuid.uuid4().hex[:6]}"


class TestSQLTableBridgeUnit:

    def _make_engine(self):
        from iris_vector_graph.engine import IRISGraphEngine
        e = IRISGraphEngine.__new__(IRISGraphEngine)
        e.conn = MagicMock()
        e._table_mapping_cache = None
        e._rel_mapping_cache = None
        e.embedding_dimension = None
        e.embedder = None
        e.embedding_config = None
        e._embedding_function_available = None
        e.capabilities = MagicMock()
        e._arno_available = None
        e._arno_capabilities = {}
        return e

    def test_get_table_mapping_returns_none_for_unmapped(self):
        e = self._make_engine()
        e._table_mapping_cache = {}
        result = e.get_table_mapping("Patient")
        assert result is None

    def test_get_table_mapping_returns_cached_entry(self):
        e = self._make_engine()
        mapping = {"label": "Patient", "sql_table": "T.Pat", "id_column": "PID"}
        e._table_mapping_cache = {"Patient": mapping}
        result = e.get_table_mapping("Patient")
        assert result == mapping

    def _seed_engine_with_mapping(self, label="Patient", sql_table="T.Pat", id_column="PID"):
        e = self._make_engine()
        e._table_mapping_cache = {
            label: {"label": label, "sql_table": sql_table, "id_column": id_column, "prop_columns": None}
        }
        e._rel_mapping_cache = {}
        return e

    def _translate(self, cypher, engine=None, params=None):
        from iris_vector_graph.cypher.translator import translate_to_sql
        from iris_vector_graph.cypher.parser import parse_query
        tree = parse_query(cypher)
        return translate_to_sql(tree, params or {}, engine=engine)

    def test_cypher_mapped_label_uses_mapped_table_not_nodes(self):
        e = self._seed_engine_with_mapping("Patient", "T.Pat", "PID")
        result = self._translate("MATCH (n:Patient) RETURN n.Name", engine=e)
        sql = result.sql if isinstance(result.sql, str) else " ".join(result.sql)
        assert "Graph_KG.nodes" not in sql
        assert "T.Pat" in sql

    def test_cypher_unmapped_label_unchanged(self):
        e = self._make_engine()
        e._table_mapping_cache = {}
        e._rel_mapping_cache = {}
        result = self._translate("MATCH (n:Service) RETURN n.id", engine=e)
        sql = result.sql if isinstance(result.sql, str) else " ".join(result.sql)
        assert "nodes" in sql and "T.Pat" not in sql

    def test_map_sql_table_upsert_updates_existing(self):
        from iris_vector_graph.engine import IRISGraphEngine
        e = self._make_engine()
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = (1,)
        mock_cur.rowcount = 0
        e.conn.cursor.return_value = mock_cur
        e._table_mapping_cache = {
            "Patient": {"label": "Patient", "sql_table": "Old.Table", "id_column": "ID"}
        }
        e.map_sql_table("New.Table", "PatientID", "Patient")
        executed_sqls = [str(c.args[0]) if c.args else "" for c in mock_cur.execute.call_args_list]
        assert any("UPDATE" in s or "INSERT" in s for s in executed_sqls)
        assert e._table_mapping_cache is None

    def test_sql_mapping_wins_over_native_nodes(self):
        e = self._seed_engine_with_mapping("Patient", "T.Pat", "PID")
        result = self._translate("MATCH (n:Patient) RETURN n.id", engine=e)
        sql = result.sql if isinstance(result.sql, str) else " ".join(result.sql)
        assert "T.Pat" in sql
        assert "Graph_KG.nodes" not in sql

    def test_where_filter_routes_to_mapped_column(self):
        e = self._seed_engine_with_mapping("Patient", "T.Pat", "PID")
        result = self._translate(
            "MATCH (n:Patient) WHERE n.MRN = $mrn RETURN n.Name",
            engine=e, params={"mrn": "MRN-001"}
        )
        sql = result.sql if isinstance(result.sql, str) else " ".join(result.sql)
        assert "rdf_props" not in sql
        assert "MRN" in sql or "mrn" in sql.lower()

    def test_fk_relationship_generates_correct_join(self):
        e = self._make_engine()
        e._table_mapping_cache = {
            "Patient": {"label": "Patient", "sql_table": "T.Pat", "id_column": "PatientID", "prop_columns": None},
            "Encounter": {"label": "Encounter", "sql_table": "T.Enc", "id_column": "EncounterID", "prop_columns": None},
        }
        e._rel_mapping_cache = {
            ("Patient", "HAS_ENCOUNTER", "Encounter"): {
                "source_label": "Patient", "predicate": "HAS_ENCOUNTER", "target_label": "Encounter",
                "target_fk": "PatientID", "via_table": None, "via_source": None, "via_target": None,
            }
        }
        result = self._translate(
            "MATCH (p:Patient)-[:HAS_ENCOUNTER]->(e:Encounter) RETURN e.AdmitDate", engine=e)
        sql = result.sql if isinstance(result.sql, str) else " ".join(result.sql)
        assert "rdf_edges" not in sql
        assert "PatientID" in sql
        assert "T.Enc" in sql

    def test_via_table_relationship_generates_correct_join(self):
        e = self._make_engine()
        e._table_mapping_cache = {
            "Patient": {"label": "Patient", "sql_table": "T.Pat", "id_column": "PatientID", "prop_columns": None},
            "Medication": {"label": "Medication", "sql_table": "T.Med", "id_column": "MedicationID", "prop_columns": None},
        }
        e._rel_mapping_cache = {
            ("Patient", "PRESCRIBED", "Medication"): {
                "source_label": "Patient", "predicate": "PRESCRIBED", "target_label": "Medication",
                "target_fk": None, "via_table": "T.PatMed",
                "via_source": "PatientID", "via_target": "MedicationID",
            }
        }
        result = self._translate(
            "MATCH (p:Patient)-[:PRESCRIBED]->(m:Medication) RETURN m.Name", engine=e)
        sql = result.sql if isinstance(result.sql, str) else " ".join(result.sql)
        assert "T.PatMed" in sql
        assert "rdf_edges" not in sql

    def test_mixed_match_routes_mapped_and_native_independently(self):
        e = self._make_engine()
        e._table_mapping_cache = {
            "Patient": {"label": "Patient", "sql_table": "T.Pat", "id_column": "PID", "prop_columns": None},
        }
        e._rel_mapping_cache = {}
        result = self._translate(
            "MATCH (p:Patient)-[:HAS_DOCUMENT]->(d:Document) RETURN p.Name", engine=e)
        sql = result.sql if isinstance(result.sql, str) else " ".join(result.sql)
        assert "T.Pat" in sql
        assert "nodes" in sql

    def test_attach_embeddings_skips_existing_by_id(self):
        from iris_vector_graph.engine import IRISGraphEngine
        e = self._make_engine()
        e._table_mapping_cache = {
            "Patient": {"label": "Patient", "sql_table": "T.Pat", "id_column": "PID", "prop_columns": None}
        }
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [("P001", "Jane Doe"), ("P002", "John Smith")]
        mock_cur.fetchone.side_effect = [(1,), (0,)]
        e.conn.cursor.return_value = mock_cur
        e.embed_text = MagicMock(return_value=[0.1] * 768)
        result = e.attach_embeddings_to_table("Patient", ["Name"], force=False)
        assert result["skipped"] == 1
        assert result["embedded"] == 1

    def test_attach_embeddings_force_reembeds_all(self):
        e = self._make_engine()
        e._table_mapping_cache = {
            "Patient": {"label": "Patient", "sql_table": "T.Pat", "id_column": "PID", "prop_columns": None}
        }
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [("P001", "Jane"), ("P002", "John")]
        e.conn.cursor.return_value = mock_cur
        e.embed_text = MagicMock(return_value=[0.1] * 768)
        result = e.attach_embeddings_to_table("Patient", ["Name"], force=True)
        assert result["embedded"] == 2
        assert result["skipped"] == 0

    def test_attach_embeddings_raises_for_unmapped_label(self):
        from iris_vector_graph.engine import IRISGraphEngine
        e = self._make_engine()
        e._table_mapping_cache = {}
        with pytest.raises(IRISGraphEngine.TableNotMappedError):
            e.attach_embeddings_to_table("Provider", ["Name"])

    def test_list_table_mappings_returns_both(self):
        e = self._make_engine()
        mock_cur = MagicMock()
        mock_cur.fetchall.side_effect = [
            [("Patient", "T.Pat", "PID", None, "2026-01-01"),
             ("Encounter", "T.Enc", "EID", None, "2026-01-01")],
            [("Patient", "HAS_ENCOUNTER", "Encounter", "PatientID", None, None, None)],
        ]
        e.conn.cursor.return_value = mock_cur
        result = e.list_table_mappings()
        assert len(result["nodes"]) == 2
        assert len(result["relationships"]) == 1

    def test_remove_table_mapping_invalidates_cache(self):
        e = self._make_engine()
        e._table_mapping_cache = {"Patient": {"label": "Patient"}}
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = (1,)
        e.conn.cursor.return_value = mock_cur
        e.remove_table_mapping("Patient")
        assert e._table_mapping_cache is None
        delete_calls = [str(c.args[0]) for c in mock_cur.execute.call_args_list if "DELETE" in str(c.args[0])]
        assert len(delete_calls) >= 1


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestSQLTableBridgeE2E:

    PAT = f"BTPatient_{PREFIX}"
    ENC = f"BTEncounter_{PREFIX}"
    MED = f"BTMedication_{PREFIX}"
    MED_JOIN = f"BTPatMed_{PREFIX}"

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        from iris_vector_graph.engine import IRISGraphEngine
        self.conn = iris_connection
        self.engine = IRISGraphEngine(iris_connection)
        self.cur = iris_connection.cursor()
        yield
        for label in [self.PAT, self.ENC, self.MED]:
            try:
                self.engine.remove_table_mapping(label)
            except Exception:
                pass
        for tbl in [f"BridgeTest.{self.PAT}", f"BridgeTest.{self.ENC}",
                    f"BridgeTest.{self.MED}", f"BridgeTest.{self.MED_JOIN}"]:
            try:
                self.cur.execute(f"DROP TABLE {tbl}")
                self.conn.commit()
            except Exception:
                pass

    def _create_patient_table(self):
        tbl = f"BridgeTest.{self.PAT}"
        try:
            self.cur.execute(f"DROP TABLE {tbl}")
        except Exception:
            pass
        self.cur.execute(
            f"CREATE TABLE {tbl} (PatientID VARCHAR(20) PRIMARY KEY, Name VARCHAR(100), MRN VARCHAR(20))"
        )
        self.cur.execute(f"INSERT INTO {tbl} VALUES ('P001', 'Jane Doe', 'MRN-001')")
        self.cur.execute(f"INSERT INTO {tbl} VALUES ('P002', 'John Smith', 'MRN-002')")
        self.conn.commit()
        return tbl

    def test_cypher_returns_same_as_direct_sql(self):
        tbl = self._create_patient_table()
        self.engine.map_sql_table(tbl, "PatientID", self.PAT)
        result = self.engine.execute_cypher(
            f"MATCH (n:{self.PAT}) WHERE n.MRN = $mrn RETURN n.Name",
            {"mrn": "MRN-001"}
        )
        self.cur.execute(f"SELECT Name FROM {tbl} WHERE MRN = ?", ["MRN-001"])
        sql_row = self.cur.fetchone()
        assert result["rows"]
        assert result["rows"][0][0] == sql_row[0]

    def test_zero_writes_to_graph_kg_nodes(self):
        tbl = self._create_patient_table()
        self.engine.map_sql_table(tbl, "PatientID", self.PAT)
        self.engine.execute_cypher(f"MATCH (n:{self.PAT}) RETURN n.Name")
        self.cur.execute(
            f"SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id LIKE ?", [f"{self.PAT}:%"]
        )
        count = int(self.cur.fetchone()[0])
        assert count == 0

    def test_cypher_count_matches_sql_count(self):
        tbl = self._create_patient_table()
        self.engine.map_sql_table(tbl, "PatientID", self.PAT)
        result = self.engine.execute_cypher(f"MATCH (n:{self.PAT}) RETURN count(n)")
        self.cur.execute(f"SELECT COUNT(*) FROM {tbl}")
        sql_count = int(self.cur.fetchone()[0])
        assert int(result["rows"][0][0]) == sql_count

    def test_fk_traversal_matches_sql_join(self):
        pat_tbl = self._create_patient_table()
        enc_tbl = f"BridgeTest.{self.ENC}"
        try:
            self.cur.execute(f"DROP TABLE {enc_tbl}")
        except Exception:
            pass
        self.cur.execute(
            f"CREATE TABLE {enc_tbl} (EncounterID VARCHAR(20) PRIMARY KEY, PatientID VARCHAR(20), AdmitDate VARCHAR(20))"
        )
        self.cur.execute(f"INSERT INTO {enc_tbl} VALUES ('E001', 'P001', '2026-01-15')")
        self.conn.commit()
        self.engine.map_sql_table(pat_tbl, "PatientID", self.PAT)
        self.engine.map_sql_table(enc_tbl, "EncounterID", self.ENC)
        self.engine.map_sql_relationship(self.PAT, "HAS_ENCOUNTER", self.ENC, target_fk="PatientID")
        result = self.engine.execute_cypher(
            f"MATCH (p:{self.PAT})-[:HAS_ENCOUNTER]->(e:{self.ENC}) WHERE p.MRN = $mrn RETURN e.AdmitDate",
            {"mrn": "MRN-001"}
        )
        assert result["rows"]
        assert result["rows"][0][0] == "2026-01-15"

    def test_map_sql_table_on_missing_table_raises_clear_error(self):
        with pytest.raises(ValueError, match="not found"):
            self.engine.map_sql_table("NonExistent.Table999", "SomeID", "GhostLabel")

    def test_list_and_remove_mapping(self):
        tbl = self._create_patient_table()
        self.engine.map_sql_table(tbl, "PatientID", self.PAT)
        mappings = self.engine.list_table_mappings()
        labels = [n["label"] for n in mappings["nodes"]]
        assert self.PAT in labels
        self.engine.remove_table_mapping(self.PAT)
        mappings2 = self.engine.list_table_mappings()
        labels2 = [n["label"] for n in mappings2["nodes"]]
        assert self.PAT not in labels2
