"""4.7.0 row 14: a value wider than its column is refused before a batch writes anything.

The driver's `executemany` raises IRIS's refusal (SQLCODE -104) when the statement has one row and skips the refused
row without raising when it has more (docs/KNOWN_ISSUES.md, 4.6.4 "Known, not fixed"). So on 4.6.5
`bulk_create_nodes([{"id": a, "labels": ["A"]}, {"id": b, "labels": ["L" * 129]}])` returned both ids and node b had
no label, and `create_node(b, labels=["A", "L" * 129])` returned True with only label A. Each writer that sends rows in
a batch now checks the widths first (`rdf_labels.label`, `rdf_props.key` and `rdf_edges.p` hold 128 characters,
`nodes.node_id` 256) and raises `ValueError` naming the row, with nothing written. 128 characters is still written.

    IVG_R470_WR_NS=IVG470WR pytest tests/e2e/test_470_batch_widths_e2e.py
"""

from __future__ import annotations

import os
import uuid

import pytest

from tests.e2e import reading_role460 as rr
from tests.e2e import scratch_vars

NS = os.environ.get("IVG_R470_WR_NS", "")
LONG = "L" * 129
EDGE = "L" * 128

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not rr.container_running(), reason=f"{rr.CONTAINER} is not running"),
    scratch_vars.requires("IVG_R470_WR_NS"),
]


def _connect():
    import iris

    return iris.connect("localhost", rr.PORT, NS, "_SYSTEM", "SYS")


@pytest.fixture
def conn():
    c = _connect()
    yield c
    c.close()


def _engine(conn, server_loader: bool):
    from iris_vector_graph import IRISGraphEngine

    eng = IRISGraphEngine(conn, embedding_dimension=rr.DIM)
    if server_loader:
        assert eng.capabilities.objectscript_deployed
    else:
        assert not eng._known_capabilities.objectscript_deployed
    return eng


def _uid() -> str:
    return "t470x" + uuid.uuid4().hex[:10]


def _count(conn, sql, params) -> int:
    cur = conn.cursor()
    cur.execute(sql, params)
    return int(cur.fetchone()[0])


def _nodes(conn, u) -> int:
    return _count(conn, "SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id %STARTSWITH ?", [u])


def _labels(conn, nid):
    cur = conn.cursor()
    cur.execute("SELECT label FROM Graph_KG.rdf_labels WHERE s = ?", [nid])
    return sorted(r[0] for r in cur.fetchall())


@pytest.mark.parametrize("server_loader", (True, False), ids=("server_loader", "batch_sql"))
@pytest.mark.parametrize(
    "shape",
    ("second_node", "second_label", "property_key"),
)
def test_bulk_create_nodes_refuses_a_too_wide_row_and_writes_nothing(conn, server_loader, shape):
    u = _uid()
    eng = _engine(conn, server_loader)
    nodes = {
        "second_node": [{"id": f"{u}a", "labels": ["A"]}, {"id": f"{u}b", "labels": [LONG]}],
        "second_label": [{"id": f"{u}a", "labels": ["A", LONG]}],
        "property_key": [{"id": f"{u}a", "labels": ["A"]}, {"id": f"{u}b", "properties": {LONG: "v"}}],
    }[shape]
    with pytest.raises(ValueError, match=r"bulk_create_nodes: node \d .*129 characters.* 128"):
        eng.bulk_create_nodes(nodes)
    assert _nodes(conn, u) == 0, "a row of the refused batch was written"


@pytest.mark.parametrize("server_loader", (True, False), ids=("server_loader", "batch_sql"))
def test_bulk_create_nodes_writes_a_128_character_label(conn, server_loader):
    u = _uid()
    eng = _engine(conn, server_loader)
    assert eng.bulk_create_nodes([{"id": f"{u}a", "labels": [EDGE]}, {"id": f"{u}b", "labels": ["B"]}]) == [
        f"{u}a", f"{u}b"]
    assert _labels(conn, f"{u}a") == [EDGE]


def test_create_node_refuses_a_too_wide_second_label(conn):
    u = _uid()
    eng = _engine(conn, False)
    with pytest.raises(ValueError, match=r"create_node: label .*129 characters"):
        eng.create_node(f"{u}a", labels=["A", LONG])
    assert _nodes(conn, u) == 0


