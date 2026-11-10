"""4.6.4, live: a node property written through the Python API reads back as the value written, as one a Cypher
`CREATE` wrote does.

Measured on 4.6.3 (407777a8, IRIS 2026.3, driver 5.4.0; 4.4.1 and 4.6.2 under 5.4.1 likewise):
`create_node("dxp1", labels=["P"], properties={"age": 30, "score": 0.5, "name": "a"})` then
`MATCH (n:P {node_id: 'dxp1'}) RETURN n.age, n.score, n.name, n.missing` answered `['30', '0.5', 'a', None]`.
Every Python path bound `prop_text(v)`, the string '30'; a Cypher write binds the int 30, which IRIS keeps as a
number in the VARCHAR row and `CY_TYPED` reads back as one.

Expected answers are the Python values written (VALUES): 30, 0.5, 2.0 (a float, not the integer 2), True, the
string '30' (written as a string, it stays one), [1, 'a', 2.5], {'x': 1, 'y': 'z'}, an integer past 64 bits as its
text (a Cypher parameter of one is stored so too), and None for a key never written. Each path's node is read
through Cypher on the default route and the SQL route and through `get_node()`. A Cypher `CREATE` of the same values
(without the map, which Cypher refuses as a property) is the control: it read so before 4.6.4.

Bolt is not read here: the 4.6.0 typed-read files (test_460_r218/r248) do not read through it either.
"""

from __future__ import annotations

import pytest

from tests.e2e import ns463

pytestmark = [pytest.mark.e2e, *ns463.requires_namespace]

LABEL = "T464"
VALUES = {"i": 30, "f": 0.5, "g": 2.0, "b": True, "s": "30", "l": [1, "a", 2.5], "m": {"x": 1, "y": "z"},
          "big": 2**70}
WANT = {"i": 30, "f": 0.5, "g": 2.0, "b": True, "s": "30", "l": [1, "a", 2.5], "m": {"x": 1, "y": "z"},
        "big": str(2**70)}
KEYS = list(VALUES)
PATHS = ("create_node", "store_node", "bulk_server", "bulk_sql", "reify_edge", "store_write_nodes",
         "bulk_loader", "bulk_loader_networkx", "engine_networkx")


def _id(path):
    return f"t464:{path}"


def _write(path, conn, engs):
    """Write VALUES to node _id(path), labelled LABEL, through one Python path."""
    from iris_vector_graph import IRISGraphEngine
    from iris_vector_graph.bulk_loader import BulkLoader
    from iris_vector_graph.capabilities import IRISCapabilities
    from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

    e = engs["default"]
    nid = _id(path)
    schema = e._ivg_schema
    if path == "create_node":
        assert e.create_node(nid, labels=[LABEL], properties=VALUES)
    elif path == "store_node":
        assert e.store_node(nid, properties=VALUES, labels=[LABEL])
    elif path == "bulk_server":
        e.capabilities = None
        assert e.capabilities.objectscript_deployed, "the server loader is not deployed here: this case proves nothing"
        assert e.bulk_create_nodes([{"id": nid, "labels": [LABEL], "properties": dict(VALUES)}]) == [nid]
    elif path == "bulk_sql":
        w = IRISGraphEngine(conn, namespace=ns463.NS, schema_prefix=schema)
        w.capabilities = IRISCapabilities()  # nothing deployed as far as it knows: the batch SQL path
        assert w.bulk_create_nodes([{"id": nid, "labels": [LABEL], "properties": dict(VALUES)}]) == [nid]
    elif path == "reify_edge":
        src, dst = f"{nid}:a", f"{nid}:b"
        assert e.create_node(src, labels=[f"{LABEL}E"]) and e.create_node(dst, labels=[f"{LABEL}E"])
        assert e.create_edge(src, "R464", dst)
        cur = conn.cursor()
        cur.execute(f"SELECT edge_id FROM {schema}.rdf_edges WHERE s = ? AND p = 'R464'", [src])
        edge_id = cur.fetchone()[0]
        assert e.reify_edge(edge_id, reifier_id=nid, label=LABEL, props=dict(VALUES)) == nid
    elif path == "store_write_nodes":
        store = IRISGraphStore(conn, namespace=ns463.NS, schema=schema)
        store.write_nodes([{"id": nid, "labels": [LABEL], "properties": dict(VALUES)}])
    elif path == "bulk_loader":
        BulkLoader(conn, schema=schema).load_nodes([(nid, dict(VALUES, namespace=LABEL))], use_noindex=False)
    elif path == "bulk_loader_networkx":
        nx = pytest.importorskip("networkx")
        G = nx.DiGraph()
        G.add_node(nid, namespace=LABEL, **VALUES)
        BulkLoader(conn, schema=schema).load_networkx(G, use_noindex=False, build_globals=False)
    elif path == "engine_networkx":
        nx = pytest.importorskip("networkx")
        G = nx.DiGraph()
        G.add_node(nid, type=LABEL, **VALUES)
        e.load_networkx(G, auto_sync=False)
    conn.commit()


