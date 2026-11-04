"""4.6.0 R081: the native executor's privilege probe answers for the user it runs as (spec 253, FR-011).

Native reads go to ^KG and the tables' globals, past SQL's privilege checks, so a user without SELECT on one of the
four graph tables must stay on the SQL path. The probe was `%CHECKPRIV SELECT ON ...`, which reports a missing
privilege as SQLCODE 100 without raising, so through DB-API it answered True for everyone.

IVG262REC (IVG Schema `Graph_KG`, created and set up when absent); two users made for this file and removed after:
one with SELECT on all four tables, one lacking it on `rdf_edges`.
"""

from __future__ import annotations

import pytest

from tests.e2e import ns262

pytestmark = [pytest.mark.integration, ns262.requires_container]

NS = "IVG262REC"
ROLE_ALL, USER_ALL, PW_ALL = "ivg262_privall", "ivg262_privalluser", "Privall262x"
ROLE_PART, USER_PART, PW_PART = "ivg262_privpart", "ivg262_privpartuser", "Privpart262x"
TABLES = ("nodes", "rdf_labels", "rdf_props", "rdf_edges")


@pytest.fixture(scope="module")
def users():
    import make_scratch_namespace

    from iris_vector_graph import IRISGraphEngine

    make_scratch_namespace.make(NS)
    admin = ns262.connect(NS)
    IRISGraphEngine(admin, embedding_dimension=4, namespace=NS).initialize_schema()
    for role, user, pw, tables in ((ROLE_ALL, USER_ALL, PW_ALL, TABLES), (ROLE_PART, USER_PART, PW_PART, TABLES[:3])):
        ns262.make_role(role)
        ns262.make_user(user, pw, role, NS)
        cur = admin.cursor()
        for table in tables:
            cur.execute(f"GRANT SELECT ON Graph_KG.{table} TO {role}")
        cur.close()
    try:
        yield admin
    finally:
        admin.close()
        ns262.drop_users_and_roles(USER_ALL, ROLE_ALL, USER_PART, ROLE_PART)


def _engine_as(user, pw):
    from iris_vector_graph import IRISGraphEngine

    conn = ns262.connect_as(NS, user, pw)
    return conn, IRISGraphEngine(conn, embedding_dimension=4, namespace=NS, vector_dtype="DOUBLE")


def test_a_user_lacking_select_on_one_graph_table_is_kept_off_the_native_path(users):
    conn, engine = _engine_as(USER_PART, PW_PART)
    try:
        assert engine._native_privileges() is False
    finally:
        conn.close()


def test_a_user_holding_select_on_all_four_may_take_the_native_path(users):
    conn, engine = _engine_as(USER_ALL, PW_ALL)
    try:
        assert engine._native_privileges() is True
    finally:
        conn.close()


def test_the_superuser_may_take_the_native_path(users):
    from iris_vector_graph import IRISGraphEngine

    assert IRISGraphEngine(users, embedding_dimension=4, namespace=NS)._native_privileges() is True
