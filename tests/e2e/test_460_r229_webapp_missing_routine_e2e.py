"""4.6.0 R229: `Graph.KG.Service.CreateWebApp` makes the application when a routine its role is granted is absent,
grants what exists, and names the missing routine in the status it returns.

`GrantWebAppSql` stopped at the first GRANT IRIS refused, and `CreateWebApp` quit on its status, so a namespace
without `kg_TXT` or `kg_RRF_FUSE` (Community 2026.1 lacked `kg_RRF_FUSE` until R200) got no application, and the role
only the privileges granted before the missing one. By hand: with `kg_TXT` dropped, the call returns an error status
naming `kg_TXT`, the application exists with `MatchRoles ":IVG_KG_<NS>"`, and the role holds the other six of R131's
seven privileges. With `kg_TXT` back (`initialize_schema()` re-creates it), the call returns OK and the role holds all
seven, as before.

Run with IVG_NAMESPACE=<an IVG460*/IVG262* scratch namespace> (IVG Schema RAG, tests/e2e/ns460alt.py). The
application (`/<ns>-r229-kg`) and the role `IVG_KG_<NS>` (the name CreateWebApp gives it) are deleted at the end.
"""

from __future__ import annotations

import pytest

from tests.e2e import ns460alt as alt
from tests.e2e import test_460_kg_webapp_reset_e2e as r147

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]

APP = "/" + alt.NS.lower() + "-r229-kg"
S = alt.SCHEMA
WITHOUT_TXT = [p for p in r147.LISTED if p[1] != f"{S}.kg_TXT"]


def _drop():
    r147._drop_everything()
    r147._drop_app(APP)


@pytest.fixture(scope="module")
def ns():
    alt.make_namespace()
    conn = alt.connect()
    engine = alt.engine(conn)
    engine.initialize_schema()
    _drop()
    try:
        yield conn, engine
    finally:
        _drop()
        engine.initialize_schema()  # kg_TXT back, whatever the test left
        conn.close()


def _create_web_app(path: str) -> str:
    """r147's call, read leniently: a GRANT's error text can carry bytes that are not UTF-8 (the failure before R229)."""
    import subprocess

    script = (f'set sc=##class(Graph.KG.Service).CreateWebApp("{path}")\n'
              'write "<<CREATED:",$system.Status.IsOK(sc),"|",$system.Status.GetErrorText(sc),":CREATED>>",!\nhalt\n')
    out = subprocess.run(["docker", "exec", "-i", alt.CONTAINER, "iris", "session", "IRIS", "-U", alt.NS],
                         input=script, capture_output=True, text=True, errors="replace", timeout=180)
    return alt.marked(out.stdout + out.stderr, "CREATED")


def _routine_exists(conn, name: str) -> bool:
    return bool(alt.rows(conn, "SELECT COUNT(*) FROM INFORMATION_SCHEMA.ROUTINES WHERE ROUTINE_SCHEMA = ? AND "
                               "ROUTINE_NAME = ?", [S, name])[0][0])


def test_a_missing_routine_is_named_and_the_rest_is_granted(ns):
    conn, _engine = ns
    cur = conn.cursor()
    cur.execute(f"DROP PROCEDURE {S}.kg_TXT")
    conn.commit()
    assert not _routine_exists(conn, "kg_TXT")
    ok, _, text = _create_web_app(APP).partition("|")
    assert ok == "0", text
    assert f"{S}.kg_TXT" in text and "does not exist" in text and "created" in text, text
    app = r147._app(APP)
    assert app["exists"] and app["MatchRoles"] == ":" + r147.ROLE and app["DispatchClass"] == "Graph.KG.Service", app
    assert r147._privileges() == WITHOUT_TXT


def test_with_every_routine_the_call_is_ok_and_grants_all_seven(ns):
    conn, engine = ns
    engine.initialize_schema()
    assert _routine_exists(conn, "kg_TXT")
    assert _create_web_app(APP).startswith("1|")
    assert r147._privileges() == r147.LISTED
