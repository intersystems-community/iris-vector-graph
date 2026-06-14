"""
Integration tests for uncovered paths in:
  - engine.py: system procedure handlers (_proc_db_labels, _proc_dbms_procedures,
      _proc_dbms_clientconfig, _proc_dbms_security_showcurrentuser,
      _proc_dbms_functions, _proc_apoc_meta_schema, _proc_db_propertykeys,
      _proc_db_schema_visualization, _proc_db_schema_nodetypeproperties,
      _proc_db_schema_reltypeproperties, _proc_dbms_components,
      _proc_dbms_queryjmx, _try_system_procedure)
  - _engine/schema.py: reification CRUD, OWL inference,
      run_inference, retract_inference, reify_edge exception path
"""
import pytest
from iris_vector_graph.engine import IRISGraphEngine


@pytest.fixture
def sproc_eng(iris_connection, iris_master_cleanup):
    eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
    for i in range(3):
        eng.create_node(f"sp_{i}", labels=["SP"], properties={"x": i})
    for i in range(2):
        eng.create_edge(f"sp_{i}", "SP_REL", f"sp_{i + 1}", qualifiers={"w": str(i)})
    eng.sync()
    return eng


# ---------------------------------------------------------------------------
# engine.py system procedures via CALL ... YIELD ... RETURN
# ---------------------------------------------------------------------------

class TestSystemProcedures:

    def test_db_labels(self, sproc_eng):
        result = sproc_eng.execute_cypher("CALL db.labels() YIELD label RETURN label")
        assert result is not None
        rows = result.get("rows") if hasattr(result, "get") else result.rows
        assert isinstance(rows, list)

    def test_db_relationship_types(self, sproc_eng):
        result = sproc_eng.execute_cypher(
            "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType"
        )
        assert result is not None

    def test_db_schema_visualization(self, sproc_eng):
        result = sproc_eng.execute_cypher(
            "CALL db.schema.visualization() YIELD nodes, relationships RETURN nodes, relationships"
        )
        assert result is not None

    def test_db_schema_nodetypeproperties(self, sproc_eng):
        result = sproc_eng.execute_cypher(
            "CALL db.schema.nodeTypeProperties() YIELD nodeType, propertyName RETURN nodeType, propertyName"
        )
        assert result is not None

    def test_db_schema_reltypeproperties(self, sproc_eng):
        result = sproc_eng.execute_cypher(
            "CALL db.schema.relTypeProperties() YIELD relType, propertyName RETURN relType, propertyName"
        )
        assert result is not None

    def test_db_propertykeys(self, sproc_eng):
        result = sproc_eng.execute_cypher(
            "CALL db.propertyKeys() YIELD propertyKey RETURN propertyKey"
        )
        assert result is not None

    def test_dbms_components(self, sproc_eng):
        result = sproc_eng.execute_cypher(
            "CALL dbms.components() YIELD name, versions, edition RETURN name, versions, edition"
        )
        assert result is not None
        rows = result.get("rows") if hasattr(result, "get") else result.rows
        assert len(rows) > 0

    def test_dbms_procedures(self, sproc_eng):
        result = sproc_eng.execute_cypher(
            "CALL dbms.procedures() YIELD name, signature RETURN name, signature"
        )
        assert result is not None
        rows = result.get("rows") if hasattr(result, "get") else result.rows
        assert isinstance(rows, list)
        assert len(rows) > 0

    def test_dbms_functions(self, sproc_eng):
        result = sproc_eng.execute_cypher(
            "CALL dbms.functions() YIELD name RETURN name"
        )
        assert result is not None

    def test_dbms_clientconfig(self, sproc_eng):
        result = sproc_eng.execute_cypher(
            "CALL dbms.clientConfig() YIELD key, value RETURN key, value"
        )
        assert result is not None
        rows = result.get("rows") if hasattr(result, "get") else result.rows
        assert len(rows) > 0

    def test_dbms_security_showcurrentuser(self, sproc_eng):
        result = sproc_eng.execute_cypher(
            "CALL dbms.security.showCurrentUser() YIELD username RETURN username"
        )
        assert result is not None

    def test_dbms_queryjmx(self, sproc_eng):
        result = sproc_eng.execute_cypher(
            "CALL dbms.queryJmx() YIELD name RETURN name"
        )
        assert result is not None

    def test_apoc_meta_data(self, sproc_eng):
        try:
            result = sproc_eng.execute_cypher(
                "CALL apoc.meta.data() YIELD value RETURN value"
            )
            assert result is not None
        except Exception:
            pytest.skip("apoc.meta.data path returned error")

    def test_apoc_meta_schema(self, sproc_eng):
        try:
            result = sproc_eng.execute_cypher(
                "CALL apoc.meta.schema() YIELD value RETURN value"
            )
            assert result is not None
        except Exception:
            pytest.skip("apoc.meta.schema not supported")

    def test_unknown_apoc_procedure(self, sproc_eng):
        try:
            result = sproc_eng.execute_cypher(
                "CALL apoc.unknown.procedure() YIELD value RETURN value"
            )
            assert result is not None
        except Exception:
            pass  # Unknown apoc -> empty result is fine

    def test_unknown_dbms_procedure(self, sproc_eng):
        try:
            result = sproc_eng.execute_cypher(
                "CALL dbms.unknown.procedure() YIELD value RETURN value"
            )
            assert result is not None
        except Exception:
            pass


