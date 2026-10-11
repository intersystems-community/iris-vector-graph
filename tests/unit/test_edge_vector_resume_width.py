"""A failed edge-vector step must not poison its resume.

Mindwalk: an edge-vector step run at the wrong width left ``edge_vector_quarantine`` and
``kg_EdgeEmbeddings_ivg400`` declared at that width, and a resume reused them, so every row failed again.
"""

import pytest

from iris_vector_graph.migrations import docs_and_edge_vectors as dev


class Conn:
    def __init__(self, widths, rows):
        self.widths = widths  # {"Graph_KG.table": declared width}
        self.rows = rows  # {"Graph_KG.table": row count}
        self.executed = []

    def cursor(self):
        return Cursor(self)

    def commit(self):
        pass


class Cursor:
    def __init__(self, conn):
        self.conn = conn
        self._rows = []

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.conn.executed.append(flat)
        if flat.startswith("SELECT COUNT(*) FROM "):
            self._rows = [(self.conn.rows.get(flat.rsplit(" ", 1)[1], 0),)]

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def close(self):
        pass


@pytest.fixture
def declared(monkeypatch):
    def fake(cursor, table):
        return cursor.conn.widths.get(table)

    monkeypatch.setattr(dev.GraphSchema, "get_embedding_dimension", staticmethod(fake))


def drops(conn):
    return [s for s in conn.executed if s.startswith("DROP TABLE")]


def test_an_empty_table_at_the_wrong_width_is_dropped_so_prepare_rebuilds_it(declared):
    tables = {"Graph_KG.kg_EdgeEmbeddings_ivg400": 8, "Graph_KG.edge_vector_quarantine": 8}
    conn = Conn(tables, {})

    dev._reset_wrong_width_tables(conn, 16, "Graph_KG")

    assert sorted(drops(conn)) == [
        "DROP TABLE Graph_KG.edge_vector_quarantine",
        "DROP TABLE Graph_KG.kg_EdgeEmbeddings_ivg400",
    ]


def test_a_table_at_the_right_width_is_left_alone(declared):
    conn = Conn({"Graph_KG.kg_EdgeEmbeddings_ivg400": 16, "Graph_KG.edge_vector_quarantine": 16}, {})

    dev._reset_wrong_width_tables(conn, 16, "Graph_KG")

    assert drops(conn) == []


def test_a_table_that_is_not_there_yet_is_left_alone(declared):
    conn = Conn({}, {})

    dev._reset_wrong_width_tables(conn, 16, "Graph_KG")

    assert drops(conn) == []


def test_a_wrong_width_table_that_holds_rows_is_refused_not_dropped(declared):
    conn = Conn({"Graph_KG.edge_vector_quarantine": 8}, {"Graph_KG.edge_vector_quarantine": 5})

    with pytest.raises(RuntimeError, match="edge_vector_quarantine.*8.*16"):
        dev._reset_wrong_width_tables(conn, 16, "Graph_KG")

    assert drops(conn) == []


def test_each_upgrade_step_logs_when_it_starts_and_finishes(caplog, monkeypatch):
    """Mindwalk: the embeddings step is silent for ~20 minutes and looks like a hang."""
    import logging

    from iris_vector_graph.migrations import upgrade as up

    monkeypatch.setattr(up, "_run_step_body", lambda name, conn, **kw: up.StepResult(name))
    with caplog.at_level(logging.INFO, logger=up.logger.name):
        up._run_step("embeddings", object(), dry_run=False, schema="Graph_KG")

    messages = [r.getMessage() for r in caplog.records]
    assert any("step embeddings starting" in m for m in messages)
    assert any("step embeddings finished in" in m for m in messages)
