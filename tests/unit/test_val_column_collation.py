"""rdf_props.val keeps EXACT collation through upgrade_val_column.

`ALTER COLUMN val VARCHAR(64000)` with no collation resets the column to IRIS's default,
SQLUPPER, and ensure_indexes ran it on every call: on every install that ran it, `n.name =
'JOHN'` matched 'John'. The ALTER now says `COLLATE %EXACT`, and it is skipped when the
class dictionary already has MAXLEN 64000 and EXACT collation.
"""

from unittest.mock import MagicMock

from iris_vector_graph.schema import GraphSchema

EXACT_ALTER = "ALTER TABLE Graph_KG.rdf_props ALTER COLUMN val VARCHAR(64000) COLLATE %EXACT"


class _Cursor:
    """Answers the dictionary probe with `params` (None: the probe fails) and records every statement."""

    def __init__(self, params, alter_error=None):
        self.params, self.alter_error, self.sql = params, alter_error, []
        self._row = None

    def execute(self, sql, args=None):
        self.sql.append(sql)
        if "%Dictionary.PropertyDefinition" in sql:
            if self.params is None:
                raise RuntimeError("no access to %Dictionary")
            self._row = (self.params,)
        elif sql.startswith("ALTER TABLE") and self.alter_error:
            raise RuntimeError(self.alter_error)

    def fetchone(self):
        return self._row

    def alters(self):
        return [s for s in self.sql if s.startswith("ALTER TABLE")]


def test_an_exact_64000_column_is_left_alone():
    cur = _Cursor("COLLATION,EXACT,MAXLEN,64000")
    assert GraphSchema.upgrade_val_column(cur) is True
    assert cur.alters() == []


def test_a_column_without_exact_collation_gets_it_back():
    cur = _Cursor("MAXLEN,64000")
    assert GraphSchema.upgrade_val_column(cur) is True
    assert cur.alters() == [EXACT_ALTER]


def test_a_short_exact_column_is_widened_and_stays_exact():
    cur = _Cursor("COLLATION,EXACT,MAXLEN,4000")
    assert GraphSchema.upgrade_val_column(cur) is True
    assert cur.alters() == [EXACT_ALTER]


def test_an_unreadable_dictionary_still_alters_with_exact():
    cur = _Cursor(None)
    assert GraphSchema.upgrade_val_column(cur) is True
    assert cur.alters() == [EXACT_ALTER]


def test_a_non_string_dictionary_answer_is_not_trusted():
    cur = MagicMock()  # every read answers a MagicMock
    cur.fetchone.return_value = (MagicMock(),)
    assert GraphSchema.upgrade_val_column(cur) is True
    sent = [c.args[0] for c in cur.execute.call_args_list]
    assert EXACT_ALTER in sent


def test_a_failed_alter_reports_false():
    cur = _Cursor("MAXLEN,64000", alter_error="permission denied")
    assert GraphSchema.upgrade_val_column(cur) is False


def test_the_repair_is_announced_before_it_runs(caplog):
    # on an existing graph the ALTER rebuilds the iFind index on val: 254 s at 2.9M rows
    cur = _Cursor("MAXLEN,64000")
    with caplog.at_level("WARNING", logger="iris_vector_graph.schema"):
        GraphSchema.upgrade_val_column(cur)
    assert any("EXACT" in r.getMessage() and r.levelname == "WARNING" for r in caplog.records)


def test_nothing_is_announced_when_nothing_runs(caplog):
    cur = _Cursor("COLLATION,EXACT,MAXLEN,64000")
    with caplog.at_level("WARNING", logger="iris_vector_graph.schema"):
        GraphSchema.upgrade_val_column(cur)
    assert not [r for r in caplog.records if r.levelname == "WARNING"]