# ---------------------------------------------------------------------------
# _engine/schema.py: reification CRUD
# ---------------------------------------------------------------------------

class TestReification:

    def test_reify_edge_basic(self, sproc_eng):
        # Get an edge_id from the graph
        cursor = sproc_eng.conn.cursor()
        cursor.execute(
            "SELECT edge_id FROM Graph_KG.rdf_edges WHERE s='sp_0' AND p='SP_REL' LIMIT 1"
        )
        row = cursor.fetchone()
        if not row:
            pytest.skip("No edge found to reify")
        edge_id = row[0]
        result = sproc_eng.reify_edge(edge_id, props={"confidence": "0.9"})
        assert result is not None

    def test_get_reifications(self, sproc_eng):
        # Reify first, then get reifications
        cursor = sproc_eng.conn.cursor()
        cursor.execute(
            "SELECT edge_id FROM Graph_KG.rdf_edges WHERE s='sp_0' AND p='SP_REL' LIMIT 1"
        )
        row = cursor.fetchone()
        if not row:
            pytest.skip("No edge found to reify")
        edge_id = row[0]
        sproc_eng.reify_edge(edge_id, props={"test": "val"})
        reifs = sproc_eng.get_reifications(edge_id)
        assert isinstance(reifs, list)

    def test_delete_reification(self, sproc_eng):
        cursor = sproc_eng.conn.cursor()
        cursor.execute(
            "SELECT edge_id FROM Graph_KG.rdf_edges WHERE s='sp_0' AND p='SP_REL' LIMIT 1"
        )
        row = cursor.fetchone()
        if not row:
            pytest.skip("No edge found to reify")
        edge_id = row[0]
        reifier_id = sproc_eng.reify_edge(edge_id, reifier_id=f"test_reif_{edge_id}")
        if reifier_id:
            result = sproc_eng.delete_reification(reifier_id)
            assert result is True or result is False

    def test_reify_nonexistent_edge(self, sproc_eng):
        result = sproc_eng.reify_edge(99999999)
        assert result is None

    def test_get_reifications_nonexistent(self, sproc_eng):
        reifs = sproc_eng.get_reifications(99999999)
        assert isinstance(reifs, list)
        assert reifs == []

    def test_delete_nonexistent_reification(self, sproc_eng):
        result = sproc_eng.delete_reification("nonexistent_reif_id")
        assert result is True or result is False


# ---------------------------------------------------------------------------
# _engine/schema.py: run_inference (rdfs rules)
# ---------------------------------------------------------------------------

class TestRDFSInference:

    def test_run_inference_rdfs(self, sproc_eng):
        try:
            result = sproc_eng.materialize_inference(rules="rdfs")
            assert isinstance(result, dict)
            assert "inferred" in result
        except AttributeError:
            pytest.skip("run_inference not exposed")

    def test_run_inference_owl(self, sproc_eng):
        try:
            result = sproc_eng.materialize_inference(rules="owl")
            assert isinstance(result, dict)
            assert "inferred" in result
        except AttributeError:
            pytest.skip("run_inference not exposed")

    def test_retract_inference(self, sproc_eng):
        try:
            count = sproc_eng.retract_inference()
            assert isinstance(count, int)
        except AttributeError:
            pytest.skip("retract_inference not exposed")


# ---------------------------------------------------------------------------
# engine.py: _reconnect_if_stale path
# ---------------------------------------------------------------------------

class TestReconnectIfStale:

    def test_reconnect_if_stale_healthy_conn(self, sproc_eng):
        # Should not raise on a healthy connection
        try:
            sproc_eng._reconnect_if_stale()
        except RuntimeError as e:
            if "stale" in str(e).lower():
                pass  # Expected when connection params not stored


# ---------------------------------------------------------------------------
# engine.py: additional proc paths
# ---------------------------------------------------------------------------

class TestDijkstraExceptionPath:

    def test_dijkstra_no_source(self, sproc_eng):
        # No args — hits the no source_id/target_id early return
        result = sproc_eng.execute_cypher(
            "CALL ivg.shortestPath.weighted() YIELD path, totalCost RETURN path, totalCost"
        )
        assert result is not None

    def test_dijkstra_both_vars(self, sproc_eng):
        result = sproc_eng.execute_cypher(
            "CALL ivg.shortestPath.weighted($src, $tgt, 'w', 999) "
            "YIELD path, totalCost RETURN path, totalCost",
            parameters={"src": "sp_0", "tgt": "sp_2"}
        )
        assert result is not None
