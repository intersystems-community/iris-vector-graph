"""4.6.0 R201: an engine on a namespace IVG was never set up in says so, not "set up by an earlier IVG release".

Walkthrough 1 W4 made `IVGBARE` on Community 2026.1 and built an engine on it. This makes a namespace with a database
of its own and nothing else (`scripts/dev/make_scratch_namespace.py`, dropped afterwards), on the container
`IVG_TEST_CONTAINER` at `IVG_PORT`. The not-upgraded message keeps its own cases: tests/e2e/test_262_not_upgraded.py.

    pytest tests/e2e/test_460_r201_never_set_up_e2e.py
"""

from __future__ import annotations

import os
import sys

import pytest

from tests.e2e import ns262

pytestmark = [pytest.mark.e2e, ns262.requires_container]

#: Dropped and made again by this file; IVG460FX* only (make_scratch_namespace.DROPPABLE).
NS = os.environ.get("IVG460_BARE_NS", "IVG460FX24B")


@pytest.fixture(scope="module")
def bare():
    sys.path.insert(0, str(ns262.REPO / "scripts" / "dev"))
    import make_scratch_namespace

    assert make_scratch_namespace.may_drop(NS), NS
    make_scratch_namespace.recreate(NS)
    conn = ns262.connect(NS)
    try:
        yield conn
    finally:
        conn.close()
        make_scratch_namespace.drop(NS)


def test_an_engine_on_it_says_it_was_never_set_up(bare):
    from iris_vector_graph import IRISGraphEngine, IVGSchemaError

    engine = IRISGraphEngine(bare, embedding_dimension=8)
    with pytest.raises(IVGSchemaError) as raised:
        engine.get_node_count()
    text = str(raised.value)
    assert f"Namespace {NS} has never been set up for IVG" in text, text
    assert "earlier IVG release" not in text
    assert "Run initialize_schema() on it, which writes" in text, text
    assert "initialize_schema(dry_run=True), which writes nothing" in text, text


def test_status_reports_it_and_no_schema(bare):
    from iris_vector_graph import IRISGraphEngine

    status = IRISGraphEngine(bare, embedding_dimension=8).status()
    assert status.schema is None
    assert any("has never been set up for IVG" in e for e in status.errors), status.errors
    assert not any("earlier IVG release" in e for e in status.errors), status.errors


def test_nothing_was_written(bare):
    from iris_vector_graph import IRISGraphEngine, IVGSchemaError

    nat = ns262.native(bare)
    before = [int(nat.isDefined(g) or 0) for g in ("^KG", "^IVG.Deploy", "^NKG")]
    with pytest.raises(IVGSchemaError):
        IRISGraphEngine(bare, embedding_dimension=8).execute_cypher("MATCH (n) RETURN count(n)")
    assert [int(nat.isDefined(g) or 0) for g in ("^KG", "^IVG.Deploy", "^NKG")] == before == [0, 0, 0]
    assert ns262.classes(bare, "Graph.KG") == set()
