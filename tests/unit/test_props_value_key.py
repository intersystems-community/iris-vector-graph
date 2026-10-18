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
