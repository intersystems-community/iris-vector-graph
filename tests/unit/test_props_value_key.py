"""Lookup of a node by a property value: an index on (key, first 128 characters of val).

`rdf_props.val` is VARCHAR(64000) %EXACT and cannot be an index subscript (v1.4.8 removed `(key, val)` for <SUBSCRIPT> on long
values), so every `n.prop = value` scanned that key's rows: an SNB SF1 profile read by id took 7 s, 0.6 ms with an index. The
column `val_key` holds the prefix (computed on every write); the translator adds `val_key = '<prefix>'` next to the exact
`val = ?`, and only for an engine that has the column, so an install without it gets the same SQL as before.
"""

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql
from iris_vector_graph.schema import GraphSchema


def _sql(q, params=None, engine=None):
    r = translate_to_sql(parse_query(q), params or {}, engine=engine)
    return r.sql if isinstance(r.sql, str) else "\n;\n".join(r.sql)


class _Engine:
    """What the translator reads from an engine: no table or relationship mappings, and the probe flag."""

    def __init__(self, flag):
        if flag is not None:
            self._props_value_key = flag

    def get_table_mapping(self, *a, **k):
        return None

    def get_rel_mapping(self, *a, **k):
        return None


ON = _Engine(True)


def test_where_equality_on_a_property_adds_the_prefix_predicate():
    sql = _sql("MATCH (p:Person) WHERE p.sid = $id RETURN p.firstName", {"id": "933"}, ON)
    assert ".val_key = '933'" in sql and ".val = ?" in sql, sql


def test_pattern_map_equality_adds_the_prefix_predicate():
    sql = _sql("MATCH (p:Person {sid: $id}) RETURN p.firstName", {"id": "933"}, ON)
    assert ".val_key = '933'" in sql, sql


def test_a_literal_on_either_side_and_a_long_value_is_cut_to_128_characters():
    long = "x" * 300
    sql = _sql(f"MATCH (p:Person) WHERE '{long}' = p.note RETURN p", None, ON)
    assert f".val_key = '{'x' * 128}'" in sql and f"'{'x' * 129}'" not in sql.split("val_key")[1][:200], sql


def test_quotes_are_escaped():
    sql = _sql("MATCH (p:Person) WHERE p.name = $n RETURN p", {"n": "O'Brien"}, ON)
    assert ".val_key = 'O''Brien'" in sql, sql


def test_no_prefix_predicate_without_the_column():
    for engine in (None, _Engine(None), _Engine(False)):
        assert "val_key" not in _sql("MATCH (p:Person) WHERE p.sid = $id RETURN p", {"id": "1"}, engine)


def test_a_mock_engine_does_not_switch_it_on():
    from unittest.mock import MagicMock

    engine = _Engine(None)
    engine._props_value_key = MagicMock()  # truthy, but not True
    assert "val_key" not in _sql("MATCH (p:Person) WHERE p.sid = $id RETURN p", {"id": "1"}, engine)


def test_only_string_equality_gets_it():
    for q, p in (("MATCH (p) WHERE p.age = 3 RETURN p", None), ("MATCH (p) WHERE p.sid <> $id RETURN p", {"id": "1"}),
                 ("MATCH (p) WHERE p.sid > $id RETURN p", {"id": "1"}), ("MATCH (p) WHERE p.ok = true RETURN p", None)):
        assert "val_key" not in _sql(q, p, ON), q


def test_the_schema_declares_the_column_and_its_index():
    ddl = GraphSchema.get_base_schema_sql()
    assert "val_key" in ddl and "COMPUTECODE" in ddl and "COMPUTEONCHANGE (val)" in ddl
    names = [n for n, _ in GraphSchema._BULK_INDEX_DDL]
    assert "idx_props_key_valkey" in names


