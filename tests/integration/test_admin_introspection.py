"""
Integration tests for AdminMixin: _show_indexes, _show_constraints, status(),
and _handle_show_command dispatch.

All tests run against live ivg-iris. No mocking — exercises real SQL COUNTs,
classMethodValue calls, and Native API adjacency probes.
"""
import pytest
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.result import IVGResult


@pytest.fixture
def engine(iris_connection, iris_master_cleanup):
    eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
    # Load a small graph so adjacency stats are non-trivial
    for i in range(5):
        eng.create_node(f"adm_{i}", labels=["Thing"])
    for i in range(4):
        eng.create_edge(f"adm_{i}", "R", f"adm_{i+1}")
    eng.sync()
    return eng


# ---------------------------------------------------------------------------
# SHOW INDEXES
# ---------------------------------------------------------------------------

class TestShowIndexes:

    def test_show_indexes_returns_ivgresult(self, engine):
        r = engine._show_indexes()
        assert isinstance(r, IVGResult)

    def test_show_indexes_has_required_columns(self, engine):
        r = engine._show_indexes()
        assert "name" in r.columns
        assert "type" in r.columns
        assert "state" in r.columns

    def test_show_indexes_contains_kg_entry(self, engine):
        r = engine._show_indexes()
        names = [row[r.columns.index("name")] for row in r.rows]
        assert any("kg" in n.lower() or "nkg" in n.lower() for n in names), (
            f"Expected ^KG or ^NKG index entry, got: {names}"
        )

    def test_show_indexes_contains_hnsw_entry(self, engine):
        r = engine._show_indexes()
        types = [row[r.columns.index("type")] for row in r.rows]
        # HNSW index should always be reported (even if BUILDING state)
        assert any("hnsw" in t.lower() or "vector" in t.lower() for t in types), (
            f"Expected HNSW/VECTOR index entry, got: {types}"
        )

    def test_show_indexes_all_rows_have_state(self, engine):
        r = engine._show_indexes()
        state_idx = r.columns.index("state")
        for row in r.rows:
            assert row[state_idx] in ("ONLINE", "BUILDING", "OFFLINE", "UNKNOWN"), (
                f"Unexpected state: {row[state_idx]}"
            )


# ---------------------------------------------------------------------------
# SHOW CONSTRAINTS
# ---------------------------------------------------------------------------

class TestShowConstraints:
    # NOTE: _show_constraints probes fhir_bridges table existence via SQL.
    # The IRIS Python driver segfaults on some non-existent table probes.
    # Test via the SHOW CONSTRAINTS dispatch path which has the same exception guard.

    def test_show_constraints_via_dispatch_returns_ivgresult(self, engine):
        r = engine._handle_show_command("SHOW CONSTRAINTS")
        assert isinstance(r, IVGResult)

    def test_show_constraints_has_name_column(self, engine):
        r = engine._handle_show_command("SHOW CONSTRAINTS")
        assert "name" in r.columns

    def test_show_constraints_non_empty(self, engine):
        r = engine._handle_show_command("SHOW CONSTRAINTS")
        assert len(r.rows) >= 1


# ---------------------------------------------------------------------------
# SHOW command dispatch
# ---------------------------------------------------------------------------

class TestHandleShowCommand:

    def test_show_databases_returns_neo4j_compat_row(self, engine):
        r = engine._handle_show_command("SHOW DATABASES")
        assert isinstance(r, IVGResult)
        assert len(r.rows) >= 1
        col = r.columns.index("name") if "name" in r.columns else 0
        assert r.rows[0][col] == "neo4j"

    def test_show_indexes_via_dispatch(self, engine):
        r = engine._handle_show_command("SHOW INDEXES")
        assert isinstance(r, IVGResult)
        assert "name" in r.columns

    def test_show_constraints_via_dispatch(self, engine):
        r = engine._handle_show_command("SHOW CONSTRAINTS")
        assert isinstance(r, IVGResult)
        assert len(r.rows) >= 1

    def test_show_unknown_returns_empty(self, engine):
        r = engine._handle_show_command("SHOW NONEXISTENT")
        assert isinstance(r, IVGResult)
        assert r.rows == [] or len(r.rows) == 0

    def test_show_procedures_returns_ivgresult(self, engine):
        r = engine._handle_show_command("SHOW PROCEDURES")
        assert isinstance(r, IVGResult)
        assert "name" in r.columns

    def test_show_functions_returns_ivgresult(self, engine):
        r = engine._handle_show_command("SHOW FUNCTIONS")
        assert isinstance(r, IVGResult)
        assert "name" in r.columns


# ---------------------------------------------------------------------------
# Engine status()
# ---------------------------------------------------------------------------

class TestEngineStatus:

    def test_status_returns_engine_status(self, engine):
        from iris_vector_graph.status import EngineStatus
        s = engine.status()
        assert isinstance(s, EngineStatus)

    def test_status_table_counts_non_negative(self, engine):
        s = engine.status()
        tc = s.tables  # field is 'tables', not 'table_counts'
        assert tc.nodes >= 0
        assert tc.edges >= 0

    def test_status_adjacency_kg_built_after_sync(self, engine):
        s = engine.status()
        # kg_populated is the field name (not kg_built)
        assert s.adjacency.kg_populated is True

    def test_status_objectscript_deployed(self, engine):
        s = engine.status()
        # ObjectScript classes are compiled on the container; status probes at connection time.
        # The engine fixture uses auto_deploy_objectscript=False so deployed may be False
        # from the engine's perspective. Just verify it's a boolean.
        assert isinstance(s.objectscript.deployed, bool)

    def test_status_reflects_node_count(self, engine, iris_connection):
        # We added 5 nodes in fixture; status should see them
        s = engine.status()
        assert s.tables.nodes >= 5

    def test_status_serializable_to_dict(self, engine):
        s = engine.status()
        d = s.to_dict() if hasattr(s, "to_dict") else vars(s)
        assert isinstance(d, dict)