def test_create_node_refuses_a_too_wide_property_key(conn):
    u = _uid()
    eng = _engine(conn, False)
    with pytest.raises(ValueError, match=r"create_node: property key .*129 characters"):
        eng.create_node(f"{u}a", labels=["A"], properties={"ok": 1, LONG: 2})
    assert _nodes(conn, u) == 0


def test_bulk_create_edges_refuses_a_too_wide_predicate(conn):
    u = _uid()
    eng = _engine(conn, False)
    with pytest.raises(ValueError, match=r"bulk_create_edges: edge 1 .*predicate.*129 characters"):
        eng.bulk_create_edges([
            {"source_id": f"{u}a", "predicate": "R", "target_id": f"{u}b"},
            {"source_id": f"{u}c", "predicate": LONG, "target_id": f"{u}d"},
        ])
    assert _nodes(conn, u) == 0


def test_bulk_loader_refuses_a_too_wide_label(conn):
    from iris_vector_graph.bulk_loader import BulkLoader

    u = _uid()
    with pytest.raises(ValueError, match=r"BulkLoader.load_nodes: node 1 .*129 characters"):
        BulkLoader(conn).load_nodes([(f"{u}a", {"namespace": "A"}), (f"{u}b", {"namespace": LONG})])
    assert _nodes(conn, u) == 0


def test_bulk_loader_refuses_a_too_wide_predicate(conn):
    from iris_vector_graph.bulk_loader import BulkLoader

    u = _uid()
    with pytest.raises(ValueError, match=r"BulkLoader.load_edges: edge 1 .*129 characters"):
        BulkLoader(conn).load_edges([(f"{u}a", "R", f"{u}b", None), (f"{u}c", LONG, f"{u}d", None)])
    assert _count(conn, "SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE s %STARTSWITH ?", [u]) == 0


@pytest.mark.parametrize(
    "table, column, width_name",
    [("rdf_labels", "label", "LABEL_WIDTH"), ("rdf_props", "key", "KEY_WIDTH"), ("rdf_edges", "p", "PREDICATE_WIDTH"),
     ("nodes", "node_id", "NODE_ID_WIDTH"), ("rdf_edges", "s", "NODE_ID_WIDTH"), ("rdf_edges", "o_id", "NODE_ID_WIDTH"),
     ("rdf_props", "val", "VALUE_WIDTH")],
)
def test_the_checked_widths_are_the_namespaces(conn, table, column, width_name):
    from iris_vector_graph import _widths

    cur = conn.cursor()
    cur.execute(
        "SELECT CHARACTER_MAXIMUM_LENGTH FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = 'Graph_KG' "
        "AND TABLE_NAME = ? AND COLUMN_NAME = ?", [table, column],
    )
    assert int(cur.fetchone()[0]) == getattr(_widths, width_name)


# ------------------------------------------------------------------------------- the backstop: a refusal not a width


@pytest.mark.parametrize("server_loader", (True, False), ids=("server_loader", "batch_sql"))
def test_bulk_create_nodes_raises_on_a_refused_row_that_is_not_a_width(conn, server_loader):
    """A NULL label (rdf_labels.label is NOT NULL, SQLCODE -108) passes the width check; the driver skipped its row
    of the batch, and the call answered every id. The read-back names it and nothing is written."""
    from iris_vector_graph.errors import GraphWriteError

    u = _uid()
    eng = _engine(conn, server_loader)
    with pytest.raises(GraphWriteError, match=r"bulk_create_nodes failed.*refused 1 label row"):
        eng.bulk_create_nodes([{"id": f"{u}a", "labels": ["A"]}, {"id": f"{u}b", "labels": ["B", None]}])
    assert _nodes(conn, u) == 0


def test_create_node_raises_on_a_refused_label_that_is_not_a_width(conn):
    from iris_vector_graph.errors import GraphWriteError

    u = _uid()
    eng = _engine(conn, False)
    with pytest.raises(GraphWriteError, match=r"create_node failed.*refused 1 of the 2 label rows"):
        eng.create_node(f"{u}a", labels=["A", None])
    assert _nodes(conn, u) == 0


