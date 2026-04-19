import os
import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

REQUIRED_TABLES = [
    "Graph_KG.nodes",
    "Graph_KG.rdf_edges",
    "Graph_KG.rdf_labels",
    "Graph_KG.rdf_props",
    "Graph_KG.rdf_reifications",
]

CORE_PROCEDURES = [
    ("Graph_KG.MatchEdges", ["", "", 0]),
]


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestSchemaInitialization:
    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        from iris_vector_graph.engine import IRISGraphEngine

        self.conn = iris_connection
        self.engine = IRISGraphEngine(iris_connection, embedding_dimension=4)

    def test_initialize_schema_completes_without_error(self):
        self.engine.initialize_schema(auto_deploy_objectscript=False)

    def test_initialize_schema_is_idempotent(self):
        self.engine.initialize_schema(auto_deploy_objectscript=False)
        self.engine.initialize_schema(auto_deploy_objectscript=False)

    def test_required_tables_exist_after_init(self):
        self.engine.initialize_schema(auto_deploy_objectscript=False)
        cursor = self.conn.cursor()
        for table in REQUIRED_TABLES:
            try:
                cursor.execute(f"SELECT TOP 1 1 FROM {table}")
            except Exception as e:
                pytest.fail(
                    f"Required table {table} not queryable after initialize_schema: {e}"
                )

    def test_optional_indexes_fail_gracefully(self):
        from iris_vector_graph.schema import GraphSchema

        cursor = self.conn.cursor()
        status = GraphSchema.ensure_indexes(cursor)
        for optional in ("idx_props_val_ifind", "idx_edges_confidence"):
            assert optional in status, f"Status dict missing key {optional}"

    def test_core_procedure_matchedges_callable(self):
        self.engine.initialize_schema(auto_deploy_objectscript=False)
        cursor = self.conn.cursor()
        try:
            cursor.execute("SELECT Graph_KG.MatchEdges(?, ?, ?)", ["", "", 0])
            cursor.fetchone()
        except Exception as e:
            pytest.fail(
                f"Graph_KG.MatchEdges not callable after initialize_schema: {e}"
            )

    def test_probe_capabilities_does_not_raise(self):
        self.engine.initialize_schema(auto_deploy_objectscript=False)
        from iris_vector_graph.schema import GraphSchema

        caps = GraphSchema.check_objectscript_classes(self.conn.cursor(), self.conn)
        assert caps is not None
