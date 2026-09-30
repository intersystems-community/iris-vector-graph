"""4.1.1 — the FHIR E2E session starts from an empty repository.

IVGFHIR is a scratch namespace, but nothing emptied it: each run PUT and deleted
under a fresh prefix, and a FHIR delete is soft, so by 2026-09-30 the repository
held 282,582 deleted rows beside 23,638 live ones and every Rebuild paid for them.
`reset_repository` empties it through HS.FHIRServer.Installer.Reset and erases each
registered FHIR graph, whose watermarks would otherwise point past the new rows.
"""

from __future__ import annotations

from unittest import mock

from tests.e2e import fhir_conftest


class _Cursor:
    def __init__(self, graphs):
        self.graphs = graphs
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append(sql)

    def fetchall(self):
        return [(g,) for g in self.graphs]

    def close(self):
        pass


def _run(graphs):
    conn = mock.Mock()
    cur = _Cursor(graphs)
    conn.cursor.return_value = cur
    irisobj = mock.Mock()
    engine = mock.Mock()
    with mock.patch("iris.createIRIS", return_value=irisobj, create=False):
        fhir_conftest.reset_repository(conn, engine)
    return irisobj, engine, cur


def test_resets_the_endpoint_through_the_installer():
    irisobj, _, _ = _run([])
    irisobj.classMethodVoid.assert_called_once_with(
        "HS.FHIRServer.Installer", "Reset", "", fhir_conftest.ENDPOINT
    )


def test_erases_every_registered_fhir_graph():
    _, engine, cur = _run([fhir_conftest.GRAPH, "fhir:IVGFHIR:other"])
    assert "Graph_KG.fhir_graphs" in cur.sql[0]
    assert engine.erase_graph.call_args_list == [
        mock.call(fhir_conftest.GRAPH),
        mock.call("fhir:IVGFHIR:other"),
    ]


def test_session_fixture_resets_after_deploy():
    import inspect

    src = inspect.getsource(fhir_conftest._fhir_session.__wrapped__)
    assert src.index("deploy(conn)") < src.index("reset_repository(")
    assert src.index("initialize_schema(") < src.index("reset_repository(")
