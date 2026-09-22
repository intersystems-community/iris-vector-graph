"""An orphan routed table must not make every erase in the namespace fail.

A routed table carries a foreign key on `(graph_id, node_id)`. Both erase paths in
`Graph.KG.Eraser` collect the routed tables they have to empty from
`Graph_KG.embedding_registry`, so a `kg_emb_` table with no registry row is never
emptied, `DELETE FROM Graph_KG.nodes` fails its referential check with
`SQLCODE -124`, and the transaction rolls back: "erase failed on nodes with
SQLCODE -124; nothing was erased". One stray table blocks every erase in the
namespace, fixture teardowns included.

The registry stays the authority for what may be *dropped* — that is FR-011, and a
recomputed name that finds nothing would leave a table full of vectors standing. But
it cannot be the only source of what must be *emptied*, because the foreign key does
not consult it.

These tests build the orphan the way one actually appears: create the routed table,
then remove its registry row, which is what a rollback after the (already durable)
DDL leaves behind.
"""

from __future__ import annotations

import os

import pytest

from iris_vector_graph.constants import ROUTE_TABLE_PREFIX
from iris_vector_graph.engine import IRISGraphEngine

pytestmark = pytest.mark.skipif(
    os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true",
    reason="SKIP_IRIS_TESTS is set",
)

_ORPHAN = ROUTE_TABLE_PREFIX + "0rphan7e57c0de"
_GRAPH = "orphan-route-graph"


def _drop_orphan(conn) -> None:
    cur = conn.cursor()
    for sql in (
        f"DELETE FROM Graph_KG.embedding_registry WHERE table_name = '{_ORPHAN}'",
        f"DROP TABLE Graph_KG.{_ORPHAN}",
    ):
        try:
            cur.execute(sql)
        except Exception:
            pass
    try:
        conn.commit()
    except Exception:
        pass
    cur.close()


@pytest.fixture
def orphan_route(iris_connection):
    """A `kg_emb_` table holding a node's vector, with no registry row naming it."""
    _drop_orphan(iris_connection)
    cur = iris_connection.cursor()
    cur.execute(
        f"""CREATE TABLE Graph_KG.{_ORPHAN} (
              graph_id VARCHAR(256) %EXACT NOT NULL DEFAULT '',
              node_id VARCHAR(256) %EXACT NOT NULL,
              emb VECTOR(DOUBLE, 8),
              CONSTRAINT pk_{_ORPHAN} PRIMARY KEY (graph_id, node_id),
              CONSTRAINT fk_{_ORPHAN} FOREIGN KEY (graph_id, node_id)
                REFERENCES Graph_KG.nodes(graph_id, node_id)
            )"""
    )
    iris_connection.commit()
    yield _ORPHAN
    _drop_orphan(iris_connection)


def test_erase_graph_succeeds_with_an_orphan_routed_table(iris_connection, orphan_route):
    engine = IRISGraphEngine(iris_connection, embedding_dimension=8)
    engine.initialize_schema(auto_deploy_objectscript=False)

    cur = iris_connection.cursor()
    cur.execute(
        "INSERT INTO Graph_KG.nodes (graph_id, node_id) VALUES (?, ?)",
        [_GRAPH, "orphan:n1"],
    )
    vec = ",".join("0.5" for _ in range(8))
    cur.execute(
        f"INSERT INTO Graph_KG.{orphan_route} (graph_id, node_id, emb) "
        f"VALUES (?, ?, TO_VECTOR(?, DOUBLE, 8))",
        [_GRAPH, "orphan:n1", vec],
    )
    iris_connection.commit()

    # The erase must not be defeated by a table the registry does not name.
    engine.erase_graph(_GRAPH)

    cur.execute(
        f"SELECT COUNT(*) FROM Graph_KG.{orphan_route} WHERE graph_id = ?", [_GRAPH]
    )
    assert cur.fetchone()[0] == 0, "the orphan kept this graph's vectors"
    cur.execute("SELECT COUNT(*) FROM Graph_KG.nodes WHERE graph_id = ?", [_GRAPH])
    assert cur.fetchone()[0] == 0, "the nodes survived, so the erase rolled back"


def test_an_unregistered_route_is_emptied_but_not_dropped(iris_connection, orphan_route):
    """The registry stays the authority for DDL (FR-011); only the rows go."""
    engine = IRISGraphEngine(iris_connection, embedding_dimension=8)
    engine.initialize_schema(auto_deploy_objectscript=False)

    cur = iris_connection.cursor()
    cur.execute(
        "INSERT INTO Graph_KG.nodes (graph_id, node_id) VALUES (?, ?)",
        [_GRAPH, "orphan:n2"],
    )
    iris_connection.commit()

    engine.erase_graph(_GRAPH)

    cur.execute(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_SCHEMA = 'Graph_KG' AND TABLE_NAME = ?",
        [orphan_route],
    )
    assert cur.fetchone()[0] == 1, "an unregistered table was dropped by an erase"
