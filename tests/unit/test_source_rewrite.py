"""Spec 262 T006: the deploy rewrites IVG's class sources for the namespace's IVG Schema (FR-005, research R1).

Every packaged `.cls` is rewritten for `IVGALT`; afterwards no SQL name of IVG's (`Graph_KG`, `Graph_KG.<object>`,
`Graph.KG.<table or function class>`) survives, no reference to a code class changed, and every class that projects
SQL names the schema it projects into, so nothing compiles into a `Graph_KG` schema.
"""

import re

import pytest

from iris_vector_graph._engine.class_deploy import (
    fingerprint,
    packaged_class_dir,
    read_class_sources,
    rewrite_sources,
)

SCHEMA_TOKEN = re.compile(r"(?<![\w.%^])Graph_KG(?!\w)")
TABLE_CLASS = re.compile(r"(?<![\w.%^])Graph\.KG\.[a-z]\w*")
CODE_CLASS = re.compile(r"(?<![\w.%^])Graph\.KG\.[A-Z][\w.]*")


@pytest.fixture(scope="module")
def sources():
    class_dir = packaged_class_dir()
    assert class_dir is not None
    return read_class_sources(class_dir)


@pytest.fixture(scope="module")
def rewritten(sources):
    return rewrite_sources(sources, "IVGALT")


def test_every_source_is_rewritten(sources, rewritten):
    assert set(rewritten) == set(sources)
    assert len(sources) > 50


def test_no_schema_name_survives(rewritten):
    survivors = {n: SCHEMA_TOKEN.findall(t) for n, t in rewritten.items() if SCHEMA_TOKEN.search(t)}
    assert survivors == {}


def test_no_table_or_function_class_survives(rewritten):
    survivors = {n: sorted(set(TABLE_CLASS.findall(t))) for n, t in rewritten.items() if TABLE_CLASS.search(t)}
    assert survivors == {}


def test_table_classes_follow_the_schemas_package(sources, rewritten):
    text = rewritten["Graph.KG.Adjacency"]
    assert "IVGALT.rdfedges||idxedgesadj" in text
    assert "IVGALT.rdf_edges" in rewritten["Graph.KG.Eraser"]


def test_no_code_class_reference_changes(sources, rewritten):
    changed = {n for n in sources if CODE_CLASS.findall(sources[n]) != CODE_CLASS.findall(rewritten[n])}
    assert changed == set()


def test_globals_named_after_the_package_are_left_alone(sources, rewritten):
    assert "^Graph.KG.Meta(" in rewritten["Graph.KG.Meta"]




def test_the_default_schema_is_the_identity(sources):
    assert rewrite_sources(sources, "Graph_KG") == sources


def test_the_fingerprint_follows_the_schema(sources, rewritten):
    assert fingerprint(rewritten) != fingerprint(rewrite_sources(sources, "Graph_KG"))
    assert fingerprint(rewritten) != fingerprint(rewrite_sources(sources, "OTHER"))


def test_a_name_with_an_underscore_maps_to_its_package(sources):
    text = rewrite_sources(sources, "MY_APP")["Graph.KG.Adjacency"]
    assert "MY.APP.rdfedges||idxedgesadj" in text
