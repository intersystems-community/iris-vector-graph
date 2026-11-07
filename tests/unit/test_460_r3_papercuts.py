"""4.6.0 round-3 walkthrough papercuts (expert W7, W8, W9).

- W7: setup logged ``IVG ready`` before it granted the read path and recorded the schema; over an 83 ms link the
  call ran 40 more minutes after the line, and the record was never written. The line now comes last.
- W8: as a reader whose index and ledger probes failed, the report printed ``Vector index: absent``,
  ``Full-text index: absent`` and ``Ledger: not enabled`` beside the failures. Each is now ``unknown``.
- W9: ``ppr({'a1': 1})`` answered as ``ppr('a1')`` (the keys taken, the weights ignored) and ``ppr(5)`` raised
  ``'int' object is not iterable``. The docs say one seed or a list; anything else raises a ``TypeError`` saying so.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.schema import GraphSchema
from iris_vector_graph.status import EngineStatus, LedgerStatus
from tests.unit.test_460_r050_status_unknown import _lines
from tests.unit.test_460_status_probes import _Registry, _status
from tests.unit.test_setup_logging import _Cursor, _deploy, _Native


# --- W7 ----------------------------------------------------------------------------------------------------------

@contextmanager
def _setup(grant_read_path):
    """A fake setup up to the read-path grants, which ``grant_read_path`` (a side effect) answers."""
    with patch("iris.createIRIS", lambda c: _Native(), create=False), \
            patch.object(GraphSchema, "deploy_objectscript_classes", staticmethod(_deploy)), \
            patch.object(GraphSchema, "ensure_adjacency_index",
                         staticmethod(lambda cur: {"idx_edges_adj": "created", "idx_labels_adj": "created"})), \
            patch.object(GraphSchema, "bootstrap_kg_global",
                         staticmethod(lambda cur, conn=None, schema="Graph_KG": False)), \
            patch("iris_vector_graph._engine.legacy.grant_read_path", side_effect=grant_read_path):
        yield


def _initialize_schema():
    conn = MagicMock(spec=["cursor", "commit", "rollback", "close"])
    conn.cursor.side_effect = _Cursor
    IRISGraphEngine(conn, embedding_dimension=768).initialize_schema()


def test_the_ready_line_is_logged_after_the_grants_and_the_schema_record(caplog):
    order = []

    def grants(conn, iris_obj, schema, dry_run, pending=None):
        order.append("grants")
        return []

    def record(iris_obj, schema, version):
        order.append("record")

    class Order(logging.Handler):
        def emit(self, rec):
            if rec.getMessage().startswith("IVG ready"):
                order.append("ready")

    handler = Order()
    logging.getLogger("iris_vector_graph").addHandler(handler)
    try:
        with _setup(grants), patch("iris_vector_graph.schema_name.write_record", side_effect=record), \
                caplog.at_level(logging.INFO):
            _initialize_schema()
    finally:
        logging.getLogger("iris_vector_graph").removeHandler(handler)
    assert order == ["grants", "record", "ready"], order


def test_a_setup_that_fails_after_the_classes_logs_no_ready_line(caplog):
    with _setup(KeyboardInterrupt), caplog.at_level(logging.INFO), pytest.raises(KeyboardInterrupt):
        _initialize_schema()
    assert not [r for r in caplog.records if r.getMessage().startswith("IVG ready")]


# --- W8 ----------------------------------------------------------------------------------------------------------

def test_a_failed_hnsw_probe_makes_the_vector_index_unknown():
    status, _ = _status(hnsw={"kg_NodeEmbeddings": RuntimeError("[SQLCODE: <-99>]")})
    assert "vector_index" in status.unknown
    assert _lines(status.report())["Vector index"].startswith("unknown"), status.report()


def test_a_failed_vector_count_makes_the_vector_index_unknown():
    status, _ = _status(_Registry(fail_on={"COUNT(*) FROM Graph_KG.kg_NodeEmbeddings": RuntimeError("<-99>")}))
    assert "vector_index" in status.unknown
    assert _lines(status.report())["Vector index"].startswith("unknown")


def test_a_failed_bm25_list_makes_the_full_text_index_unknown():
    def lists(conn, cls, method, *args):
        if cls == "Graph.KG.BM25Index":
            raise RuntimeError("<PROTECT> ^BM25Idx")
        return ""

    status, _ = _status(classmethod=lists)
    assert "fulltext_index" in status.unknown
    assert _lines(status.report())["Full-text index"].startswith("unknown")


def test_a_failed_ledger_read_makes_the_ledger_unknown():
    def ledger(errors=None):
        errors.append("ledger status probe failed (meta): <PROTECT> ^IVG.Ledger")
        return LedgerStatus()  # its default: never-enabled

    status, _ = _status(ledger_error=ledger)
    assert "ledger" in status.unknown
    assert _lines(status.report())["Ledger"].startswith("unknown")
    assert "not enabled" not in status.report()


def test_good_index_and_ledger_probes_report_what_they_saw():
    status, _ = _status()
    assert status.unknown == [], status.errors
    lines = _lines(status.report())
    assert lines["Full-text index"] == "absent"


def test_a_refused_namespace_reports_the_indexes_and_the_ledger_unknown():
    report = EngineStatus(unknown=["graph", "acceleration", "sync", "vector_index", "fulltext_index", "ledger"],
                          errors=["Namespace X has never been set up"]).report()
    lines = _lines(report)
    for key in ("Vector index", "Full-text index", "Ledger"):
        assert lines[key].startswith("unknown"), report
    assert "absent" not in report and "not enabled" not in report


def test_the_refused_namespace_names_every_part_unknown(monkeypatch):
    from tests.unit.route_fakes_227 import engine_with

    engine = engine_with(_Registry(), embedding_dimension=4)
    monkeypatch.setattr(type(engine), "_status_schema",
                        lambda self: (None, "Namespace NEVERSET has never been set up for IVG", 0, None))
    status = engine.status()
    assert {"vector_index", "fulltext_index", "ledger"} <= set(status.unknown)
    assert "absent" not in status.report()


# --- W9 ----------------------------------------------------------------------------------------------------------

def _ppr_engine():
    from iris_vector_graph._engine.algorithms import AlgorithmsMixin

    engine = AlgorithmsMixin.__new__(AlgorithmsMixin)
    engine.kg_PERSONALIZED_PAGERANK = MagicMock(return_value={"a": 1.0})
    return engine


@pytest.mark.parametrize("seed", [{"a1": 1}, 5, {"a1", "a2"}, None])
def test_ppr_refuses_what_is_not_a_node_id_or_a_list_of_them(seed):
    engine = _ppr_engine()
    with pytest.raises(TypeError, match=r"ppr\(\) takes one node ID \(a string\) or a list of them"):
        engine.ppr(seed)
    engine.kg_PERSONALIZED_PAGERANK.assert_not_called()


@pytest.mark.parametrize("seed", ["a", ["a"], ("a", "b")])
def test_ppr_still_takes_a_seed_or_a_list(seed):
    assert _ppr_engine().ppr(seed) == {"scores": [{"id": "a", "score": 1.0}]}


# --- python W6 ---------------------------------------------------------------------------------------------------

def test_a_failed_read_is_in_the_result_and_not_on_stderr(caplog, capsys):
    """`RETURN 1 / 0` came back with `result.error` (USER_GUIDE §3) and printed `execute_sql error: ...` to stderr as
    well, through logging's last-resort handler, for a caller who had configured no logging (round 3, python W6)."""
    from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

    message = ("[SQLCODE: <-149>:<SQL Function encountered an error>] [%msg: <SQL Function GRAPH_KG.CY_ARITH failed "
               "with error: SQLCODE=-400,%msg=ArithmeticError: integer division by zero>]")
    conn = MagicMock()
    conn.cursor.return_value.execute.side_effect = RuntimeError(message)
    with caplog.at_level(logging.DEBUG, logger="iris_vector_graph"):
        result = IRISGraphStore(conn).execute_sql("SELECT Graph_KG.CY_ARITH(1, '/', 0)", [])
    assert result.rows == [] and "ArithmeticError: integer division by zero" in result.error
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING], [r.getMessage() for r in caplog.records]
    assert [r for r in caplog.records if "ArithmeticError" in r.getMessage()]  # still in the log, at DEBUG
    assert "execute_sql error" not in capsys.readouterr().err