@pytest.fixture(scope="module")
def engines():
    conn = ns463.connect()
    engs = ns463.engines(conn)
    _clean(engs["sql"])
    for path in PATHS:
        _write(path, conn, engs)
    ns463.rows(engs["sql"], f"CREATE (:{LABEL} {{node_id: $id, i: $i, f: $f, g: $g, b: $b, s: $s, l: $l, big: $big}})",
               {"id": _id("cypher"), **{k: v for k, v in VALUES.items() if k != "m"}})
    yield engs
    _clean(engs["sql"])
    conn.close()


def _clean(e):
    for lbl in (LABEL, f"{LABEL}E"):
        ns463.rows(e, f"MATCH (n:{lbl}) DETACH DELETE n")
    # DETACH DELETE of the reifier and the edge leaves the rdf_reifications row (an open 4.6.3 defect), which would
    # refuse the next run's reify_edge with -119
    cur = e.conn.cursor()
    cur.execute(f"DELETE FROM {e._ivg_schema}.rdf_reifications WHERE reifier_id = ?", [_id("reify_edge")])
    e.conn.commit()


def _want(keys):
    return ns463.typed([[WANT[k] for k in keys] + [None]])


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_cypher_written_values_read_as_written(engines, route):
    """The control: a Cypher CREATE's values (no map) read as written before 4.6.4 and after."""
    keys = [k for k in KEYS if k != "m"]
    q = f"MATCH (n:{LABEL} {{node_id: $id}}) RETURN {', '.join('n.' + k for k in keys)}, n.missing"
    assert ns463.typed(ns463.rows(engines[route], q, {"id": _id("cypher")})) == _want(keys)


@pytest.mark.parametrize("route", ns463.ROUTES)
@pytest.mark.parametrize("path", PATHS)
def test_api_written_values_read_as_written(engines, path, route):
    q = f"MATCH (n:{LABEL} {{node_id: $id}}) RETURN {', '.join('n.' + k for k in KEYS)}, n.missing"
    assert ns463.typed(ns463.rows(engines[route], q, {"id": _id(path)})) == _want(KEYS)


@pytest.mark.parametrize("path", PATHS)
def test_get_node_reads_values_as_written(engines, path):
    node = engines["default"].get_node(_id(path))
    assert node is not None
    assert ns463.typed([[node.get(k) for k in KEYS] + [node.get("missing")]]) == _want(KEYS)


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_the_lead_reproduction(engines, route):
    """The report's own case: create_node then MATCH ... RETURN n.age, n.score, n.name, n.missing."""
    e = engines["default"]
    nid = "t464:dxp1"
    try:
        assert e.create_node(nid, labels=[LABEL], properties={"age": 30, "score": 0.5, "name": "a"})
        e.conn.commit()
        got = ns463.rows(engines[route], f"MATCH (n:{LABEL} {{node_id: '{nid}'}}) "
                                         "RETURN n.age, n.score, n.name, n.missing")
        assert ns463.typed(got) == [[(30, "int"), (0.5, "float"), ("a", "str"), (None, "NoneType")]]
    finally:
        e.delete_node(nid)
        e.conn.commit()


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_api_written_number_compares_and_sorts_as_a_number(engines, route):
    """ORDER BY and a numeric WHERE over API-written numbers: 9.5 before 10.5 (text order would put 10.5 first)."""
    e = engines["default"]
    ids = ["t464:o1", "t464:o2"]
    try:
        e.bulk_create_nodes([{"id": ids[0], "labels": [LABEL], "properties": {"o": 10.5}},
                             {"id": ids[1], "labels": [LABEL], "properties": {"o": 9.5}}])
        e.conn.commit()
        q = f"MATCH (n:{LABEL}) WHERE n.node_id IN $ids AND n.o > 9 RETURN n.o ORDER BY n.o"
        assert ns463.typed(ns463.rows(engines[route], q, {"ids": ids})) == [[(9.5, "float")], [(10.5, "float")]]
        q = f"MATCH (n:{LABEL}) WHERE n.node_id IN $ids RETURN n.o * 2 AS d ORDER BY d"
        assert ns463.typed(ns463.rows(engines[route], q, {"ids": ids})) == [[(19.0, "float")], [(21.0, "float")]]
    finally:
        for nid in ids:
            e.delete_node(nid)
        e.conn.commit()


