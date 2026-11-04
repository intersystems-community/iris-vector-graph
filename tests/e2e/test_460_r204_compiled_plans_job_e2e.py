"""4.6.0 R204: in the default `job` mode a repeated hop shape the native executor runs is compiled (`IVGC.P<n>`) by
its second run, with the same answer; a hop read the native executor does not run is never compiled, in any mode.

Walkthrough 1 W7 ran ~20 hop reads over 5 shapes in `job` mode and found no `IVGC.*` class. Their reads went to the SQL
path: an anchor `{node_id: ...}` on a node pattern with no label, and `RETURN x.node_id`, are not native plans
(`cypher/native_plan.py`), so nothing was ever asked to compile; `Entries()` listed only the one planned shape they
ran under `sync`. Job mode itself compiles (measured on Community 2026.1 and 2026.3). ADMIN_GUIDE §4 now says which
reads are compiled, with these examples, and this file runs them. The answers, for a -> b -> c -> d (KNOWS, labelled
P, named A..D), were worked out by hand. Since R246 the native executor runs the unlabelled anchor, so it is compiled
(tests/e2e/test_460_r246_native_node_id_e2e.py).

    IVG_NAMESPACE=IVG460FXO pytest tests/e2e/test_460_r204_compiled_plans_job_e2e.py
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tests.e2e import reading_role460 as rr

pytestmark = [pytest.mark.e2e, *rr.requires_namespace]

GUIDE = Path(__file__).resolve().parents[2] / "docs" / "ADMIN_GUIDE.md"

#: (query, its rows): reads the native executor runs, so their shape is compiled
COMPILED = [
    ("MATCH (a:P {name: 'A'})-[:KNOWS]->(b)-[:KNOWS]->(c) RETURN c.name", [["C"]]),
    ("MATCH (a:P {node_id: 'a'})-[:KNOWS]->(b) RETURN b.name", [["B"]]),
    ("MATCH (a:P)-[:KNOWS]->(b)-[:KNOWS]->(c) RETURN count(*)", [[2]]),
    # an anchor with no label: native since 4.6.0 R246
    ("MATCH (a {node_id: 'a'})-[:KNOWS]->(b) RETURN b.name", [["B"]]),
]
#: reads on the SQL path: compiled in no mode
NOT_COMPILED = [
    ("MATCH (a:P {name: 'A'})-[:KNOWS]->(b) RETURN b.node_id", [["b"]]),
]
#: how long a background build may take before the test calls it stuck (it took under 2 s when measured)
BUILD_WAIT_S = 30


@pytest.fixture(scope="module")
def engine():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = rr.connect()
    eng = IRISGraphEngine(conn, embedding_dimension=rr.DIM)
    rr.seed(eng)
    nat = iris.createIRIS(conn)
    mode = nat.get("^IVG.CypherGen", "mode")
    nat.kill("^IVG.CypherGen", "mode")
    try:
        yield eng, nat, conn
    finally:
        nat.classMethodValue("Graph.KG.Cypher.Gen", "DropAll")
        if mode is not None:
            nat.set(mode, "^IVG.CypherGen", "mode")
        eng.erase_graph(None)
        conn.close()


def _entries(nat) -> list:
    return json.loads(str(nat.classMethodValue("Graph.KG.Cypher.Gen", "Entries")) or "[]")


def _compiled(conn, name: str) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM %Dictionary.CompiledClass WHERE Name = ?", [name])
    return int(cur.fetchone()[0]) == 1


def test_the_default_mode_is_job(engine):
    _, nat, _ = engine
    assert str(nat.classMethodValue("Graph.KG.Cypher.Gen", "Effective")) == "job"


@pytest.mark.parametrize("query, rows", COMPILED)
def test_a_repeated_native_shape_is_compiled_by_its_second_run(engine, query, rows):
    eng, nat, conn = engine
    nat.classMethodValue("Graph.KG.Cypher.Gen", "DropAll")
    assert eng.execute_cypher(query).rows == rows  # interpreted; the job builds the class
    deadline = time.monotonic() + BUILD_WAIT_S
    while True:
        entries = _entries(nat)
        if entries and entries[0]["status"] != "pending":
            break
        assert time.monotonic() < deadline, f"the build was still pending after {BUILD_WAIT_S} s: {entries}"
        time.sleep(0.5)
    assert len(entries) == 1 and entries[0]["status"] == "ok", entries
    assert _compiled(conn, entries[0]["class"]), entries
    assert eng.execute_cypher(query).rows == rows  # the class's run


@pytest.mark.parametrize("query, rows", NOT_COMPILED)
def test_a_read_on_the_sql_path_is_not_compiled(engine, query, rows):
    eng, nat, conn = engine
    nat.classMethodValue("Graph.KG.Cypher.Gen", "DropAll")
    for mode in ("job", "sync"):
        nat.set(mode, "^IVG.CypherGen", "mode")
        for _ in range(2):
            assert eng.execute_cypher(query).rows == rows
    nat.kill("^IVG.CypherGen", "mode")
    time.sleep(1)
    assert _entries(nat) == []


def test_the_guide_names_these_examples():
    """ADMIN_GUIDE §4 says which reads are compiled; its examples are the ones run here."""
    text = GUIDE.read_text()
    for query, _ in COMPILED + NOT_COMPILED:
        assert query in text, query
