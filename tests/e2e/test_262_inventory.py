"""Spec 262 T061 (FR-017, FR-018, SC-002): ADMIN_GUIDE's inventory is what a fresh namespace holds, both ways.

A namespace with a new database of its own (IVG460INV, or `IVG_INVENTORY_NS`, dropped and made anew) gets one `initialize_schema()`, as an
operator would run it: the default IVG Schema, `embedding_dimension=768`. Its packages, tables, views, routines and
globals are then read from the catalog and compared with `docs/inventory.json` (generated from ADMIN_GUIDE's
inventory tables by `scripts/dev/gen_inventory_json.py`), in both directions:

- every object the inventory says setup creates is there, and nothing else is (0 differences);
- an object the inventory says appears only on first use or when an operator turns it on is not there yet;
- every global is either one the inventory names or the storage of a table the inventory names;
- every class is a shipped class, the class IRIS made for an inventory table, or the class holding an inventory
  routine; a shipped class that is absent was skipped by setup, for a reason the inventory gives.

Globals are listed the way the doc-test harness lists them (`%SYS.GlobalQuery:NameSpaceList`, system globals left
out) and only the ones setup added count: a new database can hold a global or two of IRIS's own before IVG arrives.

A second `initialize_schema()` then shows what re-running setup changes (ADMIN_GUIDE, "Running setup again").
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from tests.e2e import ns262

pytestmark = [pytest.mark.e2e, ns262.requires_container]

REPO = Path(__file__).resolve().parents[2]
INVENTORY = REPO / "docs" / "inventory.json"
SOURCES = REPO / "iris_src" / "src"
NS = os.environ.get("IVG_INVENTORY_NS", "IVG460INV")
if not re.fullmatch(r"IVG460INV[A-Z0-9]*", NS):
    raise ValueError(f"IVG_INVENTORY_NS={NS!r}: the inventory test drops its namespace, so it must start IVG460INV")

_SYSTEM_SCHEMA = re.compile(r"^(%|INFORMATION_SCHEMA$)")


# --------------------------------------------------------------------------- reading the namespace


def _session(lines: list) -> str:
    script = "\n".join(lines + ["halt"]) + "\n"
    out = subprocess.run(["docker", "exec", "-i", ns262.CONTAINER, "iris", "session", "IRIS", "-U", NS],
                         input=script, capture_output=True, text=True, timeout=300, check=True)
    return out.stdout


def globals_now() -> set:
    """The namespace's globals, system globals left out (as tests/docs/harness.py lists them), without the caret."""
    raw = _session([
        'set rs=##class(%ResultSet).%New("%SYS.GlobalQuery:NameSpaceList") do rs.Execute($namespace,"*",0) '
        'for  quit:\'rs.Next()  write "GLOBAL:",rs.Get("Name"),!',
        'write "GLOBALS-END",!',
    ])
    if "GLOBALS-END" not in raw:
        raise RuntimeError(f"could not list the globals of {NS}: {raw[-600:]}")
    return {m.lstrip("^") for m in re.findall(r"^GLOBAL:(\S+)\s*$", raw, re.M)}


def catalog(conn, iris_classes: frozenset = frozenset()) -> dict:
    """The namespace's SQL objects and classes; `iris_classes` (those an empty namespace already shows: IRIS's own
    mapped `INFORMATION.SCHEMA` classes) are left out."""
    tables = {str(t) for s, t, typ in ns262.rows(
        conn, "SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE FROM INFORMATION_SCHEMA.TABLES")
        if not _SYSTEM_SCHEMA.match(str(s)) and str(typ) == "BASE TABLE"}
    table_schemas = {str(s) for s, t, typ in ns262.rows(
        conn, "SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE FROM INFORMATION_SCHEMA.TABLES")
        if not _SYSTEM_SCHEMA.match(str(s))}
    views = {str(t) for s, t in ns262.rows(conn, "SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.VIEWS")
             if not _SYSTEM_SCHEMA.match(str(s))}
    routines = {}
    routine_schemas = set()
    for s, n, typ in ns262.rows(conn, "SELECT ROUTINE_SCHEMA, ROUTINE_NAME, ROUTINE_TYPE "
                                      "FROM INFORMATION_SCHEMA.ROUTINES"):
        if _SYSTEM_SCHEMA.match(str(s)):
            continue
        routines[str(n)] = str(typ).lower()
        routine_schemas.add(str(s))
    classes = ns262.classes(conn) - set(iris_classes)
    return {
        "tables": tables,
        "views": views,
        "routines": routines,
        "schemas": table_schemas | routine_schemas,
        "classes": classes,
        "packages": {c.rsplit(".", 1)[0] for c in classes if "." in c},
    }


