"""Spec 262 T028 (unit half): setup run by a user without a DDL privilege stops at the first statement, naming it.

IRIS answers a CREATE the user may not run with SQLCODE -99 at prepare. Setup used to log each such statement as a
warning and carry on, and ran `CREATE SCHEMA` first, which needs no privilege: an empty schema, a status that read as a
setup, nothing usable. The E2E half (`test_262_alt_schema_paths.py`) checks a real user's namespace is unchanged.
"""

from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.schema_name import IVGSchemaError, missing_privilege

PRIV_ERR = ("<SQL ERROR>; Details: [SQLCODE: <-99>:<Privilege violation>]\n[Location: <Prepare>]\n"
            "[%msg: <User ivg262_nopriv does not have %CREATE_TABLE privileges>]")


def test_missing_privilege_reads_the_privilege_from_iris_text():
    assert missing_privilege(RuntimeError(PRIV_ERR)) == "%CREATE_TABLE"
    assert missing_privilege(RuntimeError("[SQLCODE: <-201>:<Table or view name not unique>]")) is None


def _engine(conn, schema="IVGALT"):
    from iris_vector_graph.engine import IRISGraphEngine

    with patch.object(IRISGraphEngine, "_build_index_registry", return_value={}), \
         patch.object(IRISGraphEngine, "_detect_stored_vector_dtype", return_value="DOUBLE"):
        return IRISGraphEngine(conn, embedding_dimension=4, schema_prefix=schema, native_cypher=False)


def test_setup_stops_at_the_first_privilege_violation_and_names_it():
    conn = MagicMock()
    cursor = conn.cursor.return_value
    sent = []

    def execute(sql, *args, **kwargs):
        sent.append(str(sql))
        if str(sql).lstrip().upper().startswith(("CREATE", "ALTER", "DROP")):
            raise RuntimeError(PRIV_ERR)
        if "$USERNAME" in str(sql):
            cursor.fetchone.return_value = ("ivg262_nopriv",)

    cursor.execute.side_effect = execute
    cursor.fetchone.return_value = None
    cursor.fetchall.return_value = []
    engine = _engine(conn)
    with pytest.raises(IVGSchemaError) as raised:
        engine._initialize_schema(auto_deploy_objectscript=False)
    message = str(raised.value)
    assert "%CREATE_TABLE" in message and "ivg262_nopriv" in message and "IVGALT" in message
    assert "Nothing was created" in message
    ddl = [s for s in sent if s.lstrip().upper().startswith(("CREATE", "ALTER", "DROP"))]
    assert len(ddl) == 1, ddl  # stopped at the first; no CREATE SCHEMA before it
    assert not ddl[0].lstrip().upper().startswith("CREATE SCHEMA")


def test_a_violation_after_objects_were_created_says_so():
    conn = MagicMock()
    cursor = conn.cursor.return_value
    creates = []

    def execute(sql, *args, **kwargs):
        if str(sql).lstrip().upper().startswith("CREATE"):
            creates.append(sql)
            if len(creates) == 2:
                raise RuntimeError(PRIV_ERR.replace("%CREATE_TABLE", "%CREATE_FUNCTION"))

    cursor.execute.side_effect = execute
    cursor.fetchone.return_value = None
    cursor.fetchall.return_value = []
    with pytest.raises(IVGSchemaError, match="%CREATE_FUNCTION") as raised:
        _engine(conn)._initialize_schema(auto_deploy_objectscript=False)
    assert "Nothing was created" not in str(raised.value)
    assert "re-run" in str(raised.value)
