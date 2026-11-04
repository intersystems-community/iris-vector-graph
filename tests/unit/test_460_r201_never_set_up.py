"""4.6.0 R201: an engine on a namespace IVG was never set up in said it "was set up by an earlier IVG release".

Walkthrough 1 W4: `IVGBARE`, a namespace the operator made and never set up, answered `node_count()` with the
not-upgraded error, which sent them looking for a 4.5 install that did not exist. The first-access check
(`schema_name.check_first_access`, FR-006a) now tells three namespaces apart that have no record and no 4.6.0 marker,
from the Native API only (no SQL runs before it):

- one holding objects an earlier release made (a class in `legacy_manifest.LEGACY_MANIFEST`): the not-upgraded
  error, as before;
- one holding none of IVG's classes, tables or globals: a never-set-up error;
- one holding IVG's classes, tables or globals but nothing an earlier release made: setup did not finish.

Each names `initialize_schema()`, says it writes, and names its dry run. Live: tests/e2e/test_460_r201_never_set_up_e2e.py.
"""

from __future__ import annotations

import pytest

from iris_vector_graph import IVGSchemaError
from iris_vector_graph.schema_name import check_first_access


class Native:
    def __init__(self, classes=(), globals_=()):
        self.classes, self.globals = set(classes), set(globals_)
        self.calls = []

    def get(self, gname, *subs):
        self.calls.append(("get", gname) + subs)
        return None

    def isDefined(self, gname, *subs):
        self.calls.append(("isDefined", gname) + subs)
        if gname == "^oddDEF" and len(subs) == 1:
            return 10 if subs[0] in self.classes else 0
        if not subs:
            return 10 if gname in self.globals else 0
        raise AssertionError((gname, subs))

    def classMethodValue(self, cls, method, *args):
        assert (cls, method) == ("%SYSTEM.SYS", "NameSpace"), (cls, method)
        return "IVGBARE"


def _raised(native, schema="Graph_KG") -> str:
    with pytest.raises(IVGSchemaError) as raised:
        check_first_access(native, schema)
    return str(raised.value)


def _names_setup_and_says_it_writes(text):
    assert "initialize_schema()" in text and "initialize_schema(dry_run=True)" in text, text
    assert "writes" in text, text
    assert "Nothing was run." in text, text


def test_a_namespace_never_set_up_says_so():
    text = _raised(Native())
    assert "earlier IVG release" not in text
    assert "Namespace IVGBARE has never been set up for IVG" in text, text
    _names_setup_and_says_it_writes(text)


@pytest.mark.parametrize("legacy", ["User.funcCYCMP", "User.nodes", "Graph.KG.Loader", "iris.vector.graph.GraphOperators"])
def test_a_namespace_with_an_earlier_releases_objects_is_still_not_upgraded(legacy):
    text = _raised(Native(classes={legacy, "Graph.KG.PageRank"}, globals_={"^KG"}))
    assert "Namespace IVGBARE was set up by an earlier IVG release" in text, text
    _names_setup_and_says_it_writes(text)


@pytest.mark.parametrize("trace", [{"classes": {"Graph.KG.PageRank"}}, {"classes": {"Graph.KG.nodes"}},
                                   {"globals_": {"^KG"}}, {"globals_": {"^IVG.Deploy"}}])
def test_ivgs_own_objects_without_an_earlier_releases_are_an_unfinished_setup(trace):
    text = _raised(Native(**trace))
    assert "earlier IVG release" not in text and "never been set up" not in text, text
    assert "setup has not finished" in text, text
    _names_setup_and_says_it_writes(text)


def test_the_tables_probed_are_the_engines_schemas():
    text = _raised(Native(classes={"RAG.nodes"}), schema="RAG")
    assert "setup has not finished" in text, text


def test_a_namespace_with_the_marker_passes_and_probes_nothing_else():
    native = Native(classes={"Graph.KG.funcCYCMP"})
    check_first_access(native, "Graph_KG")
    assert [c for c in native.calls if c[0] == "isDefined"] == [("isDefined", "^oddDEF", "Graph.KG.funcCYCMP")]
