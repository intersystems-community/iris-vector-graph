"""Spec 227 T018/T019 — the ObjectScript half of the embedding re-key.

Three separate failures live behind one cause: `iris_src/src/` still described the
3.2.0 embedding tables.

1. `kgNodeEmbeddings.cls` and `kgNodeEmbeddingsoptimized.cls` declared
   `id VARCHAR(256)` + `emb` as hand-written `%Persistent` classes with
   `DdlAllowed`. A DDL `CREATE TABLE Graph_KG.kg_NodeEmbeddings` compiles to a
   class of *exactly that name* (`Graph_KG` → package `Graph.KG`, underscores
   dropped from the table name — see `GraphSchema._class_for_table`), so the two
   files were a second, hand-maintained declaration of a table the 4.0.0 DDL
   already declares, and the deploy path (`%SYSTEM.OBJ.LoadDir('/tmp/src')`,
   `scripts/enterprise-container.sh`) loads every `.cls` it finds. Deploying
   therefore replaced a correct 4.0.0 table with a 3.2.0-shaped one, at which
   point `erase_graph` fails with SQLCODE -29 on `graph_id` and no 4.0.0 write
   path can find `node_id`. The resolution is not to re-key the two files — a
   hand-written class would also have to hardcode a vector width the DDL takes as
   a parameter, which is drift waiting to happen — but to delete them and let the
   DDL be the one declaration.

2. Statements in other classes still named the removed `id` column.

3. `Graph.KG.GraphStores` still listed the embedding tables and `rdf_labels` /
   `rdf_props` as *not* graph-scoped. That inventory is what `Graph.KG.GraphVerify`
   reports from (ADR-0004), so a stale entry makes verification announce a hole
   the schema no longer has.

Asserted against source text, which is what T018/T019 can be checked by without a
container; the live gate is the container run (T024 `compile-all` plus the erase
and scale E2Es).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from iris_vector_graph.schema import GraphSchema

SRC = Path(__file__).resolve().parents[2] / "iris_src" / "src"
KG = SRC / "Graph" / "KG"

#: The two tables the 4.0.0 DDL declares and no class file may.
EMBEDDING_TABLES = ("kg_NodeEmbeddings", "kg_NodeEmbeddings_optimized")

#: Stores that carry a `graph_id` under 4.0.0 and are erased by it.
NOW_SCOPED = (
    "Graph_KG.rdf_labels",
    "Graph_KG.rdf_props",
    "Graph_KG.kg_NodeEmbeddings",
    "Graph_KG.kg_NodeEmbeddings_optimized",
)


def _class_files() -> list:
    return sorted(SRC.rglob("*.cls"))


def test_the_class_tree_is_readable():
    """Guard the guard: an empty file list would pass every scan below."""
    files = _class_files()
    assert len(files) > 30, f"found only {len(files)} .cls files under {SRC}"
    assert KG / "Eraser.cls" in files


@pytest.mark.parametrize("table", EMBEDDING_TABLES)
def test_no_class_file_declares_an_embedding_table(table):
    """One table, one declaration. The DDL's.

    A `.cls` with `SqlTableName = kg_NodeEmbeddings` wins whenever it is deployed,
    silently replacing the table the DDL built — including its width, its
    `graph_id` and its `(graph_id, node_id)` unique key.
    """
    declaring = [
        path.relative_to(SRC).as_posix()
        for path in _class_files()
        if re.search(rf"SqlTableName\s*=\s*{table}\b", path.read_text())
    ]
    assert declaring == [], (
        f"{declaring} declare Graph_KG.{table} as a persistent class. Deploying them "
        "overwrites the 4.0.0 table with the class's shape; delete the file and let "
        "the DDL in iris_vector_graph/schema.py declare it."
    )


@pytest.mark.parametrize("table", EMBEDDING_TABLES)
def test_the_ddl_still_declares_it(table):
    """The other half of the previous test: deleted from one place, present in one."""
    ddl = GraphSchema.get_base_schema_sql(embedding_dimension=384)
    assert f"CREATE TABLE Graph_KG.{table}" in ddl


def _embedding_statements(text: str) -> list:
    """Every SQL fragment in ``text`` that names an embedding table.

    From the table name to the end of the statement — the closing `)` of an `&sql(`
    group, or a line end for a built string. Crude on purpose: the question is
    whether the removed `id` column is named anywhere near one of these tables.

    Matched on `.kg_NodeEmbeddings` rather than on `Graph_KG.kg_NodeEmbeddings`,
    because `Graph.KG.LedgerApply` builds the schema by concatenation
    (`pSchema _ ".kg_NodeEmbeddings"`) and that is exactly where the first `WHERE
    id = ?` was found.
    """
    out = []
    for match in re.finditer(r"\.kg_NodeEmbeddings(?:_optimized)?\b[^)\n]*", text):
        out.append(match.group(0))
    return out


@pytest.mark.parametrize("path", _class_files(), ids=lambda p: p.name)
def test_no_class_names_the_removed_id_column_on_an_embedding_table(path):
    """`id` became `node_id` (FR-001). A statement still saying `id` gets -29.

    -29 is "field not found", and the two known offenders swallowed it:
    `LedgerApply.DeleteNodeRows` ran the DELETE without checking `%SQLCODE`, so a
    deleted node kept its vectors, and `GraphOperators.kgKNNVEC` built
    `SELECT n.id` into a statement whose prepare failure it turned into an empty
    array.
    """
    offenders = [
        fragment
        for fragment in _embedding_statements(path.read_text())
        if re.search(r"(?<![\w.])id\s*=|\bn\.id\b|\(\s*id\b|\bSELECT\s+id\b", fragment)
    ]
    assert offenders == [], (
        f"{path.name} names the removed `id` column on an embedding table: {offenders}"
    )


def _inventory_entries() -> list:
    """(name, locator, graphScoped) for every `..Entry(...)` in the inventory."""
    text = (KG / "GraphStores.cls").read_text()
    return [
        (m.group(1).replace('""', '"'), m.group(3), int(m.group(4)))
        for m in re.finditer(
            r'\.\.Entry\(\s*"((?:[^"]|"")*)"\s*,\s*"(\w+)"\s*,\s*"([^"]*)"\s*,\s*(\d)',
            text,
        )
    ]


@pytest.mark.parametrize("store", NOW_SCOPED)
def test_the_inventory_calls_a_scoped_store_scoped(store):
    """`Graph.KG.GraphVerify` reports from this list; a stale entry is a false report.

    The Eraser deletes all four by `COALESCE(graph_id, '') = COALESCE(:tName, '')`
    (see `tests/unit/test_227_erase_plan.py`), so describing them as unable to
    isolate a graph's content is no longer true.
    """
    entries = {name: (locator, scoped) for name, locator, scoped in _inventory_entries()}
    assert store in entries, f"{store} vanished from the inventory"
    locator, scoped = entries[store]
    assert scoped == 1, f"{store} is erased by graph_id but the inventory says unscoped"
    assert locator == "graph_id", f"{store} is scoped by graph_id, not by {locator!r}"


def test_every_store_still_unscoped_says_why():
    """The inventory's value is that a hole is declared, not that the list is short."""
    text = (KG / "GraphStores.cls").read_text()
    missing = []
    for match in re.finditer(
        r'\.\.Entry\(\s*"((?:[^"]|"")*)"\s*,\s*"\w+"\s*,\s*"[^"]*"\s*,\s*0\s*(,\s*"([^"]*)")?\)',
        text,
    ):
        if not (match.group(3) or "").strip():
            missing.append(match.group(1).replace('""', '"'))
    assert missing == [], f"unscoped stores with no stated reason: {missing}"