def test_the_migration_adds_the_column_backfills_then_indexes_in_that_order():
    """The index is created last, so its presence means the backfill finished; the engine keys on it."""
    from unittest.mock import MagicMock

    cur = MagicMock()
    executed = []
    cur.execute.side_effect = lambda sql, *a: executed.append(" ".join(str(sql).split()))
    cur.fetchone.side_effect = [None, None]  # no column yet; no index yet
    cur.rowcount = 0  # the backfill's UPDATE reports no rows left
    status = GraphSchema.ensure_props_value_key(cur)
    add = next(i for i, s in enumerate(executed) if "ADD COLUMN val_key" in s)
    fill = next(i for i, s in enumerate(executed) if s.startswith("UPDATE") and "val_key IS NULL" in s)
    idx = next(i for i, s in enumerate(executed) if "CREATE INDEX idx_props_key_valkey" in s)
    assert add < fill < idx, executed
    assert status["column"] == "added" and status["index"] == "created"


def test_the_snapshot_does_not_export_the_computed_column():
    import inspect

    from iris_vector_graph._engine import snapshot

    assert '"val_key"' in inspect.getsource(snapshot)


def test_the_engine_probe_needs_both_column_and_index(monkeypatch):
    from unittest.mock import MagicMock

    from iris_vector_graph.engine import IRISGraphEngine

    for column, index, expected in ((1, 1, True), (1, 0, False), (0, 0, False)):
        conn = MagicMock()
        cur = conn.cursor.return_value
        cur.fetchone.side_effect = [(column,), (index,)]
        engine = IRISGraphEngine.__new__(IRISGraphEngine)
        engine.conn = conn
        engine._props_value_key_cached = None
        assert engine._props_value_key is expected, (column, index)


def test_the_backfill_loop_stops_on_a_cursor_that_reports_nothing_useful():
    """A MagicMock cursor (rowcount and counts are mocks) must not loop forever."""
    from unittest.mock import MagicMock

    status = GraphSchema.ensure_props_value_key(MagicMock())
    assert status["backfilled"] == 0


def test_a_stalled_backfill_stops_and_does_not_create_the_index():
    """A cursor whose count never drops (a stub that answers (384,) to everything, or an UPDATE that changes nothing)."""
    from unittest.mock import MagicMock

    cur = MagicMock()
    cur.fetchone.return_value = (384,)
    status = GraphSchema.ensure_props_value_key(cur)
    assert status["backfill_stalled"] == 384 and status["index"].startswith("not created")
    assert not any("CREATE INDEX idx_props_key_valkey" in str(c) for c in cur.execute.call_args_list)


# --- The index named idx_props_key_valkey must be on (key, val_key), never on (key, val) -----------------------------
#
# IRIS accepts `CREATE INDEX ... (key, val_key)` on a table that has no `val_key` column yet and builds the index on
# `val` instead (INFORMATION_SCHEMA then lists the columns `key`, `val`). The base DDL ran that statement on every
# initialize_schema(), before `ensure_props_value_key` had added the column, so on a pre-4.3 install it indexed `val`
# itself: a long value is a <SUBSCRIPT> error, and enough of them is `SQLCODE -400 ERROR #5017: Too many errors`.
# With no long value the wrong index was kept under the right name and the migration, which only checked the name,
# skipped the real one.


def test_the_base_ddl_does_not_create_the_value_key_index():
    """The index is the migration's to create: it is the only place that knows whether the column exists."""
    ddl = GraphSchema.get_base_schema_sql()
    assert "val_key  VARCHAR(128)" in ddl, "the fresh-install table still declares the column"
    assert "CREATE INDEX idx_props_key_valkey" not in ddl
    from iris_vector_graph.utils import _split_sql_statements

    assert not [s for s in _split_sql_statements(ddl) if "val_key" in s and s.lstrip().upper().startswith("CREATE INDEX")]


