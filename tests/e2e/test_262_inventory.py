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
IRIS's iFind dictionary globals (`^IRIS.IF.*`) are system globals that the first property written brings into the
namespace's database for IVG's iFind indexes, so they are listed too (4.6.0 R050, expert walkthrough W6: the
inventory left them out).

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


#: The system globals IVG brings into the namespace: IRIS's iFind dictionary, for IVG's two iFind indexes.
IFIND_GLOBALS = "IRIS.IF."


def globals_now() -> set:
    """The namespace's globals, system globals left out (as tests/docs/harness.py lists them) but for IRIS's iFind
    dictionary (`IFIND_GLOBALS`), without the caret."""
    def listed(mask: str, system: int) -> set:
        raw = _session([
            'set rs=##class(%ResultSet).%New("%SYS.GlobalQuery:NameSpaceList") '
            f'do rs.Execute($namespace,"{mask}",{system}) for  quit:\'rs.Next()  write "GLOBAL:",rs.Get("Name"),!',
            'write "GLOBALS-END",!',
        ])
        if "GLOBALS-END" not in raw:
            raise RuntimeError(f"could not list the globals of {NS}: {raw[-600:]}")
        return {m.lstrip("^") for m in re.findall(r"^GLOBAL:(\S+)\s*$", raw, re.M)}

    # Masked: some system global names are not UTF-8, and the terminal's output is read as text
    return listed("*", 0) | {g for g in listed(IFIND_GLOBALS + "*", 1) if g.startswith(IFIND_GLOBALS)}


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


def _named_storage(present: str) -> set:
    """The storage globals the inventory names one by one (not the `^<hash>.<n>` pattern), by their Present."""
    return {g.lstrip("^") for g in expected(("storage",), present) if "<" not in g}


def test_named_storage_globals_are_there_exactly_when_the_inventory_says(fresh):
    """4.6.0 R205 (walkthrough 1 W8): the inventory listed the ledger tables' storage "after setup", and none of it
    exists until the ledger is enabled. Each named storage global is now checked against its Present column."""
    have = fresh["globals"]
    after_setup = _named_storage("setup")
    later = {g.lstrip("^") for e in inventory()["entries"] if e["kind"] == "storage" and e["present"] != "setup"
             for g in e["names"] if "<" not in g}
    assert after_setup - have == set()
    assert later & have == set()
    for g in later | after_setup:
        assert owner_of(g, storage_globals(fresh["conn"], fresh["catalog"]["classes"])), f"^{g} is no class's storage"


def _kg_entry() -> dict:
    return next(e for e in inventory()["entries"] if e["names"] == ["^KG"])


def kg_subscripts(conn) -> set:
    nat = ns262.native(conn)
    out, sub = set(), ""
    while True:
        sub = nat.nextSubscript(False, "^KG", sub)
        if sub is None or sub == "":
            return out
        out.add(str(sub))


def test_kg_after_setup_holds_its_version_alone(fresh):
    assert kg_subscripts(fresh["conn"]) == {"__version"}
    assert '`"__version"`' in _kg_entry()["purpose"]


def test_nkg_is_not_there_after_setup(fresh):
    assert "NKG" not in fresh["globals"]
    assert next(e for e in inventory()["entries"] if e["names"] == ["^NKG"])["present"] == "use"


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
    # The reason setup gives is, word for word, one the inventory's "Reason setup gives" column gives for that class
    # (4.6.0 R205: the column said `needs %AI` where setup says which classes and that they are not installed).
    mismatch = {c: r for c, r in status_skipped.items() if r not in documented[c]["reasons"]}
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
    # 39 at R133; R182 added CY_FLOAT_TEXT, CY_TOSTRING, CY_REL_TOSTRING; R210 CY_JSON_TYPE, CY_VALUE_TYPE; R218 CY_TYPED;
    # R240 CY_ARITH; R248 CY_JSON_NUM; R250 CY_IS_FLOAT, R227 CY_LIST_ELEM, R273 CY_LIST_VAL;
    # R307 CY_STR_SIZE, CY_LIST_SRC, CY_JSON_LISTMAP
    assert len(function_classes) == 54
    assert status["routines"] == {"created": [], "unchanged": 54, "failed": {}}
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
    assert status["routines"] == {"created": ["CY_CMP"], "unchanged": 53, "failed": {}}  # the 54 setup creates, less CY_CMP
    cur.execute("SELECT Graph_KG.CY_CMP('1', '2')")
    assert cur.fetchone()[0] == answer  # callable again, and the same function (its answer is not the question here)
    still = (_routine_classes_of_setup(conn, classes) - {"Graph.KG.funcCYCMP"})
    assert {c for c in still if before.get(c) != after.get(c)} == set()


