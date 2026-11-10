"""4.7.0 row 3c: two silent failures in the loaders.

(i) `BulkLoader.build_graph_globals` sent `Do ##class(Graph.KG.Traversal).BuildKG()` as SQL: IRIS answered SQLCODE -51
("SQL statement expected"), the method logged it and answered False, and `^KG` was never rebuilt. `load_networkx`
put that False in `stats["globals_built"]` and returned as if the load were complete. Now it runs the class method
on the loader's connection, as the engine's `sync()` does, and a `%Status` error raises.

(ii) `attach_embeddings_to_table` stores each row's vector for the node `<label>:<id>`; `store_embedding` refuses a
node that does not exist, and the method logged it and skipped the row, so a mapped table whose rows have no nodes
embedded 0 rows and answered `{"embedded": 0, ...}` with nothing else said.

    IVG_R470_WR_NS=IVG470WR pytest tests/e2e/test_470_silent_writers_e2e.py
"""

from __future__ import annotations

import os
import uuid

import pytest

from tests.e2e import reading_role460 as rr
from tests.e2e import scratch_vars

NS = os.environ.get("IVG_R470_WR_NS", "")
DATA_TABLE = "SQLUser.ivg470wr_data"

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not rr.container_running(), reason=f"{rr.CONTAINER} is not running"),
    scratch_vars.requires("IVG_R470_WR_NS"),
]


def _connect():
    import iris

    return iris.connect("localhost", rr.PORT, NS, "_SYSTEM", "SYS")


class Fake:
    def encode(self, texts, **_):
        import numpy as np

        if isinstance(texts, str):
            return np.full(rr.DIM, 0.25)
        return np.full((len(texts), rr.DIM), 0.25)


@pytest.fixture
def conn():
    c = _connect()
    yield c
    c.close()


def _uid() -> str:
    return "t470w" + uuid.uuid4().hex[:10]


def _kg_out(conn, s, p, o) -> int:
    import iris

    return int(iris.createIRIS(conn).isDefined("^KG", "out", 0, s, p, o))


def _kill_kg(conn, *ids):
    import iris

    native = iris.createIRIS(conn)
    for nid in ids:
        native.kill("^KG", "out", 0, nid)
        native.kill("^KG", "in", 0, nid)


# ------------------------------------------------------------------------------- (i) build_graph_globals


def test_build_graph_globals_rebuilds_kg(conn):
    from iris_vector_graph import IRISGraphEngine
    from iris_vector_graph.bulk_loader import BulkLoader

    u = _uid()
    loader = BulkLoader(conn)
    loader.load_nodes([(f"{u}a", {"namespace": "T470W"}), (f"{u}b", {"namespace": "T470W"})])
    loader.load_edges([(f"{u}a", "R470W", f"{u}b", None)])
    loader.rebuild_all_indices()
    _kill_kg(conn, f"{u}a", f"{u}b")
    assert _kg_out(conn, f"{u}a", "R470W", f"{u}b") == 0, "the test could not take the edge out of ^KG"
    assert loader.build_graph_globals() is True
    assert _kg_out(conn, f"{u}a", "R470W", f"{u}b") > 0, "^KG was not rebuilt"
    eng = IRISGraphEngine(_connect(), embedding_dimension=rr.DIM)
    try:
        r = eng.execute_cypher("MATCH (a {id: $a})-[:R470W]->(b) RETURN b.id", {"a": f"{u}a"}, raise_on_error=True)
        assert [row[0] for row in r.rows] == [f"{u}b"]
    finally:
        eng.conn.close()


def test_load_networkx_builds_the_globals(conn):
    import networkx as nx

    from iris_vector_graph.bulk_loader import BulkLoader

    u = _uid()
    g = nx.DiGraph()
    g.add_node(f"{u}a", namespace="T470W")
    g.add_node(f"{u}b", namespace="T470W")
    g.add_edge(f"{u}a", f"{u}b", predicate="R470W")
    stats = BulkLoader(conn).load_networkx(g)
    assert stats["globals_built"] is True, stats
    assert sum(_kg_out(conn, f"{u}a", p, f"{u}b") > 0 for p in ("R470W", "RELATED_TO", "related_to")) == 1