def test_the_legacy_knn_procedure_cannot_cross_graphs():
    """`iris.vector.graph.GraphOperators` ships a second `kg_KNN_VEC` (SC-010).

    It predates the generated procedure and is still deployed, because
    `GraphSchema.check_objectscript_classes` reports its presence as a capability.
    Whatever it answers, it must answer about one graph: a read path that scans
    every graph's vectors is the leak spec 227 exists to close, and after the
    re-key it would not even run, because it selected `n.id`.
    """
    text = (SRC / "iris" / "vector" / "graph" / "GraphOperators.cls").read_text()
    body = re.search(r"ClassMethod kgKNNVEC\(.*?\n}", text, re.DOTALL)
    assert body, "no kgKNNVEC in GraphOperators.cls"
    knn = body.group(0)
    assert "n.node_id" in knn, "the legacy procedure still selects the removed `id`"
    assert re.search(r"COALESCE\(n\.graph_id, ''\)\s*=\s*COALESCE\(\?", knn), (
        "the legacy kg_KNN_VEC has no graph predicate, so it scans every graph's "
        "vectors and returns another graph's neighbours. COALESCE on both sides: the "
        "column is nullable and an empty host value binds as NULL."
    )


def test_no_class_file_declares_the_edge_table():
    """`Graph.KG.Edge` was the third stale declaration of a DDL-owned table.

    It is not the same story as the embedding classes — this one was *believed* to
    be the owner: it is the class `tests/integration/test_namespace_isolation.py`
    probes to decide whether a namespace is a real IVG install, `disable_indexes`
    skips the `rdf_edges` indexes on its account
    (`iris_vector_graph/schema.py:709`), and `scripts/enterprise-container.sh`
    recompiles it after clearing the `User.*` DDL clones.

    Measured against the live container, the two declarations are not
    interchangeable. Class-owned, `Graph_KG.rdf_edges` is
    `(ID, s, p, o_id, qualifiers, graph_id)`:

    * no `edge_id` — the column 46 call sites read, including
      `_engine/nodes_edges.py`'s reification cascade and all of `_engine/prov.py`.
      `SELECT edge_id FROM Graph_KG.rdf_edges` answers SQLCODE -29.
    * no `fk_edges_source` / `fk_edges_dest`, so an edge can name a node that does
      not exist in its graph.
    * `Index uspo On (s, p, oId) [ Unique ]` — graph-blind, which is the same
      constraint on edges that spec 227 removed from `nodes`.

    So the DDL is the declaration the engine is written against, and a namespace
    where this class compiled first is one where reification and provenance were
    never reachable. Deleted for the reason T018/T019 deleted the embedding
    classes: one table, one declaration, and the DDL's is the one that works.
    """
    declaring = [
        path.relative_to(SRC).as_posix()
        for path in _class_files()
        if re.search(r"SqlTableName\s*=\s*rdf_edges\b", path.read_text())
    ]
    assert declaring == [], (
        f"{declaring} declare Graph_KG.rdf_edges. Whichever of the two declarations "
        "compiles last owns the table, and the class shape has no edge_id."
    )


