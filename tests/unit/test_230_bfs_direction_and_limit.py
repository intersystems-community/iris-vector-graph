"""Two ways a variable-length Cypher pattern answered a question nobody asked.

Both were found by the same live E2E cluster, and neither reports anything.

**The direction defect.** `Graph.KG.NKGAccel.BFSJson` — the Rust accelerator behind
`_ArnoBfsAdapter` — takes `(srcId, preds, maxHops, maxResults)`. It has no direction
argument, and it reads `^NKG` outbound only. `_select_bfs_strategy` chose it whenever
Arno was loaded and the graph was the default one, so

    MATCH (x)-[r*1..1]-(y)  WHERE x.id = $id RETURN y.id   -- undirected
    MATCH (x)<-[r*1..1]-(y) WHERE x.id = $id RETURN y.id   -- inbound

both came back with the *outbound* neighbour. The rows are real edges pointing the
wrong way, so nothing looks wrong: the query answers, the IDs exist, and the edge it
names is in the database. Measured on `ivg-iris-enterprise`: the ObjectScript
`BFSFastJson` answers `both` and `in` correctly on the same rows, so the defect is
entirely in which adapter the store picks. The fix is the one spec 227 already made
for `graph` — an accelerator that cannot express the question does not get it.

**The limit defect.** When the source is bound by a *property* (`x.id = $id`) rather
than by `node_id`, `_route_var_length` hands the query to
`_execute_var_length_labeled` / `_execute_var_length_labeled_path_funcs`. Those two
never look at the translated statement's row cap, so `LIMIT 5` over a 20-neighbour hub
returned 20 rows. The ID-bound route does read it (`_extract_limit`, query.py) and
passes it into BFS, which is why only the property-bound form was wrong.
"""

from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.stores.iris_sql_store import (
    IRISGraphStore,
    _ArnoBfsAdapter,
    _ObjectScriptBfsAdapter,
)


def _store() -> IRISGraphStore:
    store = IRISGraphStore.__new__(IRISGraphStore)
    store.conn = MagicMock()
    store._namespace = "USER"
    store._namespace_checked = True
    store._arno_available = False
    store._arno_capabilities = {}
    return store


def _arno_store() -> IRISGraphStore:
    """A store where Arno is loaded and advertises BFS — the interesting case."""
    store = _store()
    store._arno_available = True
    store._arno_capabilities = {"bfs": True, "rust_callout": True}
    return store


# ---------------------------------------------------------------------------
# The direction defect
# ---------------------------------------------------------------------------


class TestOnlyAnOutboundBFSTakesTheArnoPath:
    """`BFSJson` has no direction argument, so it may only be asked outbound questions."""

    def test_an_outbound_bfs_still_takes_the_accelerator(self):
        store = _arno_store()
        with patch.object(store, "_detect_arno", return_value=True):
            assert isinstance(
                store._select_bfs_strategy(graph=None, direction="out"), _ArnoBfsAdapter
            )

    def test_the_default_direction_is_outbound(self):
        """Callers that omit the direction mean `out` — the accelerator still applies."""
        store = _arno_store()
        with patch.object(store, "_detect_arno", return_value=True):
            assert isinstance(store._select_bfs_strategy(graph=None), _ArnoBfsAdapter)

    @pytest.mark.parametrize("direction", ["in", "inbound", "both"])
    def test_an_inbound_or_undirected_bfs_does_not(self, direction):
        """Arno would answer outbound, and outbound is a different question.

        This is the defect, not a performance preference: the accelerator returns the
        source's *successors* whatever direction was asked for, so an inbound query
        got its outbound neighbours and reported success.
        """
        store = _arno_store()
        with patch.object(store, "_detect_arno", return_value=True):
            strategy = store._select_bfs_strategy(graph=None, direction=direction)
        assert isinstance(
            strategy, _ObjectScriptBfsAdapter
        ), f"direction={direction!r} was routed to Arno, which only walks outbound"

    def test_execute_bfs_threads_the_direction_to_the_selection(self):
        """The selection is only correct if it is told what was asked."""
        store = _store()
        strategy = MagicMock()
        with patch.object(store, "_select_bfs_strategy", return_value=strategy) as select:
            store.execute_bfs("a", [], 1, "both", 0, graph=None)
        kwargs = select.call_args[1]
        args = select.call_args[0]
        assert (
            kwargs.get("direction") == "both" or "both" in args
        ), f"execute_bfs chose an adapter without the direction: {select.call_args}"


# ---------------------------------------------------------------------------
# The limit defect
# ---------------------------------------------------------------------------


