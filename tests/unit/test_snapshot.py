"""save_snapshot / restore_snapshot against a live namespace of their own.

The namespace is IVG_NS_IVG460SNAP (default IVG460SNAP), with a database of its own, made and set up from this
checkout when it does not exist. Never the session's namespace (IVG_NAMESPACE, USER by default): restore_snapshot
without `merge` erases the whole namespace, and save_snapshot reads every index any other test left there, so in USER
these tests emptied it for every other suite and failed on whatever another test had written (4.6.0 R302: the
`$vector` centroids of the IVF indexes test_ivf_index leaves behind).
"""

import importlib.util
import json
import os
import uuid
import zipfile
from pathlib import Path

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
REPO = Path(__file__).resolve().parents[2]
NS = os.environ.get("IVG_NS_IVG460SNAP", "IVG460SNAP")

SQL_TABLES = [
    "Graph_KG.nodes",
    "Graph_KG.rdf_edges",
    "Graph_KG.rdf_labels",
    "Graph_KG.rdf_props",
]


def _make_scratch_namespace():
    spec = importlib.util.spec_from_file_location("make_scratch_namespace_snapshot", REPO / "scripts/dev/make_scratch_namespace.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def snapshot_conn():
    """A connection to NS with this checkout's classes and schema in it."""
    import iris

    import tests.conftest as session
    from iris_vector_graph.engine import IRISGraphEngine

    msn = _make_scratch_namespace()
    if not msn.may_create(NS):
        pytest.fail(f"IVG_NS_IVG460SNAP={NS!r}: an upper-case IVG460*/IVG262* name is required")
    if msn.make(NS):
        msn.install_ivg(NS)
    session._deploy_objectscript(msn.CONTAINER, NS)
    conn = iris.connect("localhost", msn.PORT, NS, "_SYSTEM", "SYS")
    IRISGraphEngine(conn, embedding_dimension=768).initialize_schema(auto_deploy_objectscript=False)
    yield conn
    conn.close()


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestSnapshotE2E:
    @pytest.fixture(autouse=True)
    def setup(self, snapshot_conn, tmp_path):
        from iris_vector_graph.engine import IRISGraphEngine

        self.conn = snapshot_conn
        self.engine = IRISGraphEngine(snapshot_conn)
        self._run = uuid.uuid4().hex[:8]
        self._tmp = tmp_path
        yield
        self._cleanup()

    def _n(self, label):
        return f"snap_{label}_{self._run}"

    def _cleanup(self):
        cursor = self.conn.cursor()
        prefix = f"snap_%_{self._run}"
        for table in [
            "Graph_KG.rdf_edges",
            "Graph_KG.rdf_labels",
            "Graph_KG.rdf_props",
            "Graph_KG.nodes",
        ]:
            try:
                cursor.execute(
                    f"DELETE FROM {table} WHERE s LIKE ? OR o_id LIKE ?",
                    [prefix, prefix],
                )
            except Exception:
                pass
            try:
                cursor.execute(f"DELETE FROM {table} WHERE node_id LIKE ?", [prefix])
            except Exception:
                pass
        try:
            self.conn.commit()
        except Exception:
            pass

    def _snapshot_path(self):
        return str(self._tmp / f"snapshot_{self._run}.ivg")

    def _count(self, table, where_col, prefix):
        cursor = self.conn.cursor()
        cursor.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {where_col} LIKE ?", [prefix]
        )
        return cursor.fetchone()[0]

    def test_save_restore_roundtrip(self):
        a, b, c = self._n("A"), self._n("B"), self._n("C")
        for n in [a, b, c]:
            self.engine.create_node(n)
        self.engine.create_edge(a, "R", b)
        self.engine.create_edge(b, "R", c)
        prefix = f"snap_%_{self._run}"
        nodes_before = self._count("Graph_KG.nodes", "node_id", prefix)
        edges_before = self._count("Graph_KG.rdf_edges", "s", prefix)
        assert nodes_before == 3
        assert edges_before == 2
        path = self._snapshot_path()
        self.engine.save_snapshot(path)
        cursor = self.conn.cursor()
        for table in [
            "Graph_KG.rdf_edges",
            "Graph_KG.rdf_labels",
            "Graph_KG.rdf_props",
            "Graph_KG.nodes",
        ]:
            try:
                cursor.execute(
                    f"DELETE FROM {table} WHERE s LIKE ? OR o_id LIKE ?",
                    [prefix, prefix],
                )
            except Exception:
                pass
            try:
                cursor.execute(f"DELETE FROM {table} WHERE node_id LIKE ?", [prefix])
            except Exception:
                pass
        self.conn.commit()
        assert self._count("Graph_KG.nodes", "node_id", prefix) == 0
        result = self.engine.restore_snapshot(path)
        assert result["restored_tables"]["Graph_KG.nodes"] >= 3
        assert self._count("Graph_KG.nodes", "node_id", prefix) == 3
        assert self._count("Graph_KG.rdf_edges", "s", prefix) == 2

    def test_snapshot_info_staticmethod(self):
        from iris_vector_graph.engine import IRISGraphEngine

        a, b = self._n("A"), self._n("B")
        self.engine.create_node(a)
        self.engine.create_node(b)
        self.engine.create_edge(a, "R", b)
        path = self._snapshot_path()
        self.engine.save_snapshot(path)
        info = IRISGraphEngine.snapshot_info(path)
        assert isinstance(info, dict)
        assert "metadata" in info or "tables" in info or "version" in info
        assert info.get("tables", {}).get("Graph_KG.nodes", 0) >= 1

    def test_restore_is_destructive_by_default(self):
        a, b = self._n("A"), self._n("B")
        extra = self._n("EXTRA")
        self.engine.create_node(a)
        self.engine.create_node(b)
        self.engine.create_edge(a, "R", b)
        path = self._snapshot_path()
        self.engine.save_snapshot(path)
        self.engine.create_node(extra)
        prefix = f"snap_%_{self._run}"
        assert self._count("Graph_KG.nodes", "node_id", prefix) == 3
        self.engine.restore_snapshot(path)
        assert self._count("Graph_KG.nodes", "node_id", prefix) == 2, (
            "Destructive restore should remove the extra node added after snapshot"
        )

    def test_restore_merge_preserves_local(self):
        a, b = self._n("A"), self._n("B")
        extra = self._n("EXTRA")
        self.engine.create_node(a)
        self.engine.create_node(b)
        self.engine.create_edge(a, "R", b)
        path = self._snapshot_path()
        self.engine.save_snapshot(path)
        self.engine.create_node(extra)
        self.engine.restore_snapshot(path, merge=True)
        prefix = f"snap_%_{self._run}"
        assert self._count("Graph_KG.nodes", "node_id", prefix) == 3, (
            "merge=True should preserve local nodes added after snapshot"
        )

    def test_snapshot_includes_globals_for_bfs(self):
        a, b, c = self._n("A"), self._n("B"), self._n("C")
        for n in [a, b, c]:
            self.engine.create_node(n)
        self.engine.create_edge(a, "CONN", b)
        self.engine.create_edge(b, "CONN", c)
        path = self._snapshot_path()
        self.engine.save_snapshot(path)
        prefix = f"snap_%_{self._run}"
        cursor = self.conn.cursor()
        for table in ["Graph_KG.rdf_edges", "Graph_KG.nodes"]:
            try:
                cursor.execute(
                    f"DELETE FROM {table} WHERE s LIKE ? OR o_id LIKE ?",
                    [prefix, prefix],
                )
            except Exception:
                pass
            try:
                cursor.execute(f"DELETE FROM {table} WHERE node_id LIKE ?", [prefix])
            except Exception:
                pass
        self.conn.commit()
        self.engine.restore_snapshot(path)
        result = self.engine.execute_cypher(
            f"MATCH p = shortestPath((x {{id:'{a}'}})-[*..4]-(y {{id:'{c}'}})) RETURN p"
        )
        assert len(result["rows"]) == 1, (
            "BFS via ^KG globals should work after snapshot restore"
        )