def storage_globals(conn, classes: set) -> dict:
    """{global name: class} for every global a class in `classes` stores its rows or indexes in. An extent-set
    class (DDL's) keeps its rows in `^t52S.Ab3.1` and each index in a global of its own beside it (`.2`, `.3`, ...,
    `%Dictionary.CompiledStorageIndex`); a class-defined table in `^<class>D` / `I` / `S`."""
    owned: dict = {}
    prefixes: dict = {}
    cols = ("DataLocation", "IndexLocation", "StreamLocation", "IdLocation", "CounterLocation")
    for row in ns262.rows(conn, f"SELECT parent, {', '.join(cols)} FROM %Dictionary.CompiledStorage"):
        cls = str(row[0])
        if cls not in classes:
            continue
        for loc in row[1:]:
            loc = str(loc or "")
            if not loc.startswith("^") or "{" in loc:
                continue
            name = loc[1:].split("(", 1)[0]
            owned[name] = cls
            if name.endswith(".1"):  # an extent set's data global: its index globals share the stem
                prefixes[name[:-1]] = cls
    for parent, loc in ns262.rows(conn, "SELECT parent, Location FROM %Dictionary.CompiledStorageIndex"):
        cls = str(parent).split("||", 1)[0]
        if cls in classes and str(loc or "").startswith("^"):
            owned[str(loc)[1:].split("(", 1)[0]] = cls
    return {"exact": owned, "prefixes": prefixes}


def owner_of(name: str, storage: dict):
    if name in storage["exact"]:
        return storage["exact"][name]
    for prefix, cls in storage["prefixes"].items():
        if name.startswith(prefix):
            return cls
    return None


def table_classes(conn) -> dict:
    """{class: SQL table name} for the namespace's persistent classes."""
    return {str(c): str(t) for c, t in ns262.rows(
        conn, "SELECT Name, SqlTableName FROM %Dictionary.CompiledClass WHERE ClassType = 'persistent'")}


def routine_classes(conn) -> dict:
    """{class: {SQL name, ...}} for every class that projects a method or query as an SQL routine."""
    out: dict = {}
    for cls, name in ns262.rows(conn, "SELECT parent, SqlName FROM %Dictionary.CompiledMethod WHERE SqlProc = 1"):
        out.setdefault(str(cls), set()).add(str(name))
    for cls, name in ns262.rows(conn, "SELECT parent, SqlName FROM %Dictionary.CompiledQuery WHERE SqlProc = 1"):
        out.setdefault(str(cls), set()).add(str(name))
    return out


def shipped_classes() -> set:
    return {".".join(p.relative_to(SOURCES).with_suffix("").parts) for p in SOURCES.rglob("*.cls")}


def time_changed(conn, classes: set) -> dict:
    return {str(n): str(t) for n, t in ns262.rows(conn, "SELECT Name, TimeChanged FROM %Dictionary.ClassDefinition")
            if str(n) in classes}


# --------------------------------------------------------------------------- the inventory


def inventory() -> dict:
    return json.loads(INVENTORY.read_text())


def expected(kinds: tuple, present: str = "setup") -> set:
    return {n for e in inventory()["entries"] if e["kind"] in kinds and e["present"] == present for n in e["names"]}


def expected_routine_kinds() -> dict:
    return {n: e["kind"] for e in inventory()["entries"] if e["kind"] in ("function", "procedure")
            and e["present"] == "setup" for n in e["names"]}


def not_yet(kinds: tuple) -> set:
    return {n for e in inventory()["entries"] if e["kind"] in kinds and e["present"] != "setup" for n in e["names"]}


def _diff(label: str, want: set, have: set) -> list:
    out = []
    if want - have:
        out.append(f"{label} the inventory lists that the namespace lacks: {sorted(want - have)}")
    if have - want:
        out.append(f"{label} the namespace holds that the inventory does not list: {sorted(have - want)}")
    return out


# --------------------------------------------------------------------------- the namespace


def _activated_by_the_server(nat) -> set:
    """Globals a fresh namespace holds because of the server, not setup. ^ArnoAccel("lib_path") is written by a
    successful Arno load, which setup's own engine makes when the library is at a path it tries (the test container
    has one at /tmp/libarno_callout.so); the inventory lists ^ArnoAccel "when activated"."""
    return {"ArnoAccel"} if nat.isDefined("^ArnoAccel", "lib_path") else set()