class TestTheRowCapIsReadOffTheTranslatedStatement:
    """One reader for the three spellings a cap arrives in."""

    @pytest.mark.parametrize(
        "sql,expected",
        [
            ("SELECT a FROM b FETCH FIRST 5 ROWS ONLY", 5),
            ("SELECT TOP 7 a FROM b", 7),
            ("SELECT DISTINCT TOP 3 a FROM b", 3),
            ("SELECT a FROM b LIMIT 9", 9),
            ("SELECT a FROM b", 0),
            ("", 0),
        ],
    )
    def test_each_spelling_is_read(self, sql, expected):
        from iris_vector_graph._engine.query import _extract_sql_row_limit

        assert _extract_sql_row_limit(sql) == expected


class _FakeStore:
    """Enough of a store for the labeled var-length route."""

    _schema_prefix = "Graph_KG"

    def __init__(self, neighbours):
        self._neighbours = neighbours
        self.conn = MagicMock()

    def execute_bfs(self, source_id, predicates, max_hops, direction, max_results, *, graph=None):
        from iris_vector_graph.result import IVGResult

        return IVGResult(
            columns=["id", "hops", "pred"],
            rows=[[nid, 1, "REL"] for nid in self._neighbours],
        )

    def get_nodes(self, node_ids, prop_keys=None):
        from iris_vector_graph.result import IVGResult

        return IVGResult(columns=["node_id", "labels"], rows=[[nid, "[]"] for nid in node_ids])


class _FakeSQLQuery:
    def __init__(self, sql):
        self.sql = sql
        self.parameters = [["src"]]
        self.column_name_map = {"y_id": "y.id"}
        self.query_metadata = None
        self.graph_context = None
        self.var_length_paths = []


class TestALabeledSourceVLPObeysTheLimit:
    """`x.id = $id` binds the source by property, which is the route that dropped it."""

    @staticmethod
    def _engine(monkeypatch, neighbours):
        from iris_vector_graph import _engine
        from iris_vector_graph.engine import IRISGraphEngine

        engine = IRISGraphEngine.__new__(IRISGraphEngine)
        engine._store = _FakeStore(neighbours)
        engine._nkg_dirty = False
        monkeypatch.setattr(_engine.query, "extract_vlp_source_ids", lambda **kw: ["src"])
        return engine

    def _vl0(self):
        return {
            "source_labels": [],
            "target_labels": [],
            "types": [],
            "min_hops": 1,
            "max_hops": 1,
            "direction": "out",
            "source_var": "x",
            "target_var": "y",
            "source_alias": "n0",
            "target_alias": "n1",
            "rel_var": None,
        }

    def test_the_limit_caps_the_rows(self, monkeypatch):
        engine = self._engine(monkeypatch, [f"leaf{i}" for i in range(20)])
        sql_query = _FakeSQLQuery("SELECT %EXACT(p3.val) AS y_id FROM nodes n0 LIMIT 5")
        result = engine._execute_var_length_labeled(sql_query, {}, self._vl0())
        capped = engine._apply_sql_row_limit(result, sql_query)
        assert len(capped.rows) == 5, f"LIMIT 5 returned {len(capped.rows)} rows"

    def test_no_limit_returns_everything(self, monkeypatch):
        engine = self._engine(monkeypatch, [f"leaf{i}" for i in range(20)])
        sql_query = _FakeSQLQuery("SELECT %EXACT(p3.val) AS y_id FROM nodes n0")
        result = engine._execute_var_length_labeled(sql_query, {}, self._vl0())
        capped = engine._apply_sql_row_limit(result, sql_query)
        assert len(capped.rows) == 20

    def test_an_order_by_is_not_truncated(self, monkeypatch, caplog):
        """Truncating an unordered answer to an ordered question would be worse.

        This route assembles its rows from BFS output, which carries no sort, so the
        first five rows are not the five the `ORDER BY` asked for. Dropping fifteen of
        them would turn "too many rows" into "the wrong rows", so the cap is skipped
        and the gap is logged instead of hidden.
        """
        import logging

        engine = self._engine(monkeypatch, [f"leaf{i}" for i in range(20)])
        sql_query = _FakeSQLQuery(
            "SELECT %EXACT(p3.val) AS y_id FROM nodes n0 ORDER BY y_id LIMIT 5"
        )
        result = engine._execute_var_length_labeled(sql_query, {}, self._vl0())
        with caplog.at_level(logging.WARNING):
            capped = engine._apply_sql_row_limit(result, sql_query)
        assert len(capped.rows) == 20
        assert "ORDER BY" in caplog.text

    def test_the_route_applies_the_cap_itself(self, monkeypatch):
        """A caller should not have to remember: `_route_var_length` does it."""
        engine = self._engine(monkeypatch, [f"leaf{i}" for i in range(20)])
        sql_query = _FakeSQLQuery("SELECT %EXACT(p3.val) AS y_id FROM nodes n0 LIMIT 5")
        sql_query.var_length_paths = [self._vl0()]
        result = engine._route_var_length(sql_query, {})
        assert (
            len(result.rows) == 5
        ), f"the property-bound var-length route ignored LIMIT 5: {len(result.rows)} rows"
