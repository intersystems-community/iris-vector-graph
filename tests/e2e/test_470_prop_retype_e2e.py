"""4.7.0 row 35, live: a node property's new value with the stored text and another type is written, in one write.

IRIS's filer writes no value whose text equals the stored one. Measured on 2026.3 (IVG470CY): over the string '30',
`UPDATE rdf_props SET val = ?` with a bound integer 30, a UDF's number, UPDATE %NOCHECK and %OpenId / %Save each
answered one row and left the string ($ListBuild type byte \\x01). So `SET n.p = toInteger(n.p)` and `SET n.p = 30`
left an imported '30' a string. A first fix wrote the row twice (a CHAR(1) marker, then the number), and a second
connection reading between the writes answered '\\x0130'.

Graph.KG.PropWrite.SetVal (SQL Graph_KG.Prop_Set, the SET of each UPDATE of rdf_props.val) writes such a value into
the row's val slot of the data global, once. Known answers: after the SET the stored value is the integer 30
(type byte \\x04), `n.p + 1` is 31, `WHERE n.p = 30` matches, and each index of rdf_props finds the row once; a reader
on another connection, after any statement of the write, reads '30' or 30 and nothing else; a rollback leaves the
string. The storage layout the method relies on (one $ListBuild node per row, val in slot 4) is read from the
dictionary here, independently, so a change to it fails this file.
"""

from __future__ import annotations

import pytest

from tests.e2e import ns463

pytestmark = [pytest.mark.e2e, *ns463.requires_namespace]

LABEL = "T470R"
STRING_30 = b"\x04\x0130"  # $ListBuild("30")
INTEGER_30 = b"\x03\x04\x1e"  # $ListBuild(30)


@pytest.fixture()
def engines():
    conn = ns463.connect()
    engs = ns463.engines(conn)
    ns463.rows(engs["sql"], f"MATCH (n:{LABEL}) DETACH DELETE n")
    ns463.rows(engs["sql"], f"CREATE (:{LABEL} {{id: 'r1', p: '30', q: '30'}})")
    conn.commit()
    yield engs
    ns463.rows(engs["sql"], f"MATCH (n:{LABEL}) DETACH DELETE n")
    conn.commit()
    conn.close()


def _schema(e):
    return e._ivg_schema


def _stored(conn, schema, key="p"):
    """The stored value's $ListBuild and val_key of node r1's `key`, every row of it."""
    cur = conn.cursor()
    cur.execute(
        f"SELECT $LISTBUILD(p.val), p.val_key FROM {schema}.rdf_props p "
        f"JOIN {schema}.rdf_labels l ON l.s = p.s AND l.label = ? WHERE p.\"key\" = ?", [LABEL, key])
    return [tuple(r) for r in cur.fetchall()]


def _node_id(conn, schema):
    cur = conn.cursor()
    cur.execute(f"SELECT s FROM {schema}.rdf_labels WHERE label = ?", [LABEL])
    return cur.fetchone()[0]


@pytest.mark.parametrize("cypher", [
    f"MATCH (n:{LABEL} {{id: 'r1'}}) SET n.p = toInteger(n.p)",
    f"MATCH (n:{LABEL} {{id: 'r1'}}) SET n.p = 30",
    f"MATCH (n:{LABEL} {{id: 'r1'}}) SET n.p = $v",
    f"MATCH (n:{LABEL} {{id: 'r1'}}) SET n += {{p: 30}}",
    f"MATCH (n:{LABEL} {{id: 'r1'}}) SET n.p = toInteger(n.q)",
    f"MATCH (n:{LABEL} {{id: 'r1'}}) WITH n, toInteger(n.p) AS v SET n.p = v",
    f"MERGE (n:{LABEL} {{id: 'r1'}}) ON MATCH SET n.p = 30",
])
def test_the_number_is_stored_and_read(engines, cypher):
    e = engines["sql"]
    ns463.rows(e, cypher, {"v": 30})
    e.conn.commit()
    assert _stored(e.conn, _schema(e)) == [(INTEGER_30, "30")]
    for route in ns463.ROUTES:
        q = f"MATCH (n:{LABEL} {{id: 'r1'}}) RETURN n.p, n.p + 1"
        assert ns463.typed(ns463.rows(engines[route], q)) == [[(30, "int"), (31, "int")]]
        assert ns463.rows(engines[route], f"MATCH (n:{LABEL}) WHERE n.p = 30 RETURN n.id") == [["r1"]]