def test_text_a_4_6_3_write_left_reads_as_text_until_rewritten(engines):
    """4.6.3's create_node stored 30 and 0.5 as the strings '30' and '0.5', the same row a string written on purpose
    makes, so nothing can retype it: it reads as the string. The documented repair (KNOWN_ISSUES, migration notes), for
    a property the caller knows is numeric: read it and write the number back with store_node. (A Cypher
    `SET n.age = toInteger(n.age)` failed with SQLCODE -23, and the WITH form wrote nothing, its value being equal to
    the stored text, until 4.7.0, which writes the number through Prop_Set: tests/e2e/test_470_prop_retype_e2e.py.)"""
    e = engines["default"]
    nid = "t464:old"
    try:
        assert e.create_node(nid, labels=[LABEL], properties={"age": "30", "score": "0.5"})
        e.conn.commit()
        q = f"MATCH (n:{LABEL} {{node_id: $id}}) RETURN n.age, n.score"
        for route in ns463.ROUTES:
            assert ns463.typed(ns463.rows(engines[route], q, {"id": nid})) == [[("30", "str"), ("0.5", "str")]]
        # the recipe as the docs give it
        for node_id, age, score in ns463.rows(
            e, f"MATCH (n:{LABEL}) WHERE n.node_id = $id AND n.age IS NOT NULL RETURN n.node_id, n.age, n.score",
            {"id": nid},
        ):
            if isinstance(age, str):
                e.store_node(node_id, properties={"age": int(age), "score": float(score)})
        e.conn.commit()
        for route in ns463.ROUTES:
            assert ns463.typed(ns463.rows(engines[route], q, {"id": nid})) == [[(30, "int"), (0.5, "float")]]
        node = e.get_node(nid)
        assert ns463.typed([[node["age"], node["score"]]]) == [[(30, "int"), (0.5, "float")]]
    finally:
        e.delete_node(nid)
        e.conn.commit()


#: Not 1e300 or 1e-300, whose IRIS text is 40 characters or more, which CY_TYPED leaves unmarked: the default route
#: reads 1e300 as an int, and under driver 5.4.1 the SQL route reads both as IRIS's text, whoever wrote them (a Cypher
#: CREATE of them too, measured). An open defect of the read, not of these writes.
FLOATS = {"a": 0.1, "b": 1 / 3, "c": 1e20, "d": 1e-10, "e": -2.5e-7, "f": 123456789.123}
#: NaN and the infinities stay their text, as 4.6.3 stored them: a stored $DOUBLE NaN fails a read of a list holding it
#: (`collect()` then UNWIND, `any(e IN [n.p] ...)`, -400 "Parsing error": IRIS's JSON has no NaN; KNOWN_ISSUES),
#: which the 4.6.4 gate found when the API stored them as $DOUBLEs (b5b6dabf).
NON_FINITE = {"g": (float("inf"), "inf"), "h": (float("-inf"), "-inf"), "n": (float("nan"), "nan")}


@pytest.mark.parametrize("path", ["create_node", "bulk_server", "bulk_sql"])
def test_floats_round_trip_exactly(engines, path):
    """Each finite float reads back as the same float (the $DOUBLE a Cypher write of it stores); NaN and the
    infinities read back as their text, and a list read over them answers."""
    from iris_vector_graph import IRISGraphEngine
    from iris_vector_graph.capabilities import IRISCapabilities

    e = engines["default"]
    nid = f"t464:floats:{path}"
    props = {**FLOATS, **{k: v for k, (v, _) in NON_FINITE.items()}}
    try:
        if path == "create_node":
            assert e.create_node(nid, labels=[LABEL], properties=props)
        else:
            w = e
            if path == "bulk_sql":
                w = IRISGraphEngine(e.conn, namespace=ns463.NS, schema_prefix=e._ivg_schema)
                w.capabilities = IRISCapabilities()
            else:
                e.capabilities = None
                assert e.capabilities.objectscript_deployed
            assert w.bulk_create_nodes([{"id": nid, "labels": [LABEL], "properties": dict(props)}]) == [nid]  # the batch path adds "id"
        e.conn.commit()
        q = f"MATCH (n:{LABEL} {{node_id: $id}}) RETURN {', '.join('n.' + k for k in props)}"
        want = list(FLOATS.values()) + [text for _, text in NON_FINITE.values()]
        listed = (f"MATCH (n:{LABEL} {{node_id: $id}}) WITH n, collect(n.n) AS c UNWIND c AS x "
                  "RETURN x, any(y IN [n.h] WHERE y IS NOT NULL)")
        for route in ns463.ROUTES:
            (row,) = ns463.rows(engines[route], q, {"id": nid})
            assert [(type(v).__name__, v) for v in row] == [(type(v).__name__, v) for v in want], (route, row)
            assert [list(r) for r in ns463.rows(engines[route], listed, {"id": nid})] == [["nan", True]], route
        node = e.get_node(nid)
        assert [node[k] for k in props] == want
    finally:
        e.delete_node(nid)
        e.conn.commit()
