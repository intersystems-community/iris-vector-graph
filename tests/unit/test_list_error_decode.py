"""A constraint error that arrives as `<LIST ERROR>` is decoded before anyone reads it.

Measured on ivg-iris-enterprise with two connections on one table: A inserts, B runs
`%BuildIndices`, and from then on A's duplicate insert fails with

    <LIST ERROR> Incorrect list format, unsupported type for IRISList; type detected : 0

instead of `SQLCODE -119`. The same happens to a `-121` foreign-key refusal. It is
per connection and per statement text, and it lasts: a fresh cursor, a rollback, the
same text with a literal in place of `?` all still get `<LIST ERROR>`, and purging the
table's cached queries makes every connection get it. B, a new connection, and a text
A has never executed all report the real SQLCODE.

So `execute_decoded` re-runs a `<LIST ERROR>` under a text the connection has not
executed (the statement plus a comment) and lets that error through. If the comment
text has itself gone stale after a later rebuild, the next one is tried.
"""

from unittest.mock import MagicMock

import pytest

from iris_vector_graph import utils
from iris_vector_graph.bulk_loader import BulkLoader
from iris_vector_graph.exceptions import BulkLoadError
from iris_vector_graph.utils import execute_decoded, is_list_error

LIST_ERROR = Exception(
    "<LIST ERROR> Incorrect list format, unsupported type for IRISList; Details: type detected : 0"
)
DUP = Exception("[SQLCODE: <-119>:<UNIQUE or PRIMARY KEY constraint failed uniqueness check>]")
FK = Exception("[SQLCODE: <-121>:<FOREIGN KEY constraint failed referential check>]")
SQL = "INSERT INTO t (k) VALUES (?)"


def _cursor(*outcomes):
    """A cursor whose successive `execute` calls raise or return `outcomes` in order."""
    cur = MagicMock()
    seen = []

    def execute(sql, params=None):
        seen.append(sql)
        out = outcomes[len(seen) - 1]
        if isinstance(out, Exception):
            raise out
        return out

    cur.execute.side_effect = execute
    return cur, seen


class TestExecuteDecoded:
    def test_a_clean_statement_runs_once(self):
        cur, seen = _cursor(None)
        execute_decoded(cur, SQL, ["x"])
        assert seen == [SQL]

    def test_another_error_is_raised_as_is(self):
        cur, seen = _cursor(DUP)
        with pytest.raises(Exception, match="-119"):
            execute_decoded(cur, SQL, ["x"])
        assert seen == [SQL]

    def test_a_list_error_is_rerun_under_a_new_text(self):
        cur, seen = _cursor(LIST_ERROR, DUP)
        with pytest.raises(Exception, match="-119"):
            execute_decoded(cur, SQL, ["x"])
        assert len(seen) == 2
        assert seen[1].startswith(SQL) and seen[1] != SQL
        assert "/*" in seen[1]

    def test_the_rerun_can_succeed(self):
        cur, seen = _cursor(LIST_ERROR, None)
        execute_decoded(cur, SQL, ["x"])
        assert len(seen) == 2

    def test_a_stale_rerun_text_moves_to_the_next(self, monkeypatch):
        monkeypatch.setattr(utils, "_decode_epoch", 0)
        cur, seen = _cursor(LIST_ERROR, LIST_ERROR, FK)
        with pytest.raises(Exception, match="-121"):
            execute_decoded(cur, SQL, ["x"])
        assert len(set(seen)) == 3
        assert utils._decode_epoch == 1

    def test_it_gives_up_with_the_original_error(self):
        cur, seen = _cursor(LIST_ERROR, LIST_ERROR, LIST_ERROR)
        with pytest.raises(Exception) as exc:
            execute_decoded(cur, SQL, ["x"])
        assert exc.value is LIST_ERROR

    def test_a_trailing_line_comment_does_not_swallow_the_marker(self):
        cur, seen = _cursor(LIST_ERROR, None)
        execute_decoded(cur, SQL + " -- note", ["x"])
        assert "\n" in seen[1]

    def test_is_list_error(self):
        assert is_list_error(LIST_ERROR)
        assert not is_list_error(DUP)


def _loader():
    conn = MagicMock()
    return BulkLoader(conn, batch_size=10), conn


class TestBulkLoaderDecodes:
    def test_a_mangled_foreign_key_refusal_raises_with_its_sqlcode(self):
        loader, _ = _loader()
        cur, _seen = _cursor(LIST_ERROR, FK)
        cur.executemany = MagicMock(side_effect=LIST_ERROR)
        with pytest.raises(BulkLoadError) as exc:
            loader._executemany_batched(cur, SQL, [["a"]], "Edges")
        assert "-121" in str(exc.value)
        assert exc.value.failed == 1

    def test_a_mangled_duplicate_is_skipped(self):
        loader, _ = _loader()
        cur, _seen = _cursor(LIST_ERROR, DUP, None)
        cur.executemany = MagicMock(side_effect=LIST_ERROR)
        assert loader._executemany_batched(cur, SQL, [["a"], ["b"]], "Nodes") == 1
        assert loader._skipped == 1


class TestNodePKMigrationDecodes:
    def test_a_mangled_duplicate_is_skipped(self):
        from scripts.migrations.migrate_to_nodepk import bulk_insert_nodes

        conn = MagicMock()
        cur, _seen = _cursor(LIST_ERROR, DUP, None)
        conn.cursor.return_value = cur
        assert bulk_insert_nodes(conn, ["a", "b"]) == 1
