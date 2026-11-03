"""Spec 262 T039: the grant copy, at the SQL layer (FR-010a, research R4).

A role granted EXECUTE on a legacy-style `SQLUser` function gets the same grant on its replacement; the SuperUser and
Owner Privilege rows SQLAllPrivileges also returns are never copied. Read before and after through the catalog.
Runs in IVG262ALT (IVG Schema IVGALT, or any schema: the replacement is made here).
"""

from __future__ import annotations

import pytest

from iris_vector_graph._engine.legacy import copy_grants, direct_grants
from tests.e2e import ns262

pytestmark = [pytest.mark.integration, ns262.requires_container]

NS = "IVG262ALT"
ROLE = "ivg262_grant_role"


@pytest.fixture
def world():
    conn = ns262.connect(NS)
    cur = conn.cursor()
    for sql in ("DROP FUNCTION SQLUser.R262GC", "DROP FUNCTION R262GT.R262GC", f"CREATE ROLE {ROLE}"):
        try:
            cur.execute(sql)
        except Exception:  # absent from an earlier run, or the container-wide role already exists
            pass
    cur.execute("CREATE FUNCTION SQLUser.R262GC() RETURNS INTEGER LANGUAGE OBJECTSCRIPT { Quit 1 }")
    cur.execute("CREATE FUNCTION R262GT.R262GC() RETURNS INTEGER LANGUAGE OBJECTSCRIPT { Quit 1 }")
    cur.execute(f"GRANT EXECUTE ON SQLUser.R262GC TO {ROLE}")
    yield conn, ns262.native(conn)
    for sql in ("DROP FUNCTION SQLUser.R262GC", "DROP FUNCTION R262GT.R262GC"):
        cur.execute(sql)
    conn.close()


def test_only_the_direct_grant_is_read(world):
    _, native = world
    assert [(g["subject"], g["privilege"]) for g in direct_grants(native, "SQLUser.R262GC")] == [(ROLE, "EXECUTE")]


def test_the_copy_grants_exactly_the_direct_rows_on_the_replacement(world):
    conn, native = world
    assert direct_grants(native, "R262GT.R262GC") == []
    items = [{"sql_name": "SQLUser.R262GC", "replacement": "R262GC", "grants": direct_grants(native, "SQLUser.R262GC")}]
    copied = copy_grants(conn, items, "R262GT", dry_run=False)
    assert [(c["replacement"], c["subject"], c["copied"]) for c in copied] == [("R262GT.R262GC", ROLE, True)]
    assert [(g["subject"], g["privilege"], g["with_grant"]) for g in direct_grants(native, "R262GT.R262GC")] == [
        (ROLE, "EXECUTE", False)]


def test_the_dry_run_grants_nothing(world):
    conn, native = world
    items = [{"sql_name": "SQLUser.R262GC", "replacement": "R262GC", "grants": direct_grants(native, "SQLUser.R262GC")}]
    copied = copy_grants(conn, items, "R262GT", dry_run=True)
    assert [c["copied"] for c in copied] == [False]
    assert direct_grants(native, "R262GT.R262GC") == []
