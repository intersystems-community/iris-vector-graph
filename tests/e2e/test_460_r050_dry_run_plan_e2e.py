"""4.6.0 R050 (expert walkthrough W4, W5): a dry run says what setup will create or build, writes nothing, and the run
then does exactly that and replies it under the same keys.

The walkthrough's dry run on a namespace never set up listed nothing it would create; on one set up empty and then
loaded it did not mention the ^KG/^NKG build the next run made, and that run replied `tables_created: True` having
created none. Here, on a namespace with a new database of its own (`IVG_DRY_RUN_NS`, dropped and made anew without IVG):

1. the dry run lists the inventory's 18 tables, 51 routines (ADMIN_GUIDE, "Running setup again"), every class the
   server can compile, both adjacency indexes, and no ^KG build; the namespace is the same before and after it
   (catalog, classes and when each changed, every global name in its database, IVG's globals' contents, row counts);
2. the run creates what the dry run listed, and replies it;
3. after a write, the dry run says the next run builds ^KG and ^NKG, writes nothing, and the run does (^NKG appears);
4. a re-run creates nothing and builds nothing, and says so.

    IVG_DRY_RUN_NS=IVG460FXDRY pytest tests/e2e/test_460_r050_dry_run_plan_e2e.py
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.e2e import ns262

pytestmark = [pytest.mark.e2e, ns262.requires_container]

REPO = Path(__file__).resolve().parents[2]
NS = os.environ.get("IVG_DRY_RUN_NS", "IVG460FXDRY")
INVENTORY = json.loads((REPO / "docs" / "inventory.json").read_text())
SOURCES = REPO / "iris_src" / "src"


def inventory_tables() -> list:
    return sorted(n for e in INVENTORY["entries"] if e["kind"] == "table" and e["present"] == "setup"
                  for n in e["names"])


def shipped_classes() -> set:
    return {".".join(p.relative_to(SOURCES).with_suffix("").parts) for p in SOURCES.rglob("*.cls")}


def database_globals() -> list:
    """Every global name in the namespace's own database, IRIS's included."""
    script = ('set rs=##class(%ResultSet).%New("%SYS.GlobalQuery:DirectoryList") '
              f'do rs.Execute("/usr/irissys/mgr/{NS.lower()}/","","*",1) '
              'for  quit:\'rs.Next()  write "G:",rs.Get("Name"),!\nwrite "END",!\nhalt\n')
    out = subprocess.run(["docker", "exec", "-i", ns262.CONTAINER, "iris", "session", "IRIS", "-U", "%SYS"],
                         input=script, capture_output=True, text=True, timeout=120, check=True).stdout
    assert "END" in out, out[-400:]
    return sorted(line[2:].strip() for line in out.splitlines() if line.startswith("G:"))


def snapshot(conn) -> dict:
    snap = ns262.deep_snapshot(conn)
    snap["class_changed"] = dict(ns262.rows(conn, "SELECT Name, TimeChanged FROM %Dictionary.ClassDefinition "
                                                  "WHERE Name %STARTSWITH 'Graph' OR Name %STARTSWITH 'IVG'"))
    snap["meta"] = ns262.global_digest(ns262.native(conn), "^Graph.KG.Meta")
    snap["database_globals"] = database_globals()
    return snap


@pytest.fixture(scope="module")
def ns():
    import make_scratch_namespace

    make_scratch_namespace.recreate(NS)
    conn = ns262.connect(NS)
    yield conn
    conn.close()


@pytest.fixture(scope="module")
def first(ns):
    """The dry run on the namespace never set up, the snapshots around it, then the run."""
    from iris_vector_graph import IRISGraphEngine

    before = snapshot(ns)
    plan = IRISGraphEngine(ns, embedding_dimension=768).initialize_schema(dry_run=True)
    after = snapshot(ns)
    status = IRISGraphEngine(ns, embedding_dimension=768).initialize_schema()
    return {"plan": plan, "before": before, "after": after, "status": status}


def test_the_first_dry_run_writes_nothing(first):
    assert first["after"] == first["before"]


def test_the_first_dry_run_lists_the_inventorys_tables_and_the_51_routines(first):
    plan = first["plan"]
    assert plan["dry_run"] is True and plan["schema_recorded"] == "new"
    assert plan["tables_created"] == inventory_tables() and len(plan["tables_created"]) == 18
    assert len(plan["routines"]["created"]) == 51 and plan["routines"]["unchanged"] == 0


def test_the_first_dry_run_lists_every_class_the_server_compiles(first):
    plan = first["plan"]
    assert set(plan["objectscript_compiled"]) | set(plan["objectscript_skipped"]) == shipped_classes()
    assert not set(plan["objectscript_compiled"]) & set(plan["objectscript_skipped"])


def test_the_first_dry_run_declares_both_adjacency_indexes_and_builds_nothing(first):
    assert first["plan"]["adjacency_index"] == {"idx_edges_adj": "created", "idx_labels_adj": "created"}
    assert first["plan"]["kg_built"] is False


def test_the_run_does_what_the_dry_run_said(first, ns):
    plan, status = first["plan"], first["status"]
    for key in ("tables_created", "objectscript_compiled", "objectscript_skipped", "kg_built"):
        assert status[key] == plan[key], key
    # the run also counts the temporal edges it gave rows before declaring the edge index; the dry run does not
    indexes = ("idx_edges_adj", "idx_labels_adj")
    assert {k: status["adjacency_index"][k] for k in indexes} == plan["adjacency_index"]
    assert status["routines"]["created"] == plan["routines"]["created"]
    assert sorted(t for s, t in ns262.namespace_snapshot(ns)["tables"] if s == "Graph_KG") == inventory_tables()


@pytest.fixture(scope="module")
def loaded(first, ns):
    """A write after the first setup, then the dry run around snapshots, the run, and a re-run."""
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(ns, embedding_dimension=768)
    assert engine.create_node("a", labels=["P"]) is True and engine.create_node("b", labels=["P"]) is True
    assert engine.create_edge("a", "KNOWS", "b") is True
    nat = ns262.native(ns)
    nkg_before = bool(nat.isDefined("^NKG"))
    before = snapshot(ns)
    plan = IRISGraphEngine(ns, embedding_dimension=768).initialize_schema(dry_run=True)
    after = snapshot(ns)
    status = IRISGraphEngine(ns, embedding_dimension=768).initialize_schema()
    nkg_after = bool(nat.isDefined("^NKG"))
    again_plan = IRISGraphEngine(ns, embedding_dimension=768).initialize_schema(dry_run=True)
    again = IRISGraphEngine(ns, embedding_dimension=768).initialize_schema()
    return {"plan": plan, "before": before, "after": after, "status": status, "nkg": (nkg_before, nkg_after),
            "again_plan": again_plan, "again": again}


def test_with_rows_waiting_the_dry_run_says_kg_will_be_built_and_writes_nothing(loaded):
    assert loaded["plan"]["kg_built"] is True
    assert loaded["after"] == loaded["before"]
    assert loaded["nkg"][0] is False


def test_the_run_builds_kg_and_nkg_and_says_so(loaded):
    assert loaded["status"]["kg_built"] is True
    assert loaded["nkg"][1] is True


def test_with_nothing_to_do_the_plan_and_the_run_say_nothing_was_made(loaded):
    for status in (loaded["plan"], loaded["status"], loaded["again_plan"], loaded["again"]):
        assert status["tables_created"] == []
        assert status["routines"]["created"] == []
        assert status["objectscript_compiled"] == []
        assert status["adjacency_index"] == {"idx_edges_adj": "present", "idx_labels_adj": "present"}
    assert loaded["again_plan"]["kg_built"] is False and loaded["again"]["kg_built"] is False
