"""4.6.0 R257: two fallbacks said nothing (fixes29).

* ``betweenness_centrality``'s ObjectScript tier (``Graph.KG.NKGAccel.BetweennessGlobal``) failing, as it does for a
  reader at database ``R``, or answering something other than ``OK:``, went to the Python walk over ``^KG`` (much
  slower) under ``except Exception: pass``. It now logs a WARNING naming the tier and its error first. The case
  that skips the tier by design (a named graph with edges) stays quiet; ``direction="both"`` was one until R232 made
  the tier answer it.
* ``GraphSchema.rebuild_indexes`` reported an index it could not create only in a status dict, which none of its
  three callers read: R243's lost indexes were hidden that way. It logs a WARNING naming each one and its error; the
  ``val_key`` index an install has no column for is skipped (status None), not failed. ``bulk_create_nodes``,
  ``bulk_create_edges`` and ``bulk_load_session`` name the indexes that are missing after a load, and the session's
  stats list them.

The reader case is pinned live in tests/e2e/test_460_r257_silent_fallbacks_e2e.py.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.schema import GraphSchema
from iris_vector_graph.stores.iris_sql_store import IRISGraphStore


class _NoNodes:
    def __init__(self, *a, **kw):
        pass

    def iter_nodes(self):
        return iter(())


def _store(answer):
    store = object.__new__(IRISGraphStore)
    store._named_graph_with_edges = lambda: None
    store.conn = MagicMock()
    native = MagicMock()
    if isinstance(answer, Exception):
        native.classMethodValue.side_effect = answer
    else:
        native.classMethodValue.return_value = answer
    store._iris_obj = lambda: native
    return store


def _betweenness(store, direction="out"):
    with patch("iris_vector_graph.stores.lazy_kg.LazyKG", _NoNodes):
        return store._betweenness_gref(0, direction, 0, 10, 256, None)


def test_a_failing_objectscript_tier_says_so_before_the_python_walk(caplog):
    err = RuntimeError("<PROTECT> ^IRIS.Temp.ivgBC(1)")
    with caplog.at_level(logging.WARNING, logger="iris_vector_graph.stores.iris_sql_store"):
        result = _betweenness(_store(err))
    assert result.rows == []
    warned = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("BetweennessGlobal" in m and "<PROTECT> ^IRIS.Temp.ivgBC(1)" in m and "Python" in m for m in warned), warned


def test_an_answer_that_is_not_ok_says_so(caplog):
    with caplog.at_level(logging.WARNING, logger="iris_vector_graph.stores.iris_sql_store"):
        _betweenness(_store("ERROR:^NKG not built"))
    warned = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("BetweennessGlobal" in m and "ERROR:^NKG not built" in m for m in warned), warned


@pytest.mark.parametrize("direction", ["out", "both"])
def test_a_tier_skipped_by_design_is_quiet(caplog, direction):
    store = _store("OK:[]")
    store._named_graph_with_edges = lambda: "g1"
    with caplog.at_level(logging.WARNING, logger="iris_vector_graph.stores.iris_sql_store"):
        _betweenness(store, direction=direction)
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def _cursor(fail=()):
    cur = MagicMock()

    def execute(sql, *a):
        if sql.lstrip().upper().startswith("SELECT"):  # the val_key column probe
            return None
        for name in fail:
            if f"INDEX {name} " in sql:
                raise RuntimeError(f"[SQLCODE: <-30>] cannot index {name}")
        return None

    cur.execute.side_effect = execute
    cur.fetchone.return_value = (1,)
    return cur


def test_rebuild_indexes_logs_each_index_it_could_not_create(caplog):
    with caplog.at_level(logging.WARNING, logger="iris_vector_graph.schema"):
        status = GraphSchema.rebuild_indexes(_cursor(fail=("idx_props_s",)))
    assert status["idx_props_s"] is False and status["idx_labels_s"] is True
    warned = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("idx_props_s" in m and "rdf_props" in m and "cannot index idx_props_s" in m for m in warned), warned


def test_the_val_key_index_without_its_column_is_skipped_not_failed(caplog):
    cur = _cursor()
    with patch.object(GraphSchema, "_props_has_value_key_column", return_value=False), \
            caplog.at_level(logging.WARNING, logger="iris_vector_graph.schema"):
        status = GraphSchema.rebuild_indexes(cur)
    assert status["idx_props_key_valkey"] is None
    assert GraphSchema.indexes_not_rebuilt(status) == []
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_indexes_not_rebuilt_lists_the_failed_ones():
    assert GraphSchema.indexes_not_rebuilt({"a": True, "b": False, "c": None, "d": False}) == ["b", "d"]


def _engine():
    from iris_vector_graph.engine import IRISGraphEngine

    eng = object.__new__(IRISGraphEngine)
    return eng


@pytest.mark.parametrize("call", ["bulk_create_nodes", "bulk_create_edges"])
def test_a_bulk_load_names_the_indexes_it_left_missing(caplog, call):
    from iris_vector_graph._engine import nodes_edges

    status = {"idx_props_s": False, "idx_labels_s": True}
    with patch.object(GraphSchema, "rebuild_indexes", return_value=status), \
            caplog.at_level(logging.WARNING, logger=nodes_edges.logger.name):
        nodes_edges._rebuild_after_load(MagicMock(), "Graph_KG", call)
    warned = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(call in m and "idx_props_s" in m and "idx_labels_s" not in m for m in warned), warned


def test_a_bulk_load_whose_indexes_came_back_is_quiet(caplog):
    from iris_vector_graph._engine import nodes_edges

    with patch.object(GraphSchema, "rebuild_indexes", return_value={"idx_props_s": True, "v": None}), \
            caplog.at_level(logging.WARNING, logger=nodes_edges.logger.name):
        assert nodes_edges._rebuild_after_load(MagicMock(), "Graph_KG", "bulk_create_nodes") == []
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_every_caller_reads_the_rebuild_status():
    """No call of GraphSchema.rebuild_indexes in the engine drops its answer: the one read of it is
    _rebuild_after_load's, and the three loads call that. Read from the loaded code (R128), not the file."""
    import dis
    import types

    from iris_vector_graph._engine import nodes_edges

    def codes(code):
        yield code
        for const in code.co_consts:
            if isinstance(const, types.CodeType):
                yield from codes(const)

    roots = [nodes_edges._rebuild_after_load.__code__]
    roots += [f.__code__ for f in vars(nodes_edges.NodesEdgesMixin).values() if isinstance(f, types.FunctionType)]
    readers, callers = [], []
    for root in roots:
        for code in codes(root):
            for ins in dis.get_instructions(code):
                if ins.opname in ("LOAD_ATTR", "LOAD_METHOD") and ins.argval == "rebuild_indexes":
                    readers.append(code.co_name)
                elif ins.opname == "LOAD_GLOBAL" and ins.argval == "_rebuild_after_load":
                    callers.append(code.co_name)
    # a finally body is compiled twice (its normal and its exception path): each place counts once
    assert set(readers) == {"_rebuild_after_load"}
    assert set(callers) == {"_session", "bulk_create_edges", "bulk_create_nodes"}
