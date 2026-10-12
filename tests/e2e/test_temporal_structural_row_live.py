"""Live check for spec 246: a temporal-only edge is SQL- and Cypher-visible only with the flag on.

Needs IRIS (the enterprise test container). The lead runs this in one serial pass; the
package author does not run it. Each engine gets its own prefix so the two cases do not
see each other's rows.
"""

import os
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")


def _prefix() -> str:
    return f"TSR_{uuid.uuid4().hex[:8]}"


class TestTemporalStructuralRowLive:

    @pytest.fixture(autouse=True)
    def cleanup(self, iris_connection):
        self.created = []
        yield
        cursor = iris_connection.cursor()
        for prefix in self.created:
            for table in ("rdf_edges", "nodes"):
                try:
                    col = "s" if table == "rdf_edges" else "node_id"
                    cursor.execute(
                        f"DELETE FROM Graph_KG.{table} WHERE {col} LIKE '{prefix}%'"
                    )
                except Exception:
                    pass
        iris_connection.commit()

    def _edge_rows(self, iris_connection, prefix: str) -> int:
        cursor = iris_connection.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE s LIKE ?", [f"{prefix}%"]
        )
        return int(cursor.fetchone()[0])

    def _match_count(self, engine, prefix: str) -> int:
        result = engine.execute_cypher(
            "MATCH (a)-[r:TSR_CALLS]->(b) WHERE a.node_id STARTS WITH $p RETURN a.node_id",
            {"p": prefix},
        )
        return len(result.rows)

    def test_flag_on_makes_a_temporal_only_edge_visible(self, iris_connection):
        prefix = _prefix()
        self.created.append(prefix)
        engine = IRISGraphEngine(iris_connection, temporal_structural_rows=True)

        assert engine.create_edge_temporal(
            f"{prefix}:a", "TSR_CALLS", f"{prefix}:b", timestamp=1700000000
        ) is True

        assert self._edge_rows(iris_connection, prefix) == 1
        assert self._match_count(engine, prefix) == 1

    def test_flag_off_keeps_a_temporal_only_edge_out_of_sql(self, iris_connection):
        prefix = _prefix()
        self.created.append(prefix)
        engine = IRISGraphEngine(iris_connection)

        assert engine.create_edge_temporal(
            f"{prefix}:a", "TSR_CALLS", f"{prefix}:b", timestamp=1700000000
        ) is True

        assert self._edge_rows(iris_connection, prefix) == 0
        assert self._match_count(engine, prefix) == 0

    def test_flag_on_delete_removes_the_row_and_the_match(self, iris_connection):
        prefix = _prefix()
        self.created.append(prefix)
        engine = IRISGraphEngine(iris_connection, temporal_structural_rows=True)
        engine.create_edge_temporal(
            f"{prefix}:a", "TSR_CALLS", f"{prefix}:b", timestamp=1700000000
        )

        assert engine.delete_edge_temporal(
            f"{prefix}:a", "TSR_CALLS", f"{prefix}:b", timestamp=1700000000
        ) is True

        assert self._edge_rows(iris_connection, prefix) == 0
        assert self._match_count(engine, prefix) == 0
