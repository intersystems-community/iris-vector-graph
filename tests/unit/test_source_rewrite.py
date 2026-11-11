"""Spec 262 T006: the deploy rewrites IVG's class sources for the namespace's IVG Schema (FR-005, research R1).

Every packaged `.cls` is rewritten for `IVGALT`; afterwards no SQL name of IVG's (`Graph_KG`, `Graph_KG.<object>`,
`Graph.KG.<table or function class>`) survives, the schema classes (the SQL procedures' forwarder and the ledger tables,
R084) move to the schema's package, and no other reference to a code class changed. The code classes' own
`SqlSchemaName = Graph_KG` is left as it is: rewritten, it made IRIS map the IVG Schema onto package `Graph.KG` (R084).
"""

import re

import pytest

from iris_vector_graph._engine.class_deploy import (
    fingerprint,
    packaged_class_dir,
    read_class_sources,
    rewrite_sources,
)

SCHEMA_TOKEN = re.compile(r"(?<![\w.%^])(?<!SqlSchemaName = )Graph_KG(?!\w)")
TABLE_CLASS = re.compile(r"(?<![\w.%^])Graph\.KG\.[a-z]\w*")
MOVED = ("SqlProcs", "LedgerRevision", "LedgerStats")
MOVED_CLASS = re.compile(r"(?<![\w.%^])Graph\.KG\.(?:SqlProcs|LedgerRevision|LedgerStats)\b")
CODE_CLASS = re.compile(r"(?<![\w.%^])Graph\.KG\.(?!(?:SqlProcs|LedgerRevision|LedgerStats)\b)[A-Z][\w.]*")


@pytest.fixture(scope="module")
def sources():
    class_dir = packaged_class_dir()
    assert class_dir is not None
    return read_class_sources(class_dir)


@pytest.fixture(scope="module")
def rewritten(sources):
    return rewrite_sources(sources, "IVGALT")


def test_every_source_is_rewritten(sources, rewritten):
    moved = {f"Graph.KG.{c}" for c in MOVED}
    assert set(rewritten) == (set(sources) - moved) | {f"IVGALT.{c}" for c in MOVED}
    assert len(sources) > 50


def test_the_schema_classes_move_to_the_schemas_package(rewritten):
    """R084: the SQL procedures' forwarder and the ledger tables compile into package IVGALT, beside the DDL's."""
    for short in MOVED:
        assert re.search(rf"^Class IVGALT\.{short} ", rewritten[f"IVGALT.{short}"], re.M), short
    assert "##class(IVGALT.LedgerRevision).%OpenId(" in rewritten["Graph.KG.Ledger"]
    assert "##class(IVGALT.LedgerStats).Ensure()" in rewritten["Graph.KG.Ledger"]
    assert {n for n, t in rewritten.items() if MOVED_CLASS.search(t)} == set()


def test_the_code_classes_keep_their_sql_schema_name(sources, rewritten):
    """R084: `SqlSchemaName = IVGALT` on a class in package `Graph.KG` maps schema IVGALT onto that package."""
    declaring = {n for n, t in sources.items() if "SqlSchemaName = Graph_KG" in t}
    assert declaring == {f"Graph.KG.{c}" for c in
                         ("Meta", "PageRank", "Traversal", "Centrality", "Communities", "TraversalPaths")}
    assert all("SqlSchemaName = Graph_KG" in rewritten[n] for n in declaring)
    assert [n for n, t in rewritten.items() if re.search(r"SqlSchemaName = (?!Graph_KG\b)", t)] == []


def test_no_schema_name_survives(rewritten):
    survivors = {n: SCHEMA_TOKEN.findall(t) for n, t in rewritten.items() if SCHEMA_TOKEN.search(t)}
    assert survivors == {}


def test_no_table_or_function_class_survives(rewritten):
    survivors = {n: sorted(set(TABLE_CLASS.findall(t))) for n, t in rewritten.items() if TABLE_CLASS.search(t)}
    assert survivors == {}


def test_table_classes_follow_the_schemas_package(sources, rewritten):
    text = rewritten["Graph.KG.Adjacency"]
    assert 'IndexKind(pTableClass As %String = "IVGALT.rdfedges")' in text  # Indexed() asks IndexKind() (4.7.0)
    assert "IVGALT.rdf_edges" in rewritten["Graph.KG.Eraser"]


def test_no_code_class_reference_changes(sources, rewritten):
    def renamed(n):
        return n.replace("Graph.KG.", "IVGALT.", 1) if n.rsplit(".", 1)[-1] in MOVED else n

    changed = {n for n in sources if CODE_CLASS.findall(sources[n]) != CODE_CLASS.findall(rewritten[renamed(n)])}
    assert changed == set()


def test_globals_named_after_the_package_are_left_alone(sources, rewritten):
    assert "^Graph.KG.Meta(" in rewritten["Graph.KG.Meta"]




def test_the_default_schema_is_the_identity(sources):
    assert rewrite_sources(sources, "Graph_KG") == sources


def test_the_fingerprint_follows_the_schema(sources, rewritten):
    assert fingerprint(rewritten) != fingerprint(rewrite_sources(sources, "Graph_KG"))
    assert fingerprint(rewritten) != fingerprint(rewrite_sources(sources, "OTHER"))


def test_a_name_with_an_underscore_maps_to_its_package(sources):
    rewritten = rewrite_sources(sources, "MY_APP")
    assert 'IndexKind(pTableClass As %String = "MY.APP.rdfedges")' in rewritten["Graph.KG.Adjacency"]
    assert {f"MY.APP.{c}" for c in MOVED} <= set(rewritten)
