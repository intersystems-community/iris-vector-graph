"""4.6.0 R271, live: ``save_snapshot`` raises when it cannot read a table, naming the table and IRIS's error.

It caught the error, logged it at debug and recorded the table with 0 rows: an archive that said ``rdf_props: 0`` for a
table holding rows, and a restore from it that erased the database and put back no properties. The user here can read
every IVG table but one (``FIXES39_R271_USER``; the role holds the database at R and SELECT on each other table), so
the read of that table is refused with SQLCODE -99. Each of the reads the sql layer makes is tried: the exported
tables, the node and edge embedding tables.

    IVG_NAMESPACE=IVG460FY4 IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \\
        pytest tests/e2e/test_460_r271_snapshot_unreadable_table_e2e.py
"""

from __future__ import annotations

import os
import re

import pytest

from tests.e2e import reading_role460 as rr

pytestmark = [pytest.mark.e2e, *rr.requires_namespace]

PREFIX = "FIXES39"
TAG = "R271"
HIDDEN = ["rdf_props", "embedding_registry", "kg_NodeEmbeddings", "kg_EdgeEmbeddings"]


def _tables(conn) -> list:
    cur = conn.cursor()
    cur.execute(
        "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = ? AND TABLE_TYPE = 'BASE TABLE'",
        [rr.SCHEMA],
    )
    return [r[0] for r in cur.fetchall()]


@pytest.fixture(scope="module")
def setup():
    from iris_vector_graph import IRISGraphEngine

    sysconn = rr.connect()
    owner = IRISGraphEngine(sysconn, embedding_dimension=rr.DIM)
    rr.seed(owner)
    owner.sync()
    role, _user = rr.names(TAG, PREFIX)
    user = rr.make_reader(TAG, rr.reading_resources("R"), sql_privileges=("EXECUTE",), prefix=PREFIX)
    tables = _tables(sysconn)
    assert set(HIDDEN) <= set(tables), tables
    try:
        yield owner, sysconn, user, role, tables
    finally:
        cur = sysconn.cursor()
        for t in tables:
            try:
                cur.execute(f"REVOKE SELECT ON {rr.SCHEMA}.{t} FROM {role}")
            except Exception as exc:  # never granted (the hidden one)
                assert "-" in str(exc), exc
        sysconn.commit()
        rr.drop_reader(TAG, PREFIX)
        owner.erase_graph(None)
        sysconn.close()


def _grant_all_but(sysconn, role, tables, hidden):
    cur = sysconn.cursor()
    for t in tables:
        verb = "REVOKE" if t == hidden else "GRANT"
        try:
            cur.execute(f"{verb} SELECT ON {rr.SCHEMA}.{t} {'FROM' if verb == 'REVOKE' else 'TO'} {role}")
        except Exception as exc:  # revoking what the role never held
            assert verb == "REVOKE" and "-" in str(exc), exc
    sysconn.commit()


@pytest.mark.parametrize("hidden", HIDDEN)
def test_a_table_the_user_cannot_read_raises(setup, tmp_path, hidden):
    from iris_vector_graph import IRISGraphEngine

    _owner, sysconn, user, role, tables = setup
    _grant_all_but(sysconn, role, tables, hidden)
    conn = rr.connect(user, rr.PASSWORD)
    try:
        cur = conn.cursor()
        with pytest.raises(Exception, match="-99"):
            cur.execute(f"SELECT COUNT(*) FROM {rr.SCHEMA}.{hidden}")
        reader = IRISGraphEngine(conn, embedding_dimension=rr.DIM)
        path = tmp_path / "snap.zip"
        with pytest.raises(RuntimeError) as err:
            reader.save_snapshot(str(path))
        msg = str(err.value)
        assert re.search(rf"Graph_KG\.{hidden}\b", msg), msg
        assert "-99" in msg, msg
        assert not path.exists(), "an archive missing a table was written"
    finally:
        conn.close()


def test_the_owner_still_writes_the_archive(setup, tmp_path):
    """The control: the same namespace, read by a user who can read everything, archives every table's rows."""
    owner, sysconn, _user, _role, _tables_ = setup
    out = owner.save_snapshot(str(tmp_path / "snap.zip"))
    cur = sysconn.cursor()
    for t in ["nodes", "rdf_edges", *HIDDEN]:
        cur.execute(f"SELECT COUNT(*) FROM {rr.SCHEMA}.{t}")
        assert out["tables"][f"Graph_KG.{t}"] == int(cur.fetchone()[0]), (t, out["tables"])
    assert out["tables"]["Graph_KG.nodes"] == 4 and out["tables"]["Graph_KG.rdf_props"] > 0, out["tables"]
    assert os.path.getsize(out["path"]) > 0
