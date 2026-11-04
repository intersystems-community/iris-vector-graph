"""4.6.0 R141: `remove_legacy_objects` on a namespace built by v4.0.0's own code, then 4.6.0's `initialize_schema()`.

R119 checked 3.2.0's namespace; the v4.0.0 build failed (its setup echoes class loads to stdout, and the last write has
no newline, so the builder could not find its result line), and every release up to 4.4 loaded classes from the
server's `/tmp/src/` first, which on the shared container holds other runs' files. Both are fixed in
`scripts/fixtures/build_release_namespace.py`.

Hand-worked from v4.0.0's tree (`iris_src/src` and `schema.py`'s DDL) against this checkout's:

* Classes v4.0.0 ships and 4.6.0 does not, with no SQL projection: `Graph.KG.BenchFormat`, `Graph.KG.Benchmark`,
  `Graph.KG.Loader`, `User.Exec`, `User.SQL`, `iris.vector.graph.GraphOperators`, and `PageRankEmbedded.cls` (no
  package, so it compiles as `User.PageRankEmbedded`).
* `User.CypherFn`'s 17 `SqlProc` methods project as `SQLUser.CypherFn_IVG*`.
* SQLUser functions: `JSON_KEYS` (`User.funcJSONKEYS`) and `STR_SPLIT` (`User.funcSTRSPLIT`) from their classes; ten
  more from v4.0.0's `CREATE FUNCTION` DDL in SQLUser (JSON_ARRAYGET, JSON_ARRAYLENGTH, JSON_VALUE, LIST_HEAD,
  LIST_LAST, LIST_REVERSE, LIST_TAIL, NEWID, RAND, REGEX_MATCH).
* Its four compatibility views: `SQLUser.nodes`, `rdf_edges`, `rdf_labels`, `rdf_props`.

So the dry run lists 7 classes, 29 functions and 4 views, and removes none; the real run removes them all and leaves
the table classes (`Graph.KG.nodes`, `Graph.KG.rdfedges`, ...), and SQLUser holds no routine or view afterwards.

IVG262LEGA is this file's own namespace: dropped and built anew by v4.0.0's own code each run.
"""

from __future__ import annotations

import pytest

from tests.e2e import ns262

pytestmark = [pytest.mark.e2e, ns262.requires_container]

NS = "IVG262LEGA"

CLASSES = {
    "Graph.KG.BenchFormat", "Graph.KG.Benchmark", "Graph.KG.Loader", "User.Exec", "User.SQL",
    "iris.vector.graph.GraphOperators", "User.PageRankEmbedded",
}
CYPHERFN = {f"SQLUser.CypherFn_IVG{n}" for n in (
    "JSONARRAYLENGTH", "JSONKEYS", "JSONREMOVE", "JSONSET", "JSONVALUE", "LISTGET", "LISTHEAD", "LISTLAST",
    "LISTREVERSE", "LISTTAIL", "MAPGET", "NEWID", "RAND", "RANGE", "REGEXMATCH", "STRSPLIT", "TYPEERROR")}
FUNCTIONS = {f"SQLUser.{n}" for n in (
    "JSON_KEYS", "STR_SPLIT", "JSON_ARRAYGET", "JSON_ARRAYLENGTH", "JSON_VALUE", "LIST_HEAD", "LIST_LAST",
    "LIST_REVERSE", "LIST_TAIL", "NEWID", "RAND", "REGEX_MATCH")}
VIEWS = {f"SQLUser.{n}" for n in ("nodes", "rdf_edges", "rdf_labels", "rdf_props")}
TABLE_CLASSES = ["Graph.KG.nodes", "Graph.KG.rdfedges", "Graph.KG.rdflabels", "Graph.KG.rdfprops"]


def _classes(conn, names):
    marks = ", ".join("?" for _ in names)
    return {str(r[0]) for r in ns262.rows(conn, f"SELECT Name FROM %Dictionary.ClassDefinition WHERE Name IN ({marks})",
                                          list(names))}


@pytest.fixture(scope="module")
def at_400():
    from build_release_namespace import build

    from iris_vector_graph import IRISGraphEngine

    built = build("v4.0.0", NS)
    conn = ns262.connect(NS)
    engine = IRISGraphEngine(conn, embedding_dimension=8, namespace=NS, native_cypher=False)
    engine.initialize_schema()
    yield built, conn, engine
    conn.close()


def test_the_400_build_left_what_v400_ships(at_400):
    built, conn, _engine = at_400
    assert built["tag"] == "v4.0.0"
    assert {f"SQLUser.{n}" for _, n in built["routines"]} == CYPHERFN | FUNCTIONS
    assert {f"SQLUser.{n}" for n in built["views"]} == VIEWS
    assert _classes(conn, sorted(CLASSES)) == CLASSES


def test_the_dry_run_lists_every_400_object_and_removes_nothing(at_400):
    _built, conn, engine = at_400
    items = engine.remove_legacy_objects(dry_run=True)
    by_kind = {}
    for i in items:
        by_kind.setdefault(i["kind"], set()).add(i["sql_name"] or i["class_name"])
    assert by_kind == {"class": CLASSES, "function": CYPHERFN | FUNCTIONS, "view": VIEWS}
    assert not any(i["removed"] for i in items)
    assert _classes(conn, sorted(CLASSES)) == CLASSES
    assert len(ns262.rows(conn, "SELECT ROUTINE_NAME FROM INFORMATION_SCHEMA.ROUTINES "
                                "WHERE ROUTINE_SCHEMA = 'SQLUser'")) == len(CYPHERFN | FUNCTIONS)


def test_the_real_run_removes_them_and_no_table_class(at_400):
    _built, conn, engine = at_400
    kept = _classes(conn, TABLE_CLASSES)
    assert kept == set(TABLE_CLASSES)
    items = engine.remove_legacy_objects(dry_run=False)
    assert len(items) == len(CLASSES | CYPHERFN | FUNCTIONS | VIEWS) and all(i["removed"] for i in items)
    assert _classes(conn, sorted(CLASSES)) == set()
    assert _classes(conn, TABLE_CLASSES) == kept
    assert ns262.rows(conn, "SELECT ROUTINE_NAME FROM INFORMATION_SCHEMA.ROUTINES WHERE ROUTINE_SCHEMA = 'SQLUser'") == []
    assert ns262.rows(conn, "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.VIEWS WHERE TABLE_SCHEMA = 'SQLUser'") == []
    assert engine.remove_legacy_objects(dry_run=True) == []
    assert engine.get_node_count() > 0  # the seed's graph is still there