# --------------------------------------------------------------------------- first use (last: they write)


def test_every_kg_subtree_first_use_writes_is_one_the_inventory_names(fresh):
    """4.6.0 R205 (W8): the ^KG row named degrees that no longer exist (spec 248 replaced them) and left out "gen". A
    write, a degree read and a temporal write fill ^KG; every subtree they leave is named in the row's purpose, and
    every subtree the row names is one of them."""
    from iris_vector_graph import IRISGraphEngine

    conn = fresh["conn"]
    engine = IRISGraphEngine(conn, embedding_dimension=768)
    assert engine.create_node("alice", labels=["Person"], properties={"name": "Alice"}) is True
    assert engine.create_node("bob", labels=["Person"]) is True
    assert engine.create_edge("alice", "KNOWS", "bob") is True
    assert {"out", "in", "label", "gen"} <= kg_subscripts(conn)
    assert {d["id"]: d["degree"] for d in engine.degree_centrality()}["alice"] == 1
    assert engine.create_edge_temporal("alice", "MET", "bob", timestamp=1000, attrs={"where": "x"}) is True
    named = set(re.findall(r'`"(\w+)"`', _kg_entry()["purpose"]))
    have = kg_subscripts(conn)
    assert have - named == set(), "^KG subtrees the inventory does not name"
    # degw is the degree cache's whole-graph stamp, written when setup or sync() warms it (next test)
    assert named - have - {"degw"} == set(), "^KG subtrees the inventory names that first use did not write"


def _ifind_listed() -> set:
    return {g.lstrip("^") for e in inventory()["entries"] if e["kind"] == "global" for g in e["names"]
            if g.startswith("^" + IFIND_GLOBALS)}


def test_the_first_property_brings_the_ifind_dictionary_globals(fresh):
    """4.6.0 R050 (expert walkthrough W6): `^IRIS.IF.*` were in no inventory row. Measured: setup does not make them
    (the fixture's namespace has none); the first node property written does, the inventory's five exactly."""
    from iris_vector_graph import IRISGraphEngine

    listed = _ifind_listed()
    assert listed, "the inventory lists no iFind dictionary global"
    assert {e["present"] for e in inventory()["entries"] if any(g.lstrip("^") in listed for g in e["names"])} == {"use"}
    assert {g for g in fresh["globals"] if g.startswith(IFIND_GLOBALS)} == set()
    engine = IRISGraphEngine(fresh["conn"], embedding_dimension=768)
    assert engine.create_node("ifind_probe", labels=["Probe"], properties={"name": "first words"}) is True
    assert {g for g in globals_now() - fresh["globals_before"] if g.startswith(IFIND_GLOBALS)} == listed


def test_a_setup_run_that_finds_edges_builds_nkg_once(fresh):
    """4.6.0 R205 (W8): the walkthrough's second initialize_schema() built ^NKG, which the inventory said only
    engine.sync() does. Setup bootstraps ^KG and ^NKG once, the first time it finds rows in rdf_edges."""
    from iris_vector_graph import IRISGraphEngine

    conn = fresh["conn"]
    nat = ns262.native(conn)
    assert not nat.isDefined("^NKG")
    IRISGraphEngine(conn, embedding_dimension=768).initialize_schema()
    assert nat.isDefined("^NKG")
    assert "degw" in kg_subscripts(conn)
    entry = next(e for e in inventory()["entries"] if e["names"] == ["^NKG"])
    assert "engine.sync()" in entry["purpose"] and "setup" in entry["purpose"], entry["purpose"]


def test_enabling_the_ledger_writes_its_storage_globals(fresh):
    from iris_vector_graph import IRISGraphEngine

    conn = fresh["conn"]
    engine = IRISGraphEngine(conn, embedding_dimension=768)
    engine.ledger.enable()
    try:
        now = globals_now()
        ledger = {g.lstrip("^") for e in inventory()["entries"] if e["kind"] == "storage" and e["present"] == "activation"
                  for g in e["names"]}
        # ^Graph.KG.LedgerStatsI is named by the class storage and never written: the table's one index is its ID
        assert ledger - {"Graph.KG.LedgerStatsI"} <= now
        assert "Graph.KG.LedgerStatsI" not in now
        assert "IVG.Ledger" in now
    finally:
        engine.ledger.disable()
