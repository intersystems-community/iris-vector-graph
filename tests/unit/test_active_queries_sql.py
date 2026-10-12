"""`/admin/queries` and `list_active_queries` must issue SQL IRIS will run.

Both read `SELECT ID, State, ClientName, Command FROM %SYS.ProcessQuery ...
FETCH FIRST n ROWS ONLY`. `%SYS.ProcessQuery` has neither `ClientName` nor
`Command`, so the statement fails at Prepare; and with `FETCH FIRST` that Prepare
error does not come back as an exception, the driver SIGSEGVs (measured on
`irishealth:2026.3.0AI.113.0`). The skip that hid it blamed Community IRIS.

Active SQL lives in `INFORMATION_SCHEMA.CURRENT_STATEMENTS`, with the text in
`INFORMATION_SCHEMA.STATEMENTS` by hash; the row limit is `TOP`.
"""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from iris_vector_graph.engine import IRISGraphEngine


def _engine(rows=()):
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.return_value = list(rows)
    conn.cursor.return_value = cursor
    eng = IRISGraphEngine(conn, embedding_dimension=4)
    cursor.execute.reset_mock()  # the constructor's own probes
    iris_obj = MagicMock()
    iris_obj.classMethodValue.return_value = "2"
    return eng, cursor, iris_obj


def _sql(cursor):
    return " ".join(cursor.execute.call_args[0][0].split()).upper()


def _check(sql):
    assert "FETCH FIRST" not in sql
    assert "CLIENTNAME" not in sql and "COMMAND" not in sql
    assert "%SYS.PROCESSQUERY" not in sql
    assert "INFORMATION_SCHEMA.CURRENT_STATEMENTS" in sql
    assert sql.startswith("SELECT TOP ")


def test_engine_sql_is_runnable():
    eng, cur, iris_obj = _engine()
    with patch.object(eng, "_iris_obj", return_value=iris_obj):
        eng.list_active_queries(limit=7)
    sql = _sql(cur)
    _check(sql)
    assert sql.startswith("SELECT TOP 7 ")


def test_engine_rows_keep_their_keys():
    eng, _, iris_obj = _engine([(158112, "Executing", "_SYSTEM", "SELECT 1")])
    with patch.object(eng, "_iris_obj", return_value=iris_obj):
        (row,) = eng.list_active_queries()
    assert row == {
        "id": "158112", "state": "Executing", "client": "_SYSTEM", "command": "SELECT 1",
    }


def test_engine_limit_must_be_an_integer():
    eng, cur, iris_obj = _engine()
    with patch.object(eng, "_iris_obj", return_value=iris_obj):
        assert eng.list_active_queries(limit="5; DROP TABLE x") == []
    cur.execute.assert_not_called()


def test_route_sql_is_runnable():
    from iris_vector_graph.cypher_api import _reset_engine, app

    eng = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    eng.conn.cursor.return_value = cursor
    _reset_engine()
    try:
        with patch("iris_vector_graph.cypher_api._get_engine", return_value=eng):
            resp = TestClient(app, raise_server_exceptions=False).get("/admin/queries")
    finally:
        _reset_engine()
    assert resp.status_code == 200
    _check(_sql(cursor))
