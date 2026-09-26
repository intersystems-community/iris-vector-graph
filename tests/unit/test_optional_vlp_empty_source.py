"""Match7 [19]: `OPTIONAL MATCH p = (a)-->(b)-[*]->(c) RETURN p` with no `b` reachable.

`_execute_var_length_labeled`'s "no target" branch already answers an OPTIONAL
match's single null row under the RETURN columns (`_optional_null_columns`,
spec 203). But when the *source* of the variable-length hop (`b`) can't be
found at all — `extract_vlp_source_ids` returns `[]` — the earlier "no source"
branch short-circuits with `columns=out_cols, rows=[]` before that logic ever
runs, ignoring `is_optional` entirely. `RETURN p` came back as an empty result
named `c` instead of the one required null row named `p`.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from iris_vector_graph.result import IVGResult


class _FakeStore:
    """No label matches anything — `extract_vlp_source_ids`' label path answers []."""

    _schema_prefix = "Graph_KG"

    def __init__(self):
        self.conn = MagicMock()

    def query_nodes(self, label_filter=None, **_kwargs):
        return IVGResult(columns=["node_id"], rows=[])


class _FakeSQLQuery:
    def __init__(self, select_aliases, column_name_map=None):
        self.sql = "SELECT 1"
        self.parameters = [[]]
        self.column_name_map = column_name_map or {}
        self.query_metadata = None
        self.graph_context = None
        self.select_aliases = select_aliases


_VL0 = {
    "source_var": "b",
    "source_alias": "n2",
    "target_var": "c",
    "target_alias": "n4",
    "rel_var": None,
    "types": [],
    "direction": "out",
    "min_hops": 1,
    "max_hops": 100,
    "shortest": False,
    "all_shortest": False,
    "src_id_param": None,
    "dst_id_param": None,
    "return_path_funcs": [],
    "properties": {},
    "source_labels": ["NoSuchLabel"],
    "target_labels": [],
    "optional": True,
}


def _engine(store):
    from iris_vector_graph.engine import IRISGraphEngine

    engine = IRISGraphEngine.__new__(IRISGraphEngine)
    engine._store = store
    engine._nkg_dirty = False
    return engine


class TestOptionalNoSourceAnswersOneNullRow:
    def test_no_source_ids_with_optional_returns_null_row_named_by_select(self):
        engine = _engine(_FakeStore())
        sql_query = _FakeSQLQuery(select_aliases=["p"])
        result = engine._execute_var_length_labeled(sql_query, {}, dict(_VL0))
        assert result.columns == ["p"], result.columns
        assert result.rows == [[None]], result.rows

    def test_no_source_ids_without_optional_stays_empty(self):
        engine = _engine(_FakeStore())
        sql_query = _FakeSQLQuery(select_aliases=["p"])
        vl0 = dict(_VL0)
        vl0["optional"] = False
        result = engine._execute_var_length_labeled(sql_query, {}, vl0)
        assert result.columns == ["c"], result.columns
        assert result.rows == [], result.rows

    def test_no_source_ids_optional_falls_back_without_select_aliases(self):
        engine = _engine(_FakeStore())
        sql_query = _FakeSQLQuery(select_aliases=[])
        result = engine._execute_var_length_labeled(sql_query, {}, dict(_VL0))
        assert result.columns == ["c"], result.columns
        assert result.rows == [[None]], result.rows
