"""4.6.4: a node property written through the Python API is bound as the value it is, as a Cypher write binds it.

Before 4.6.4 every Python write path bound `prop_text(v)`, so 30 was stored as the string '30' and
`MATCH (n) RETURN n.age` answered '30' where a Cypher `CREATE` of the same value answered 30. A Cypher write binds an
int or a float as itself (IRIS keeps the number's type in the VARCHAR row, which `CY_TYPED` reads) and a boolean,
list or map as its text. These tests pin what each Python path binds; tests/e2e/test_464_api_typed_props_e2e.py
reads the stored values back.

The paths: `create_node`, `store_node`, `bulk_create_nodes` (batch SQL and the server loader's JSON),
`reify_edge(props=...)`, `IRISGraphStore.write_nodes`, `BulkLoader.load_nodes` / `load_networkx` and the engine's
`load_networkx`.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.prop_values import prop_value

#: What each Python value is bound as: the number itself, else the text a Cypher write stores.
CASES = [
    (30, 30, int),
    (0, 0, int),
    (-7, -7, int),
    (2**63 - 1, 2**63 - 1, int),
    (-(2**63), -(2**63), int),
    (0.5, 0.5, float),
    (2.0, 2.0, float),
    (-0.25, -0.25, float),
    (True, "true", str),
    (False, "false", str),
    ("30", "30", str),
    ("0.5", "0.5", str),
    ("a", "a", str),
    ("", "", str),
    ([1, "a", 2.5], '[1, "a", 2.5]', str),
    ({"x": 1, "y": "z"}, '{"x": 1, "y": "z"}', str),
    # Past a 64-bit integer IRIS holds no exact integer; a Cypher write stores such a parameter as its text too.
    (2**70, str(2**70), str),
]

VALUES = {"i": 30, "f": 0.5, "g": 2.0, "b": True, "s": "30", "l": [1, "a", 2.5], "m": {"x": 1, "y": "z"}}
BOUND = {"i": 30, "f": 0.5, "g": 2.0, "b": "true", "s": "30", "l": '[1, "a", 2.5]', "m": '{"x": 1, "y": "z"}'}


@pytest.mark.parametrize("value,bound,kind", CASES, ids=[repr(c[0])[:20] for c in CASES])
def test_prop_value(value, bound, kind):
    got = prop_value(value)
    assert (got, type(got)) == (bound, kind)


@pytest.mark.parametrize("value,text", [(float("nan"), "nan"), (float("inf"), "inf"), (float("-inf"), "-inf")])
def test_non_finite_floats_bind_as_their_text(value, text):
    # A stored $DOUBLE NaN or infinity fails a list read over it (`collect(n.p)` then UNWIND, `any(e IN [n.p] ...)`:
    # IRIS's JSON has no NaN, KNOWN_ISSUES), which a Cypher write of $v = nan already hits; the API keeps 4.6.3's text.
    assert (prop_value(value), type(prop_value(value))) == (text, str)


def test_int_and_float_subclasses_bind_as_plain_numbers():
    import enum

    class Level(enum.IntEnum):
        HIGH = 3

    class F(float):
        pass

    assert (prop_value(Level.HIGH), type(prop_value(Level.HIGH))) == (3, int)
    assert (prop_value(F(1.5)), type(prop_value(F(1.5)))) == (1.5, float)


def test_numpy_scalars_bind_as_numbers():
    np = pytest.importorskip("numpy")
    assert (prop_value(np.int64(4)), type(prop_value(np.int64(4)))) == (4, int)
    assert (prop_value(np.float32(0.5)), type(prop_value(np.float32(0.5)))) == (0.5, float)
    assert (prop_value(np.bool_(True)), type(prop_value(np.bool_(True)))) == ("true", str)


# ── engine doubles ────────────────────────────────────────────────────────────────────────────────────────────────


def _engine():
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchone.return_value = (4,)
    cursor.fetchall.return_value = []
    cursor.description = []
    cursor.rowcount = 1
    engine = IRISGraphEngine(conn, embedding_dimension=4)
    cursor.execute.reset_mock()
    cursor.executemany.reset_mock()
    return engine, cursor


def _prop_rows(cursor, node_id):
    """{key: bound val} from every rdf_props INSERT (execute and executemany) naming node_id."""
    out = {}

    def take(sql, row):
        row = list(row)
        if node_id not in row:
            return
        cols = sql.split("(", 1)[1].split(")", 1)[0].replace('"', "").replace(" ", "").split(",")
        start = row.index(node_id) - cols.index("s")
        rec = dict(zip(cols, row[start:start + len(cols)]))
        out[rec["key"]] = rec["val"]

    for call in cursor.executemany.call_args_list:
        sql = " ".join(str(call.args[0]).split())
        if "INSERT" in sql and "rdf_props" in sql:
            for row in call.args[1]:
                take(sql, row)
    for call in cursor.execute.call_args_list:
        sql = " ".join(str(call.args[0]).split())
        if "INSERT" in sql and "rdf_props" in sql and len(call.args) > 1:
            take(sql, call.args[1])
    return out


def _typed(d):
    return {k: (v, type(v).__name__) for k, v in d.items()}


def _expect(**extra):
    return _typed({**BOUND, **extra})


def test_create_node_binds_numbers():
    engine, cursor = _engine()
    engine.create_node("n464", labels=["P"], properties=VALUES)
    assert _typed(_prop_rows(cursor, "n464")) == _expect(id="n464")


def test_store_node_binds_numbers():
    engine, cursor = _engine()
    engine.store_node("n464", properties=VALUES, labels=["P"])
    assert _typed(_prop_rows(cursor, "n464")) == _expect(id="n464")


def test_bulk_create_nodes_sql_path_binds_numbers():
    engine, cursor = _engine()
    engine.capabilities = None
    engine._capabilities = None  # nothing known: the batch SQL path
    engine.bulk_create_nodes([{"id": "n464", "labels": ["P"], "properties": dict(VALUES)}])
    assert _typed(_prop_rows(cursor, "n464")) == _expect(id="n464")


def test_bulk_create_nodes_server_loader_sends_numbers():
    from iris_vector_graph.capabilities import IRISCapabilities

    engine, _ = _engine()
    engine.capabilities = IRISCapabilities(objectscript_deployed=True)
    sent = []

    def call(_iris, cls, method, payload):
        assert (cls, method) == ("Graph.KG.EdgeScan", "BulkIngestNodesSQL")
        chunk = json.loads(payload)
        sent.extend(chunk)
        return len(chunk)

    with patch.object(IRISGraphEngine, "_iris_obj", return_value=MagicMock()), patch(
        "iris_vector_graph.schema._call_classmethod_large", side_effect=call
    ):
        assert engine.bulk_create_nodes([{"id": "n464", "labels": ["P"], "properties": dict(VALUES)}]) == ["n464"]
    (node,) = sent
    # A JSON number cannot say 2.0 from 2, so a float goes as its repr and its key in `doubles`, which the class
    # stores as $DOUBLE (a Cypher write's float); an int is a JSON integer, stored as an IRIS integer.
    assert _typed(node["props"]) == _expect(id="n464", f="0.5", g="2.0")
    assert sorted(node["doubles"]) == ["f", "g"]


def test_bulk_create_nodes_server_loader_sends_no_doubles_key_without_floats():
    from iris_vector_graph.capabilities import IRISCapabilities

    engine, _ = _engine()
    engine.capabilities = IRISCapabilities(objectscript_deployed=True)
    sent = []

    def call(_iris, cls, method, payload):
        sent.extend(json.loads(payload))
        return len(json.loads(payload))

    with patch.object(IRISGraphEngine, "_iris_obj", return_value=MagicMock()), patch(
        "iris_vector_graph.schema._call_classmethod_large", side_effect=call
    ):
        engine.bulk_create_nodes([{"id": "n464", "properties": {"i": 1}}])
    assert sent == [{"id": "n464", "labels": [], "props": {"id": "n464", "i": 1}}]


def test_reify_edge_binds_numbers():
    engine, cursor = _engine()
    cursor.fetchone.return_value = ("e1",)
    with patch.object(IRISGraphEngine, "create_node", return_value=True):
        engine.reify_edge("e1", reifier_id="r464", props=dict(VALUES))
    assert _typed(_prop_rows(cursor, "r464")) == _expect()


def test_store_write_nodes_binds_values_as_create_node_does():
    from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    store = IRISGraphStore(conn)
    store.write_nodes([{"id": "n464", "labels": ["P"], "properties": dict(VALUES, name="TP53")}])
    # Before 4.6.4 a string was stored JSON-quoted ('"TP53"') and every other value as its JSON text, which the
    # store's own get_nodes (CY_TYPED since 4.6.0 R307) read back as '"TP53"' and '30'.
    assert _typed(_prop_rows(cursor, "n464")) == _expect(name="TP53")


def test_bulk_loader_load_nodes_binds_numbers():
    from iris_vector_graph.bulk_loader import BulkLoader

    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchall.return_value = []
    loader = BulkLoader(conn)
    loader.load_nodes([("n464", dict(VALUES, namespace="P"))], use_noindex=False)
    assert _typed(_prop_rows(cursor, "n464")) == _expect(id="n464")


def test_bulk_loader_load_nodes_keeps_long_text_whole():
    """4.7.0 row 3d: text up to rdf_props.val's 64000 characters is stored whole (it was cut to 60000); wider is
    refused before anything is written (tests/e2e/test_470_batch_widths_e2e.py)."""
    from iris_vector_graph.bulk_loader import BulkLoader

    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchall.return_value = []
    BulkLoader(conn).load_nodes([("n464", {"t": "x" * 63000, "i": 10**12})], use_noindex=False)
    got = _prop_rows(cursor, "n464")
    assert (len(got["t"]), got["i"], type(got["i"])) == (63000, 10**12, int)
    with pytest.raises(ValueError, match="70000 characters; the column holds 64000"):
        BulkLoader(conn).load_nodes([("n465", {"t": "x" * 70000})], use_noindex=False)


def test_engine_load_networkx_passes_values_through():
    nx = pytest.importorskip("networkx")
    G = nx.DiGraph()
    G.add_node("n464", type="P", **VALUES, long="x" * 62000, other=(1, 2))
    engine, _ = _engine()
    got = {}

    def create_node(node_id, labels=None, properties=None, graph=None):
        got.update(properties)
        return True

    with patch.object(engine, "create_node", side_effect=create_node), patch.object(engine, "sync"):
        engine.load_networkx(G)
    # create_node binds them (above); load_networkx no longer turns each into str() first, which stored a list as
    # Python's repr "[1, 'a', 2.5]" and a bool as 'True'. A value of another type is still its str(); long text is
    # whole up to the column's 64000 characters (4.7.0 row 3d: it was cut to 60000).
    assert {k: got[k] for k in VALUES} == VALUES
    assert (len(got["long"]), got["other"]) == (62000, "(1, 2)")