def _migration_cursor(index_columns):
    """A cursor for ensure_props_value_key: the column exists, the backfill has nothing left, and the catalog lists
    `index_columns` for idx_props_key_valkey (None: no such index)."""
    from unittest.mock import MagicMock

    cur = MagicMock()
    executed = []
    state = {}

    def execute(sql, *a):
        s = " ".join(str(sql).split())
        executed.append(s)
        state["last"] = s

    def fetchone():
        s = state["last"]
        if "COLUMN_NAME = 'val_key'" in s:
            return (1,)
        if "FROM INFORMATION_SCHEMA.INDEXES" in s:
            return None if index_columns is None else (1,)
        return None

    def fetchall():
        s = state["last"]
        if "FROM INFORMATION_SCHEMA.INDEXES" in s and index_columns is not None:
            return [(c,) for c in index_columns]
        return []

    cur.execute.side_effect = execute
    cur.fetchone.side_effect = fetchone
    cur.fetchall.side_effect = fetchall
    cur.rowcount = 0
    return cur, executed


def test_an_index_of_that_name_on_val_is_dropped_and_rebuilt_on_val_key():
    cur, executed = _migration_cursor(["key", "val"])
    status = GraphSchema.ensure_props_value_key(cur)
    drop = next(i for i, s in enumerate(executed) if s.startswith("DROP INDEX") and "idx_props_key_valkey" in s)
    create = next(i for i, s in enumerate(executed) if "CREATE INDEX idx_props_key_valkey" in s)
    assert drop < create, executed
    assert status["index"].startswith("rebuilt"), status


def test_an_index_on_key_and_val_key_is_left_alone():
    cur, executed = _migration_cursor(["key", "val_key"])
    status = GraphSchema.ensure_props_value_key(cur)
    assert status["index"] == "present"
    assert not any(s.startswith("DROP INDEX") or "CREATE INDEX" in s for s in executed), executed


def test_bulk_rebuild_does_not_create_the_value_key_index_on_a_table_without_the_column():
    """rebuild_indexes used to run (key, val_key) on a table without val_key, which IRIS builds over `val`."""
    from unittest.mock import MagicMock

    cur = MagicMock()
    executed = []
    cur.execute.side_effect = lambda sql, *a: executed.append(" ".join(str(sql).split()))
    cur.fetchone.return_value = None  # the catalog has no val_key column
    status = GraphSchema.rebuild_indexes(cur)
    assert not any("idx_props_key_valkey" in s and s.startswith("CREATE") for s in executed), executed
    assert status["idx_props_key_valkey"] is False


# --- The prefix the translator writes must be the prefix IRIS stored ------------------------------------------------
#
# val_key is $EXTRACT(val, 1, 128), and IRIS counts UTF-16 code units: a character outside the BMP (an emoji) is two.
# The translator sliced the Python string, which counts code points, so for a value with such a character it wrote a
# longer prefix than the stored one and `val_key = '<prefix>'` matched no row.

EMOJI = "\U0001F600"


def _lookup_sql(value):
    return _sql("MATCH (p:Person) WHERE p.sid = $id RETURN p", {"id": value}, ON)


def test_a_value_of_astral_characters_does_not_get_a_longer_prefix_than_iris_stored():
    value = EMOJI * 100  # 200 UTF-16 units; IRIS stored the first 64 characters
    sql = _lookup_sql(value)
    assert f".val_key = '{EMOJI * 64}'" in sql, sql


def test_a_prefix_that_would_end_inside_a_surrogate_pair_is_not_written():
    value = "a" + EMOJI * 100  # unit 128 is the first half of a pair: the stored key ends in a lone surrogate
    assert "val_key" not in _lookup_sql(value)


def test_the_prefix_counts_utf16_units_for_a_value_with_astral_characters_in_front():
    value = EMOJI * 10 + "z" * 200  # 20 units of emoji, then 108 of the 200 z
    assert f".val_key = '{EMOJI * 10 + 'z' * 108}'" in _lookup_sql(value)


def test_value_prefixes_that_agree_with_iris_are_still_written():
    for value, n in (("x" * 300, 128), ("é" * 300, 128), ("あ" * 129, 128), ("short", 5), (EMOJI * 64, 64)):
        assert f".val_key = '{value[:n]}'" in _lookup_sql(value), (value[:5], n)