@pytest.fixture(scope="module")
def fresh():
    import make_scratch_namespace
    from iris_vector_graph import IRISGraphEngine

    make_scratch_namespace.drop(NS)
    make_scratch_namespace.make(NS)
    before = globals_now()
    conn = ns262.connect(NS)
    iris_classes = frozenset(ns262.classes(conn))
    engine = IRISGraphEngine(conn, embedding_dimension=768)
    status = engine.initialize_schema()
    after = globals_now()
    cat = catalog(conn, iris_classes)
    yield {"conn": conn, "engine": engine, "status": status, "catalog": cat, "iris_classes": iris_classes,
           "globals_before": before, "globals": after - before,
           "activated": _activated_by_the_server(ns262.native(conn))}
    conn.close()


def test_the_inventory_names_its_schema(fresh):
    assert fresh["status"]["schema"] == inventory()["schema"] == "Graph_KG"


def test_every_sql_object_is_in_the_ivg_schema(fresh):
    assert fresh["catalog"]["schemas"] == {inventory()["schema"]}


def test_tables(fresh):
    assert _diff("tables", expected(("table",)), fresh["catalog"]["tables"]) == []


def test_views(fresh):
    assert _diff("views", expected(("view",)), fresh["catalog"]["views"]) == []


def test_routines(fresh):
    assert _diff("routines", expected(("function", "procedure")), set(fresh["catalog"]["routines"])) == []


def test_each_routine_is_the_kind_the_inventory_says(fresh):
    have = fresh["catalog"]["routines"]
    wrong = {n: (k, have[n]) for n, k in expected_routine_kinds().items() if n in have and have[n] != k}
    assert wrong == {}, "{name: (inventory, INFORMATION_SCHEMA.ROUTINES)}"


def test_packages(fresh):
    assert _diff("packages", expected(("package",)), fresh["catalog"]["packages"]) == []


def test_globals(fresh):
    """The globals setup added: each is named by the inventory, or is the storage of an inventory table."""
    conn = fresh["conn"]
    cat = fresh["catalog"]
    storage = storage_globals(conn, cat["classes"])
    tables_by_class = table_classes(conn)
    named, unlisted_storage = set(), {}
    for g in fresh["globals"] - fresh["activated"]:
        cls = owner_of(g, storage)
        if cls is None:
            named.add(g)
        elif tables_by_class.get(cls) not in expected(("table",)):
            unlisted_storage[g] = cls
    problems = _diff("globals", {g.lstrip("^") for g in expected(("global",))}, named)
    if unlisted_storage:
        problems.append(f"storage globals of classes that are not inventory tables: {unlisted_storage}")
    assert problems == []


def test_nothing_the_inventory_defers_is_there_yet(fresh):
    """Rows marked "on first use" or "when activated" are absent from a namespace only setup has touched."""
    cat = fresh["catalog"]
    early = (not_yet(("table",)) & cat["tables"]) | (not_yet(("view",)) & cat["views"])
    early |= not_yet(("function", "procedure")) & set(cat["routines"])
    early |= not_yet(("package",)) & cat["packages"]
    early |= {g.lstrip("^") for g in not_yet(("global",))} & (fresh["globals"] - fresh["activated"])
    assert early == set()


def test_every_class_is_shipped_or_belongs_to_an_inventory_object(fresh):
    conn = fresh["conn"]
    cat = fresh["catalog"]
    shipped = shipped_classes()
    tables = table_classes(conn)
    routines = routine_classes(conn)
    listed_tables = expected(("table",))
    listed_routines = expected(("function", "procedure"))
    listed_table_classes = {c for c, t in tables.items() if t in listed_tables}

    def accounted(c: str) -> bool:
        return (c in shipped or c in listed_table_classes
                # an iFind index's own classes, in the package of its table's class (Graph.KG.docs.NTEmbg)
                or any(c.startswith(t + ".") for t in listed_table_classes)
                or bool(routines.get(c) and routines[c] <= listed_routines))

    assert sorted(c for c in cat["classes"] if not accounted(c)) == []


def test_a_shipped_class_is_absent_only_when_setup_skipped_it_for_a_reason_the_inventory_gives(fresh):
    status_skipped = dict(fresh["status"].get("objectscript_skipped") or {})
    absent = shipped_classes() - fresh["catalog"]["classes"]
    assert absent == set(status_skipped), {"absent": sorted(absent), "skipped": status_skipped}
    documented = {s["class"]: s for s in inventory()["skipped_classes"]}
    undocumented = sorted(set(status_skipped) - set(documented))
    assert undocumented == [], "skipped classes ADMIN_GUIDE does not list"
    # The reason setup gives must be one the inventory gives for that class.
    mismatch = {c: r for c, r in status_skipped.items()
                if not any(key in r for key in documented[c]["reasons"])}
    assert mismatch == {}
    print("\nskipped on this server:", json.dumps(status_skipped, indent=1, sort_keys=True))


