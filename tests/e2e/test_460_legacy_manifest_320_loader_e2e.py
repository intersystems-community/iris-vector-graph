"""4.6.0 R119: 3.2.0's `Graph.KG.Loader` is on the legacy manifest, so `remove_legacy_objects` lists and removes it.

3.2.0 to 4.2.3 shipped `Graph.KG.Loader` (a `%RegisteredObject` file loader, no SQL projection); 4.3.0 stopped. An
upgrade compiles the new release's classes and deletes none, so a namespace that began at 3.2.0 kept it through every
upgrade, and the manifest, generated from the 4.4.0 to 4.5.1 tags alone, did not name it.

Hand-worked for a namespace built by v3.2.0's own code, then 4.6.0's `initialize_schema()`: the dry run lists
`Graph.KG.Loader` (kind `class`, no SQL name, no replacement) and the class is still there afterwards; the real run
removes it, and leaves the table classes 3.2.0 also shipped (`Graph.KG.kgNodeEmbeddings`, `Graph.KG.Edge`, which are
the projections of live tables) alone.

IVG262F9L is this file's own namespace: dropped and built anew by v3.2.0's own code each run.
"""

from __future__ import annotations

import pytest

from tests.e2e import ns262

pytestmark = [pytest.mark.e2e, ns262.requires_container]

NS = "IVG262F9L"
LOADER = "Graph.KG.Loader"


def _classes(conn, names):
    marks = ", ".join("?" for _ in names)
    return {str(r[0]) for r in ns262.rows(conn, f"SELECT Name FROM %Dictionary.ClassDefinition WHERE Name IN ({marks})",
                                          list(names))}


@pytest.fixture(scope="module")
def at_320():
    from build_release_namespace import build

    from iris_vector_graph import IRISGraphEngine

    build("v3.2.0", NS)
    conn = ns262.connect(NS)
    engine = IRISGraphEngine(conn, embedding_dimension=8, namespace=NS, native_cypher=False)
    engine.initialize_schema()
    yield conn, engine
    conn.close()


def test_the_320_namespace_has_the_loader(at_320):
    conn, _engine = at_320
    assert _classes(conn, [LOADER]) == {LOADER}


def test_the_dry_run_lists_the_loader_and_removes_nothing(at_320):
    conn, engine = at_320
    items = engine.remove_legacy_objects(dry_run=True)
    loader = [i for i in items if i["class_name"] == LOADER]
    assert [(i["kind"], i["sql_name"], i["replacement"], i["removed"]) for i in loader] == [("class", "", "", False)]
    assert _classes(conn, [LOADER]) == {LOADER}


def test_the_real_run_removes_the_loader_and_no_table_class(at_320):
    conn, engine = at_320
    tables = ["Graph.KG.kgNodeEmbeddings", "Graph.KG.Edge"]
    kept = _classes(conn, tables)
    items = engine.remove_legacy_objects(dry_run=False)
    assert [i["removed"] for i in items if i["class_name"] == LOADER] == [True]
    assert _classes(conn, [LOADER]) == set()
    assert _classes(conn, tables) == kept
    assert [i for i in engine.remove_legacy_objects(dry_run=True) if i["class_name"] == LOADER] == []
