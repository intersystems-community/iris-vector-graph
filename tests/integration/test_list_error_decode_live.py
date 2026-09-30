"""`execute_decoded` against the IRIS state that produces `<LIST ERROR>`.

A connection inserts into a table, a second connection runs `%BuildIndices` on it,
and the first connection's duplicate insert comes back as `<LIST ERROR> Incorrect list
format ... type detected : 0` instead of `SQLCODE -119`. `execute_decoded` has to hand
back the -119. The bulk loader's refused-edge test and the node-PK migration's
duplicate test failed in the full integration run this way (both pass alone): an
earlier test had rebuilt indices on `rdf_edges` / `nodes` off the shared connection.

A scratch table, so no graph table's indices are rebuilt under other tests.
From `intersystems-irispython` 5.4.0 the driver decodes the -119 itself; the test
then asserts that instead of the `<LIST ERROR>`.
"""

from __future__ import annotations

import contextlib
import os
import socket

import pytest

from iris_vector_graph.schema import _call_classmethod
from iris_vector_graph.utils import execute_decoded

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

_CONTAINER = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")
TABLE = "IVGTest.DecodeDup"
SQL = f"INSERT INTO {TABLE} (k) VALUES (?)"


def _second_connection():
    import iris as _iris

    try:
        orb_ip = socket.gethostbyname(f"{_CONTAINER}.orb.local")
        return _iris.connect(
            hostname=orb_ip, port=1972, namespace="USER", username="_SYSTEM", password="SYS"
        )
    except Exception:
        return _iris.connect(
            hostname="localhost",
            port=int(os.environ.get("IVG_PORT", "31972")),
            namespace="USER",
            username="_SYSTEM",
            password="SYS",
        )


def _driver_decodes_stale_errors() -> bool:
    from importlib.metadata import version

    try:
        major, minor = (int(p) for p in version("intersystems-irispython").split(".")[:2])
    except Exception:
        return False
    return (major, minor) >= (5, 4)


@pytest.fixture
def stale_statement(iris_connection):
    """`iris_connection` holds SQL from before another connection's rebuild."""
    conn = iris_connection
    cur = conn.cursor()
    with contextlib.suppress(Exception):
        cur.execute(f"DROP TABLE {TABLE}")
    cur.execute(f"CREATE TABLE {TABLE} (k VARCHAR(50) NOT NULL, CONSTRAINT uk UNIQUE (k))")
    cur.execute(SQL, ["x"])
    conn.commit()
    other = _second_connection()
    try:
        _call_classmethod(other, TABLE, "%BuildIndices")
    finally:
        other.close()
    try:
        yield conn, cur
    finally:
        conn.rollback()
        with contextlib.suppress(Exception):
            cur.execute(f"DROP TABLE {TABLE}")
        conn.commit()
        cur.close()


def test_the_duplicate_comes_back_as_a_duplicate(stale_statement):
    conn, cur = stale_statement
    with pytest.raises(Exception) as raw:
        cur.execute(SQL, ["x"])
    conn.rollback()
    if _driver_decodes_stale_errors():
        # intersystems-irispython 5.4.0 decodes it itself (measured: 5.3.2 raises
        # `<LIST ERROR>` on this fixture, 5.4.0 the -119). Hold it to that.
        assert "-119" in str(raw.value), raw.value
    elif "<LIST ERROR>" not in str(raw.value):
        pytest.fail(
            f"the driver decoded the stale statement's error this time ({raw.value}); "
            "the state this test is about was not reached"
        )

    with pytest.raises(Exception, match="-119"):
        execute_decoded(cur, SQL, ["x"])
    conn.rollback()


def test_a_new_row_still_goes_in(stale_statement):
    conn, cur = stale_statement
    execute_decoded(cur, SQL, ["y"])
    conn.commit()
    cur.execute(f"SELECT COUNT(*) FROM {TABLE}")
    assert cur.fetchone()[0] == 2