def test_no_class_calls_the_deleted_edge_class():
    """A `##class(Graph.KG.Edge)` call is `<CLASS DOES NOT EXIST>` now, not a no-op.

    `Graph.KG.Ledger.RebuildEdgeIndices` guarded its call with
    `%ExistsId("Graph.KG.Edge")` and fell through to `BUILD INDEX FOR TABLE`, so
    deleting the class would have left a branch that never runs — and the guard
    itself made the dead branch look deliberate. `rdf_edges` is rebuilt with the
    other three tables instead.
    """
    offenders = {}
    for path in _class_files():
        hits = re.findall(r"##class\(Graph\.KG\.Edge\)[^\s]*", path.read_text())
        if hits:
            offenders[path.name] = hits
    assert offenders == {}, f"calls into the deleted Graph.KG.Edge: {offenders}"


def test_the_ledger_rebuilds_the_edge_table_like_the_others():
    """`rebuild_edge_indices` has to reach `rdf_edges`, or its name is a lie."""
    text = (KG / "Ledger.cls").read_text()
    body = re.search(r"ClassMethod RebuildEdgeIndices\(.*?\n}", text, re.DOTALL)
    assert body, "no RebuildEdgeIndices in Ledger.cls"
    loop = re.search(r'For tTable = ([^\n{]*)', body.group(0))
    assert loop, "RebuildEdgeIndices no longer loops over a table list"
    assert '"rdf_edges"' in loop.group(1), (
        f"rdf_edges is not in the rebuild list: {loop.group(1).strip()}"
    )


def test_the_ddl_declares_what_the_edge_class_could_not():
    """The other half: the three facts the class shape was missing."""
    ddl = GraphSchema.get_base_schema_sql(embedding_dimension=384)
    edges = ddl[ddl.index("CREATE TABLE Graph_KG.rdf_edges") :]
    edges = edges[: edges.index(");")]
    assert "edge_id" in edges, "reification and prov read rdf_edges.edge_id"
    assert "CONSTRAINT fk_edges_source FOREIGN KEY (graph_id, s)" in edges
    assert "CONSTRAINT fk_edges_dest FOREIGN KEY (graph_id, o_id)" in edges
    assert "CONSTRAINT u_spo_graph UNIQUE (s, p, o_id, graph_id)" in edges
