"""4.6.0 R202: what ADMIN_GUIDE §3's reading role saw as warnings and status errors, offline.

The role cannot SELECT from the class dictionary and cannot write the database. `status()` counted the legacy objects
with a dictionary SELECT ("schema record probe failed" for every reader); each cached algorithm logged a WARNING per
call that its cache write was refused, though the doc says such reads answer without caching. Live:
tests/e2e/test_460_r202_reading_role_e2e.py, which builds the role from the guide's table.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from iris_vector_graph._engine import legacy
from iris_vector_graph.stores import iris_sql_store as store_mod

REFUSED = "[SQLCODE: <-99>:<Privilege violation>] [%msg: <User R is not privileged for the operation>]"


class _RefusingConn:
    def cursor(self):
        cursor = MagicMock()
        cursor.execute.side_effect = RuntimeError(REFUSED)
        return cursor


class _Native:
    def __init__(self, defined):
        self.defined = set(defined)

    def classMethodValue(self, cls, method, *args):
        assert (cls, method) == ("%Dictionary.ClassDefinition", "IDKEYExists"), (cls, method)
        return 1 if args[0] in self.defined else 0


def test_the_legacy_classes_are_asked_over_the_native_api_when_the_select_is_refused():
    found = legacy._defined_classes(_RefusingConn(), _Native({"User.funcCYCMP"}), ["User.funcCYCMP", "User.nodes"])
    assert found == {"User.funcCYCMP"}


def test_another_failure_of_the_legacy_select_still_raises():
    class _Dead:
        def cursor(self):
            cursor = MagicMock()
            cursor.execute.side_effect = RuntimeError("Communication link failure")
            return cursor

    with pytest.raises(RuntimeError, match="Communication link"):
        legacy._defined_classes(_Dead(), _Native(()), ["User.nodes"])


def test_a_refused_cache_write_is_said_once_at_info(monkeypatch, caplog):
    monkeypatch.setattr(store_mod, "_CACHE_READ_ONLY_LOGGED", [False])
    refused = RuntimeError('<PROTECT>Put+2^Graph.KG.AlgoCache.1 ^IVG.AlgoCache(0,"pagerank","x"),/db/')
    with caplog.at_level(logging.INFO, logger="iris_vector_graph"):
        for _ in range(3):
            store_mod._log_cache_store_failure(refused)
    said = [(r.levelno, r.getMessage()) for r in caplog.records if "cached" in r.getMessage()]
    assert len(said) == 1 and said[0][0] == logging.INFO and "^IVG.AlgoCache" in said[0][1]


def test_another_cache_failure_is_still_a_warning(monkeypatch, caplog):
    monkeypatch.setattr(store_mod, "_CACHE_READ_ONLY_LOGGED", [False])
    with caplog.at_level(logging.INFO, logger="iris_vector_graph"):
        store_mod._log_cache_store_failure(RuntimeError("<STORE> out of memory"))
    assert [r.levelno for r in caplog.records] == [logging.WARNING]