def test_a_number_to_its_string_is_stored_and_read(engines):
    e = engines["sql"]
    ns463.rows(e, f"MATCH (n:{LABEL} {{id: 'r1'}}) SET n.p = toInteger(n.p)")
    ns463.rows(e, f"MATCH (n:{LABEL} {{id: 'r1'}}) SET n.p = toString(n.p)")
    e.conn.commit()
    assert _stored(e.conn, _schema(e)) == [(STRING_30, "30")]


def test_each_index_finds_the_row_once(engines):
    e = engines["sql"]
    ns463.rows(e, f"MATCH (n:{LABEL} {{id: 'r1'}}) SET n.p = toInteger(n.p)")
    e.conn.commit()
    s, cur = _node_id(e.conn, _schema(e)), e.conn.cursor()
    t = f"{_schema(e)}.rdf_props"
    for sql, params in (
        (f"SELECT COUNT(*) FROM {t} WHERE \"key\" = 'p' AND val_key = '30' AND s = ?", [s]),
        (f"SELECT COUNT(*) FROM %IGNOREINDEX * {t} WHERE \"key\" = 'p' AND val_key = '30' AND s = ?", [s]),
        (f"SELECT COUNT(*) FROM {t} WHERE s = ? AND \"key\" = 'p'", [s]),
        (f"SELECT COUNT(*) FROM {t} WHERE \"key\" = 'p' AND s = ?", [s]),
        (f"SELECT COUNT(*) FROM {t} WHERE %ID %FIND search_index(idx_props_val_ifind, '30') AND s = ? AND \"key\" = 'p'",
         [s]),
    ):
        cur.execute(sql, params)
        assert cur.fetchone()[0] == 1, sql


class _Hooked:
    """A connection whose cursors call `after(sql)` after each statement they run."""

    def __init__(self, conn, after):
        self._conn, self._after = conn, after

    def cursor(self):
        return _HookedCursor(self._conn.cursor(), self._after)

    def __getattr__(self, name):
        return getattr(self._conn, name)


class _HookedCursor:
    def __init__(self, cur, after):
        self._cur, self._after = cur, after

    def execute(self, sql, *args, **kwargs):
        out = self._cur.execute(sql, *args, **kwargs)
        self._after(sql)
        return out

    def __getattr__(self, name):
        return getattr(self._cur, name)


def _run_hooked(monkeypatch, engine, cypher, after):
    from iris_vector_graph.stores import iris_sql_store

    real = iris_sql_store.IRISGraphStore.execute_transaction

    def hooked(store, stmts, params_list):
        conn = store.conn
        store.conn = _Hooked(conn, after)
        try:
            return real(store, stmts, params_list)
        finally:
            store.conn = conn

    monkeypatch.setattr(iris_sql_store.IRISGraphStore, "execute_transaction", hooked)
    return engine.execute_cypher(cypher, {}, raise_on_error=True)


def test_a_second_reader_sees_the_old_value_or_the_new_and_nothing_else(engines, monkeypatch):
    other = ns463.connect()
    try:
        reader = ns463.engines(other)["sql"]
        seen = []

        def read(sql):
            rows = reader.execute_cypher(f"MATCH (n:{LABEL} {{id: 'r1'}}) RETURN n.p, keys(n)", {}).rows
            seen.append((sql[:60], rows))

        _run_hooked(monkeypatch, engines["sql"], f"MATCH (n:{LABEL} {{id: 'r1'}}) SET n.p = toInteger(n.p)", read)
        assert seen, "no statement ran"
        for sql, rows in seen:
            assert len(rows) == 1, (sql, rows)
            value, keys = rows[0]
            assert value in ("30", 30) and type(value) in (str, int), (sql, value)
            assert "p" in keys, (sql, keys)
    finally:
        other.close()


