"""`iris_master_cleanup` must actually empty `^KG`, not merely try to.

Requires ivg-iris-enterprise:
    IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \
        pytest tests/integration/test_master_cleanup.py

The fixture deleted the SQL rows and then killed `^KG`/`^NKG` through a
short-lived dbapi connection. `iris.createIRIS` is monkeypatched at session
setup and only redirects the *session* connection to the dedicated native
connection, so that call fell through to the original, which rejects a dbapi
connection outright — and the whole block sat inside
`contextlib.suppress(Exception)`. Every integration test therefore ran against
whatever `^KG` the previous run had left behind, while the report said the
database was clean.

That is the same defect the Eraser exists to end (ADR-0004), one layer down: an
inventory maintained by hand, a failure that reports success, and drift nobody
sees. The difference is that here the drift silently decides whether other
tests pass.

The two tests below are ordered deliberately: the first seeds a global and the
SQL row that names it, the second asserts the fixture removed both. Neither is
meaningful alone, which is why the probe is a global with a name no other test
uses rather than a shared fixture.
"""

from __future__ import annotations

import os

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

PROBE = "cleanupprobe_graph"


@pytest.fixture()
def engine(iris_connection, iris_master_cleanup):
    from iris_vector_graph.engine import IRISGraphEngine

    return IRISGraphEngine(iris_connection, embedding_dimension=4)


def test_a_seed_that_the_next_test_must_not_see(engine):
    """Written through `create_edge`, so every store a real caller touches is hit."""
    engine.create_edge("probe_s", "PROBES", "probe_o", graph=PROBE)

    iris_obj = engine._iris_obj()
    assert iris_obj.get("^KG", "out", PROBE, "probe_s", "PROBES", "probe_o") is not None, (
        "the seed itself did not reach ^KG, so the next test proves nothing"
    )


def test_the_previous_test_left_nothing_in_kg_or_the_tables(engine):
    """`^KG` is emptied wholesale, so nothing needs to name the probe to reach it."""
    iris_obj = engine._iris_obj()
    assert iris_obj.get("^KG", "out", PROBE, "probe_s", "PROBES", "probe_o") is None, (
        "iris_master_cleanup reported success without killing ^KG — every "
        "integration test is running against the previous run's adjacency"
    )
    assert iris_obj.get("^KG", "deg", PROBE, "probe_s") is None

    cursor = engine.conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE COALESCE(graph_id, '') = ?", [PROBE]
    )
    assert int(cursor.fetchone()[0]) == 0
