"""4.7.0 row 29, live: status() as a user who may not run IVG's routines does not report them missing.

`status().missing_routines` was the declared routines `INFORMATION_SCHEMA.ROUTINES` does not list, and that view lists
only routines the caller may run. Measured on release-4.7.0 (9995d0d7, 2026.3.0AI.113.0, IVG470ENV): a user holding
SELECT on the IVG Schema and no EXECUTE (ADMIN_GUIDE §3's database resources) saw none of them, and status() reported
all 58 declared routines missing on a complete namespace (13 when measured on 4.6.0). Each routine the view hides is
now asked of the database itself: `SELECT TOP 0 <schema>.<name>(NULL)` answers SQLCODE -359 for a routine that is not
there, -99 for one the caller may not run (it is then listed as unchecked, with why), and runs nothing.

Known answers: on a complete namespace, the SELECT-only reader's status() reports no missing routine, names `routines`
under `unknown` and says in `errors` that it could not check them; ADMIN_GUIDE's reading role (EXECUTE on the
schema) and _SYSTEM report none missing and nothing unchecked. With one routine dropped, each of the three reports that
routine missing, and only it.
"""

from __future__ import annotations

import pytest

from tests.e2e import reading_role460 as rr

pytestmark = [pytest.mark.e2e, *rr.requires_namespace]

TAG_SELECT, TAG_READ = "R29S", "R29R"
DROPPED = "CY_REVERSE"


@pytest.fixture(scope="module")
def users():
    for tag in (TAG_SELECT, TAG_READ):
        rr.drop_reader(tag)
    rr.make_reader(TAG_SELECT, rr.reading_resources(), sql_privileges=("SELECT",))
    rr.make_reader(TAG_READ, rr.reading_resources())
    yield {"select_only": rr.names(TAG_SELECT)[1], "reading_role": rr.names(TAG_READ)[1]}
    for tag in (TAG_SELECT, TAG_READ):
        rr.drop_reader(tag)


def _status(user=None):
    from iris_vector_graph.engine import IRISGraphEngine

    conn = rr.connect(user, rr.PASSWORD) if user else rr.connect()
    try:
        return IRISGraphEngine(conn, embedding_dimension=rr.DIM).status()
    finally:
        conn.close()


def test_a_complete_namespace_reports_no_missing_routine(users):
    reader = _status(users["select_only"])
    assert reader.missing_routines == []
    assert "routines" in reader.unknown
    assert any("could not check" in e and "EXECUTE" in e for e in reader.errors), reader.errors
    for st in (_status(users["reading_role"]), _status()):
        assert st.missing_routines == [] and "routines" not in st.unknown, (st.missing_routines, st.unknown)


def test_a_dropped_routine_is_missing_for_every_user(users):
    conn = rr.connect()
    try:
        conn.cursor().execute(f"DROP FUNCTION {rr.SCHEMA}.{DROPPED}")
        conn.commit()
        for user in (users["select_only"], users["reading_role"], None):
            assert _status(user).missing_routines == [DROPPED], user
    finally:
        from iris_vector_graph.engine import IRISGraphEngine

        IRISGraphEngine(conn, embedding_dimension=rr.DIM).initialize_schema()
        conn.close()
    assert _status().missing_routines == []