#: Classes a re-run of setup rewrites besides the function and procedure classes, each a defect ADMIN_GUIDE states
#: ("Running setup again"). A ratchet: when one is fixed this test fails until it leaves the list and the guide.
#: Graph.KG.LedgerRevision left it with R137 (the first run creates idx_ledger_correlation), Graph.KG.rdfedges with
#: R138 (tighten_graph_id_column alters graph_id only when it is not yet NOT NULL DEFAULT '').
RERUN_REWRITES: set = set()


def test_the_first_run_creates_the_ledger_correlation_index(fresh):
    """4.6.0 R137: one setup on a fresh namespace, and the index is there (it used to need a second run)."""
    names = {str(r[0]) for r in ns262.rows(fresh["conn"], "SELECT Name FROM %Dictionary.CompiledIndex "
                                                          "WHERE parent = 'Graph.KG.LedgerRevision'")}
    assert "idxledgercorrelation" in names
    sql_names = {str(r[0]) for r in ns262.rows(
        fresh["conn"], "SELECT INDEX_NAME FROM INFORMATION_SCHEMA.INDEXES "
                       "WHERE TABLE_SCHEMA = 'Graph_KG' AND TABLE_NAME = 'ledger_revisions'")}
    assert "idx_ledger_correlation" in sql_names
    # "present", not "created": the class declares the index since 4.6.0 R150, so the deploy's compile made it
    assert fresh["status"]["ledger_index"] == "present"


def _routine_classes_of_setup(conn, classes: set) -> set:
    """The classes IRIS generated for setup's DDL functions and procedures (not shipped, not a table's)."""
    tables = table_classes(conn)
    return {c for c in routine_classes(conn) if c in classes and c not in shipped_classes() and c not in tables}


def test_running_setup_again_changes_no_row_and_rewrites_no_class(fresh):
    """ADMIN_GUIDE, "Running setup again": the same objects and rows, and no class rewritten. Before 4.6.0 R133 the
    39 function and procedure classes were (CREATE OR REPLACE on each), and the RERUN_REWRITES defects."""
    from iris_vector_graph import IRISGraphEngine

    conn = fresh["conn"]
    classes = fresh["catalog"]["classes"]
    before = time_changed(conn, classes)
    rows_before = ns262.deep_snapshot(conn)["rows"]
    status = IRISGraphEngine(conn, embedding_dimension=768).initialize_schema()
    after = time_changed(conn, classes)
    rows_after = ns262.deep_snapshot(conn)["rows"]
    assert catalog(conn, fresh["iris_classes"]) == fresh["catalog"]
    assert rows_after == rows_before
    rewritten = {c for c in classes if before.get(c) != after.get(c)}
    function_classes = _routine_classes_of_setup(conn, classes)
    # 39 at R133; R182 added CY_FLOAT_TEXT, CY_TOSTRING, CY_REL_TOSTRING; R210 CY_JSON_TYPE, CY_VALUE_TYPE
    assert len(function_classes) == 44
    assert status["routines"] == {"created": [], "unchanged": 44, "failed": {}}
    assert rewritten & function_classes == set()
    assert rewritten - function_classes == RERUN_REWRITES == set()


def test_a_routine_dropped_by_hand_is_created_again_by_the_next_run_and_only_it(fresh):
    """R133: the skip is keyed on the routine being there too, not the record alone."""
    from iris_vector_graph import IRISGraphEngine

    conn = fresh["conn"]
    classes = fresh["catalog"]["classes"]
    cur = conn.cursor()
    cur.execute("SELECT Graph_KG.CY_CMP('1', '2')")
    answer = cur.fetchone()[0]
    cur.execute("DROP FUNCTION Graph_KG.CY_CMP")
    conn.commit()
    gone = {str(r[0]) for r in ns262.rows(conn, "SELECT ROUTINE_NAME FROM INFORMATION_SCHEMA.ROUTINES "
                                                "WHERE ROUTINE_SCHEMA = 'Graph_KG'")}
    assert "CY_CMP" not in gone
    before = time_changed(conn, classes)
    status = IRISGraphEngine(conn, embedding_dimension=768).initialize_schema()
    after = time_changed(conn, classes)
    assert status["routines"] == {"created": ["CY_CMP"], "unchanged": 41, "failed": {}}  # 42 less CY_CMP
    cur.execute("SELECT Graph_KG.CY_CMP('1', '2')")
    assert cur.fetchone()[0] == answer  # callable again, and the same function (its answer is not the question here)
    still = (_routine_classes_of_setup(conn, classes) - {"Graph.KG.funcCYCMP"})
    assert {c for c in still if before.get(c) != after.get(c)} == set()
