"""Spec 262 T062: `docs/inventory.json` is generated from ADMIN_GUIDE's inventory tables and agrees with them, and both
agree with what the code creates, as far as that can be read without a server.

The E2E half (`tests/e2e/test_262_inventory.py`) compares the JSON with a freshly initialized namespace.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GUIDE = REPO / "docs" / "ADMIN_GUIDE.md"
INVENTORY = REPO / "docs" / "inventory.json"
GENERATOR = REPO / "scripts" / "dev" / "gen_inventory_json.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gen():
    return _load(GENERATOR, "gen_inventory_json")


@pytest.fixture(scope="module")
def inv():
    return json.loads(INVENTORY.read_text())


def _names(inv: dict, kinds: tuple, present: str = None) -> set:
    return {n for e in inv["entries"] if e["kind"] in kinds and (present is None or e["present"] == present)
            for n in e["names"]}


def test_the_json_is_what_the_generator_makes_from_admin_guide(gen, inv):
    assert gen.build(GUIDE.read_text()) == inv, "run scripts/dev/gen_inventory_json.py"


def test_the_generator_reads_names_kinds_and_presence_from_a_table(gen):
    text = "\n".join([
        "<!-- inventory -->",
        "",
        "| Kind | Names | Present | Purpose | Optional, and how it is activated | Back up |",
        "| --- | --- | --- | --- | --- | --- |",
        "| table | `nodes`, `rdf_edges` | after setup | the graph | no | yes |",
        "| global | `^IVG.AlgoCache` | on first use | a cache | yes: `IVG_DISABLE_ALGO_CACHE=1` turns it off | no |",
        "",
        "<!-- inventory: skipped -->",
        "",
        "| Class | Skipped when the server has no | Reason setup gives |",
        "| --- | --- | --- |",
        "| `Graph.KG.PyOps` | embedded Python | `needs embedded Python` |",
        "",
    ])
    got = gen.build(text)
    assert [(e["kind"], e["names"], e["present"]) for e in got["entries"]] == [
        ("table", ["nodes", "rdf_edges"], "setup"),
        ("global", ["^IVG.AlgoCache"], "use"),
    ]
    assert got["entries"][1]["backup"] == "no"
    assert got["skipped_classes"] == [
        {"class": "Graph.KG.PyOps", "missing": "embedded Python", "reasons": ["needs embedded Python"]}
    ]


def test_the_generator_refuses_a_row_it_cannot_read(gen):
    bad = "\n".join([
        "<!-- inventory -->",
        "",
        "| Kind | Names | Present | Purpose | Optional, and how it is activated | Back up |",
        "| --- | --- | --- | --- | --- | --- |",
        "| tabel | `nodes` | after setup | x | no | yes |",
    ])
    with pytest.raises(ValueError, match="tabel"):
        gen.build(bad)


def test_every_entry_says_what_it_is_for_whether_it_is_optional_and_whether_to_back_it_up(inv):
    for e in inv["entries"]:
        assert e["kind"] in {"package", "table", "view", "function", "procedure", "global", "storage"}, e
        assert e["present"] in {"setup", "use", "activation"}, e
        assert e["names"] and e["purpose"] and e["optional"] and e["backup"], e


def test_no_name_is_listed_twice(inv):
    seen = {}
    for e in inv["entries"]:
        for n in e["names"]:
            key = ("routine" if e["kind"] in ("function", "procedure") else e["kind"], n)
            assert key not in seen, f"{key} listed twice"
            seen[key] = e


def test_the_globals_operators_asked_about_are_listed(inv):
    """NR14 and FR-017: the adjacency, the deploy record, the caches and the Arno configuration."""
    globals_ = _names(inv, ("global",))
    assert {"^KG", "^NKG", "^IVG.Deploy", "^IVG.AlgoCache", "^ArnoAccel"} <= globals_
    assert any(e["kind"] == "storage" for e in inv["entries"]), "the tables' extent globals"


def test_every_table_the_ddl_creates_is_listed(inv):
    from iris_vector_graph.schema import GraphSchema

    def made(ddl: str) -> set:
        return set(re.findall(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?Graph_KG\.(\w+)", ddl, re.I))

    assert made(GraphSchema.get_base_schema_sql(embedding_dimension=768)) - _names(inv, ("table",), "setup") == set()
    # the FHIR graph's tables are made by the first fhir_graph_register, not by setup (measured, T061)
    assert made(GraphSchema.get_fhir_graph_schema_sql()) - _names(inv, ("table",), "use") == set()


def test_every_routine_setup_creates_is_listed_as_the_kind_it_is(inv):
    from iris_vector_graph.schema import GraphSchema

    ddl = "\n".join(GraphSchema.get_procedures_sql_list(table_schema="Graph_KG"))
    functions = set(re.findall(r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+Graph_KG\.(\w+)", ddl, re.I))
    procedures = set(re.findall(r"CREATE\s+(?:OR\s+REPLACE\s+)?PROCEDURE\s+Graph_KG\.(\w+)", ddl, re.I))
    sqlprocs = set(re.findall(r"SqlName\s*=\s*(\w+)\s*,\s*SqlProc",
                              (REPO / "iris_src/src/Graph/KG/SqlProcs.cls").read_text()))
    listed = _names(inv, ("function", "procedure"), "setup")
    assert (functions | procedures | sqlprocs) - listed == set()
    assert functions - _names(inv, ("function",)) == set()
    assert procedures - _names(inv, ("procedure",)) == set()


def test_the_arno_functions_are_listed_as_created_on_first_use(inv):
    src = (REPO / "iris_vector_graph/stores/arno_bridge.py").read_text()
    made = set(re.findall(r"CREATE OR REPLACE FUNCTION \{schema\}\.(ivg_arno_\w+)\(", src))
    assert len(made) == 19, sorted(made)  # the migration notes' count of 4.5.x's
    assert made == _names(inv, ("function",), "use") & made


def test_the_skipped_classes_are_the_ones_setup_skips(inv):
    """With neither embedded Python nor %AI, setup skips exactly the classes ADMIN_GUIDE lists, for the reasons it
    gives; with both it skips none."""
    from iris_vector_graph._engine.class_deploy import packaged_class_dir, read_class_sources, select_classes

    sources = read_class_sources(packaged_class_dir())
    documented = {s["class"]: s for s in inv["skipped_classes"]}
    for py, ai in ((False, False), (True, False), (False, True)):
        _, skipped = select_classes(sources, embedded_python=py, class_exists=lambda _c, ai=ai: ai)
        assert set(skipped) <= set(documented), (py, ai, sorted(set(skipped) - set(documented)))
        for cls, reason in skipped.items():
            # word for word (4.6.0 R205: the column said `needs %AI`; setup names the classes it lacks)
            assert reason in documented[cls]["reasons"], (cls, reason, documented[cls]["reasons"])
    _, none = select_classes(sources, embedded_python=True, class_exists=lambda _c: True)
    assert none == {}
    _, both_missing = select_classes(sources, embedded_python=False, class_exists=lambda _c: False)
    assert set(both_missing) == set(documented)


def test_the_scratch_script_never_drops_the_doc_namespace():
    """IVG460INV (this inventory's namespace) and the IVG460FX*/IVG460DC* scratch names are droppable (R148); the
    shared doc namespace and anything not upper case are not (tests/unit/test_make_scratch_namespace.py has the rest)."""
    mod = _load(REPO / "scripts/dev/make_scratch_namespace.py", "make_scratch_namespace_for_test")
    for ns in ("IVG460INV", "IVG460INV3", "IVG460INVC"):  # T061's own, on each build (R205)
        assert mod.may_drop(ns), ns
    for ns in ("IVG460DOC", "IVG460ALT", "USER", "ivg460inv"):
        with pytest.raises(ValueError, match="refusing to drop"):
            mod.drop(ns)