def test_a_rollback_leaves_the_string(engines, monkeypatch):
    e = engines["sql"]

    def fail(sql):
        if "Prop_Set" in sql:
            raise RuntimeError("injected failure after the write")

    with pytest.raises(Exception, match="injected failure"):
        _run_hooked(monkeypatch, e, f"MATCH (n:{LABEL} {{id: 'r1'}}) SET n.p = toInteger(n.p)", fail)
    monkeypatch.undo()
    assert _stored(e.conn, _schema(e)) == [(STRING_30, "30")]
    assert ns463.typed(ns463.rows(e, f"MATCH (n:{LABEL} {{id: 'r1'}}) RETURN n.p")) == [[("30", "str")]]


def test_the_storage_layout_is_the_one_the_method_writes(engines):
    """rdf_props's class keeps one $ListBuild node per row at the row id, its fourth slot val, read here from the
    dictionary; Graph.KG.PropWrite.Layout answers the same global and slot, and the slot holds the row's val."""
    import iris

    e = engines["sql"]
    schema, cur = _schema(e), e.conn.cursor()
    cur.execute("SELECT Name FROM %Dictionary.CompiledClass WHERE SqlSchemaName = ? AND SqlTableName = 'rdf_props'",
                [schema])
    cls = cur.fetchone()[0]
    cur.execute("SELECT DataLocation, Type FROM %Dictionary.CompiledStorage WHERE parent = ?", [cls])
    storage = cur.fetchall()
    assert len(storage) == 1 and storage[0][1] == "%Storage.Persistent", storage
    location = storage[0][0]
    cur.execute("SELECT Structure, Subscript FROM %Dictionary.CompiledStorageData WHERE parent = ?", [cls + "||Default"])
    assert [tuple(r) for r in cur.fetchall()] == [("listnode", None)]
    cur.execute("SELECT Name, Value FROM %Dictionary.CompiledStorageDataValue WHERE parent = ?",
                [cls + "||Default||" + cls.split(".")[-1] + "DefaultData"])
    assert sorted((int(n), v) for n, v in cur.fetchall()) == [
        (1, "graphid"), (2, "s"), (3, "key"), (4, "val"), (5, "valkey")]

    native = iris.createIRIS(e.conn)
    layout = iris.IRISList(native.classMethodBytes("Graph.KG.PropWrite", "Layout", f"{schema}.rdf_props"))
    assert [layout.get(1), layout.get(2), layout.get(3)] == [location, 4, 5]

    s = _node_id(e.conn, schema)
    cur.execute(f"SELECT %ID, val FROM {schema}.rdf_props WHERE s = ? AND \"key\" = 'p'", [s])
    row_id, val = cur.fetchone()
    node = iris.IRISList(native.getBytes(location.lstrip("^"), row_id))
    assert node.count() == 5 and node.get(4) == val == "30"


def test_a_row_that_does_not_hold_the_old_value_is_refused(engines):
    """The layout check: SetVal writes only into a slot holding exactly the value the UPDATE read. Called with an old
    value the row does not hold ('31' for a row holding '30', same text as the new 31), it raises and writes nothing."""
    e = engines["sql"]
    schema, cur = _schema(e), e.conn.cursor()
    cur.execute(f"SELECT %ID FROM {schema}.rdf_props WHERE s = ? AND \"key\" = 'p'", [_node_id(e.conn, schema)])
    row_id = cur.fetchone()[0]
    with pytest.raises(Exception, match="does not hold val"):
        cur.execute(f"SELECT {schema}.Prop_Set(?, '31', ?)", [row_id, 31])
        cur.fetchall()
    assert _stored(e.conn, schema) == [(STRING_30, "30")]