def _archive_lines(path: str, gname: str) -> dict:
    """{tuple(subscripts): the rest of the line} for the global `gname` in a snapshot archive."""
    with zipfile.ZipFile(path) as zf:
        text = zf.read(f"globals/{gname}.ndjson").decode("utf-8")
    out = {}
    for line in text.splitlines():
        entry = json.loads(line)
        out[tuple(entry.pop("k"))] = entry
    return out


def _unit(i: int, dim: int = 768) -> list:
    v = [0.0] * dim
    v[i] = 1.0
    return v


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestSnapshotCarriesVectorIndexes:
    """4.6.0 R302: ^IVF, ^PLAID and ^VecIdx hold `$vector` values (PLAID's docPacked a $List of them), which no
    Native API getter reads (`<LIST ERROR> ... type detected : 16`), so save_snapshot raised on any namespace holding
    an IVF, PLAID or VecIndex index, and before R271 left the global out of the archive with a warning."""

    def test_the_three_vector_indexes_survive_a_snapshot_and_restore(self, snapshot_conn, tmp_path):
        from iris_vector_graph.engine import IRISGraphEngine

        engine = IRISGraphEngine(snapshot_conn, embedding_dimension=768)
        run = uuid.uuid4().hex[:8]
        vec, plaid, ivf = f"snapvec_{run}", f"snapplaid_{run}", f"snapivf_{run}"
        a, b = f"snap_ivfA_{run}", f"snap_ivfB_{run}"

        engine.vec_create_index(vec, dim=3)
        for doc, v in (("x", [1.0, 0.0, 0.0]), ("y", [0.0, 1.0, 0.0]), ("z", [0.6, 0.8, 0.0])):
            engine.vec_insert(vec, doc, v)
        engine.vec_build(vec)
        engine.plaid_build(plaid, [{"id": "p1", "tokens": [[1.0, 0.0, 0.0], [0.9, 0.1, 0.0]]},
                                   {"id": "p2", "tokens": [[0.0, 1.0, 0.0], [0.0, 0.9, 0.1]]}], n_clusters=2)
        for node, i in ((a, 0), (b, 1)):
            engine.create_node(node)
            engine.store_embedding(node, _unit(i))
        engine.ivf_build(ivf, nlist=2, node_ids=[a, b])

        def answers():
            return (engine.vec_search(vec, [0.0, 1.0, 0.0], k=1)[0]["id"],
                    engine.plaid_search(plaid, [[0.0, 1.0, 0.0]], k=1)[0]["id"],
                    engine.ivf_search(ivf, _unit(1), k=1)[0][0])

        assert answers() == ("y", "p2", b)
        try:
            first = str(tmp_path / "first.ivg")
            engine.save_snapshot(first)
            vec_lines, plaid_lines, ivf_lines = (_archive_lines(first, g) for g in ("VecIdx", "PLAID", "IVF"))
            # the values written above, not what the archive says about itself
            assert vec_lines[(vec, "vec", "z")] == {"vec": "double", "v": [0.6, 0.8, 0.0]}
            assert plaid_lines[(plaid, "docPacked", "p2")] == {
                "list": [{"vec": "double", "v": [0.0, 1.0, 0.0]}, {"vec": "double", "v": [0.0, 0.9, 0.1]}]}
            cell_of_b = [k for k in ivf_lines if k[:2] == (ivf, "list") and k[3:] == (b,)]
            assert len(cell_of_b) == 1 and ivf_lines[cell_of_b[0]] == {"vec": "double", "v": _unit(1)}

            engine.vec_drop(vec)
            engine.plaid_drop(plaid)
            engine.ivf_drop(ivf)
            assert engine.ivf_info(ivf) == {}
            engine.restore_snapshot(first)
            assert answers() == ("y", "p2", b)

            second = str(tmp_path / "second.ivg")
            engine.save_snapshot(second)
            for g in ("VecIdx", "PLAID", "IVF"):
                ours = lambda lines: {k: v for k, v in lines.items() if k[0] in (vec, plaid, ivf)}
                assert ours(_archive_lines(second, g)) == ours(_archive_lines(first, g)), g
        finally:
            engine.vec_drop(vec)
            engine.plaid_drop(plaid)
            engine.ivf_drop(ivf)