def test_bulk_loader_refuses_a_null_predicate_under_nocheck(conn):
    """Under the default %NOINDEX %NOCHECK, IRIS checks no constraint: a NULL predicate was written, and every DELETE
    of rdf_edges in the namespace then failed (SQLCODE -415, <SUBSCRIPT> in %SQLDelete). It is refused first now."""
    from iris_vector_graph.bulk_loader import BulkLoader

    u = _uid()
    with pytest.raises(ValueError, match=r"BulkLoader.load_edges: edge 1 .*no source, predicate or target"):
        BulkLoader(conn).load_edges([(f"{u}a", "R", f"{u}b", None), (f"{u}c", None, f"{u}d", None)])
    assert _count(conn, "SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE s %STARTSWITH ?", [u]) == 0


# ------------------------------------------------------------------------------- row 3d: BulkLoader's long values


def _val(conn, nid, key):
    cur = conn.cursor()
    cur.execute('SELECT val FROM Graph_KG.rdf_props WHERE s = ? AND "key" = ?', [nid, key])
    row = cur.fetchone()
    return None if row is None else row[0]


def test_bulk_loader_stores_a_long_value_whole(conn):
    """rdf_props.val holds 64000 characters; BulkLoader cut every value to 60000 without a word (row 3d)."""
    from iris_vector_graph.bulk_loader import BulkLoader

    u = _uid()
    text = "v" * 62000
    loader = BulkLoader(conn)
    loader.load_nodes([(f"{u}a", {"namespace": "T470D", "doc": text})])
    loader.rebuild_all_indices()  # the %NOINDEX load leaves the lookups by s to the rebuild
    assert _val(conn, f"{u}a", "doc") == text


def test_bulk_loader_refuses_a_value_wider_than_the_column(conn):
    from iris_vector_graph.bulk_loader import BulkLoader

    u = _uid()
    with pytest.raises(ValueError, match=r"BulkLoader.load_nodes: node 1 property 'doc' value .*64001 characters.* 64000"):
        BulkLoader(conn).load_nodes([(f"{u}a", {"namespace": "T470D"}), (f"{u}b", {"doc": "v" * 64001})])
    assert _nodes(conn, u) == 0


def _engine_sql(conn):
    from iris_vector_graph import IRISGraphEngine

    return IRISGraphEngine(conn, embedding_dimension=rr.DIM)


def test_load_networkx_stores_a_long_value_whole(conn):
    """The engine's load_networkx cut every text value to 60000 characters without a word (row 3d)."""
    import networkx as nx

    u = _uid()
    g = nx.DiGraph()
    g.add_node(f"{u}a", type="T470D", doc="v" * 62000)
    _engine_sql(conn).load_networkx(g, auto_sync=False)
    assert _val(conn, f"{u}a", "doc") == "v" * 62000


def test_load_networkx_refuses_a_value_wider_than_the_column_and_writes_nothing(conn):
    import networkx as nx

    u = _uid()
    g = nx.DiGraph()
    g.add_node(f"{u}a", type="T470D", doc="ok")
    g.add_node(f"{u}b", type="T470D", doc="v" * 64001)
    with pytest.raises(ValueError, match=r"load_networkx: node .*property 'doc' value .*64001 characters.* 64000"):
        _engine_sql(conn).load_networkx(g, auto_sync=False)
    assert _nodes(conn, u) == 0


def _rdf(u, literal_len) -> str:
    from pathlib import Path

    scratch = Path.home() / ".cache" / "ivg-bench" / "rel-470" / "txn-scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    path = scratch / f"{u}.nt"
    path.write_text(
        f'<urn:{u}:a> <urn:p:name> "A" .\n'
        f"<urn:{u}:a> <urn:p:R470D> <urn:{u}:b> .\n"
        f'<urn:{u}:b> <http://example.org/p/doc> "{"v" * literal_len}" .\n'
    )
    return str(path)


def test_import_rdf_stores_a_long_literal_whole(conn):
    """import_rdf cut every literal to 64000 characters without a word; one that fits is stored whole."""
    u = _uid()
    _engine_sql(conn).import_rdf(_rdf(u, 62000), format="nt")
    assert _val(conn, f"urn:{u}:b", "doc") == "v" * 62000


def test_import_rdf_refuses_a_literal_wider_than_the_column_and_writes_nothing(conn):
    """On 4.6.5 the 64001-character literal was stored as its first 64000, and the call reported it written."""
    u = _uid()
    with pytest.raises(ValueError, match=r"import_rdf: .*'http://example.org/p/doc' literal .*64001 characters.* 64000"):
        _engine_sql(conn).import_rdf(_rdf(u, 64001), format="nt")
    assert _count(conn, "SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id %STARTSWITH ?", [f"urn:{u}:"]) == 0
