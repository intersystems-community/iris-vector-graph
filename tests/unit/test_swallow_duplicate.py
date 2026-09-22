"""`_swallow_duplicate` decides which INSERT failures are duplicates.

The interesting case is the one the text cannot tell us about: a connection whose
cached INSERT predates a `%BuildIndices` on the same class reports a unique
violation as `<LIST ERROR> Incorrect list format ... type detected : 0`, with no
`-119` and no "unique" anywhere in it. The helper asks the database whether the
row is there before deciding, so a genuine `<LIST ERROR>` from something else is
still raised.
"""

from unittest.mock import MagicMock

import pytest

from iris_vector_graph._engine.nodes_edges import _swallow_duplicate

_LIST_ERROR = (
    "<LIST ERROR> Incorrect list format, unsupported type for IRISList; Details: type detected : 0"
)


def _conn(fetch_result):
    """A conn whose probe cursor returns `fetch_result` from fetchone()."""
    cur = MagicMock()
    cur.fetchone.return_value = fetch_result
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


class TestTextWeCanMatch:
    def test_sqlcode_119_is_a_duplicate(self):
        conn, cur = _conn(None)
        _swallow_duplicate(conn, Exception("SQLCODE: <-119>: UNIQUE constraint"), "SELECT 1", [])
        conn.cursor.assert_not_called()  # no probe needed

    def test_the_word_duplicate_is_a_duplicate(self):
        conn, _ = _conn(None)
        _swallow_duplicate(conn, Exception("Duplicate key value"), "SELECT 1", [])
        conn.cursor.assert_not_called()

    def test_an_unrelated_error_is_raised(self):
        conn, _ = _conn([1])
        with pytest.raises(Exception, match="Table not found"):
            _swallow_duplicate(conn, Exception("Table not found"), "SELECT 1", [])
        conn.cursor.assert_not_called()


class TestListErrorIsDecidedByTheDatabase:
    def test_row_present_means_duplicate(self):
        conn, cur = _conn([1])
        _swallow_duplicate(conn, Exception(_LIST_ERROR), "SELECT 1 FROM t WHERE id = ?", ["n1"])
        cur.execute.assert_called_once_with("SELECT 1 FROM t WHERE id = ?", ["n1"])
        cur.close.assert_called_once()

    def test_row_absent_means_the_error_was_something_else(self):
        conn, cur = _conn(None)
        with pytest.raises(Exception, match="LIST ERROR"):
            _swallow_duplicate(conn, Exception(_LIST_ERROR), "SELECT 1", [])
        cur.close.assert_called_once()

    def test_a_probe_that_itself_fails_reraises_the_original(self):
        conn, cur = _conn(None)
        cur.execute.side_effect = RuntimeError("connection gone")
        with pytest.raises(Exception, match="LIST ERROR"):
            _swallow_duplicate(conn, Exception(_LIST_ERROR), "SELECT 1", [])
        cur.close.assert_called_once()
