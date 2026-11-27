"""4.7.2: a failure of the adjacency-index probe is said, not swallowed.

`IRISGraphEngine._adjacency_indexed` reads the class dictionary (and, for a role refused it, `Graph.KG.Adjacency.
IndexKind`). Its `except Exception` took any failure for "no index": every edge writer then wrote `^KG` itself, with no
word of why. The writers keep that path (it is the safe one), and the engine now says what failed and what it does.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

from iris_vector_graph.engine import IRISGraphEngine


def _engine(cursor):
    engine = IRISGraphEngine.__new__(IRISGraphEngine)
    engine.conn = MagicMock()
    engine.conn.cursor.return_value = cursor
    engine._schema_prefix = "Graph_KG"
    engine._iris_obj = MagicMock(side_effect=TypeError("no native handle"))
    return engine


def _failing_cursor():
    cursor = MagicMock()
    cursor.execute.side_effect = RuntimeError("the dictionary read blew up")
    return cursor


def _said(caplog):
    return [r for r in caplog.records if r.levelno >= logging.WARNING and "adjacency" in r.getMessage().lower()]


def test_a_probe_that_fails_says_why_and_what_the_writers_do(caplog):
    engine = _engine(_failing_cursor())
    with caplog.at_level(logging.WARNING):
        assert engine._adjacency_indexed is False
    said = _said(caplog)
    assert len(said) == 1, [r.getMessage() for r in caplog.records]
    text = said[0].getMessage()
    assert "the dictionary read blew up" in text
    assert "^KG" in text  # what the writers do instead


def test_the_probe_is_said_once_per_engine(caplog):
    engine = _engine(_failing_cursor())
    with caplog.at_level(logging.WARNING):
        assert engine._adjacency_indexed is False
        assert engine._adjacency_indexed is False
    assert len(_said(caplog)) == 1


def test_a_probe_that_answers_says_nothing(caplog):
    cursor = MagicMock()
    cursor.fetchall.return_value = [("Graph.KG.AdjIndex",)]
    engine = _engine(cursor)
    with caplog.at_level(logging.WARNING):
        assert engine._adjacency_indexed is True
    assert _said(caplog) == []
