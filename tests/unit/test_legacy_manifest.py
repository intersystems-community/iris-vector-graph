"""Spec 262 T031: the frozen 4.5.x legacy manifest is what the release tags say, names only (FR-011, FR-012).

The live half of the check (a namespace built at each tag agrees with the text) is `gen_legacy_manifest.py --live`,
run when the manifest was generated; this test regenerates the text half from the tags. A clone without the tags (CI
checks out one commit) cannot regenerate it and says so.
"""

import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "dev"))

import gen_legacy_manifest as gen  # noqa: E402

from iris_vector_graph.legacy_manifest import LEGACY_MANIFEST  # noqa: E402


def test_the_manifest_is_what_the_tags_create():
    if not gen.tags_available():
        pytest.skip(f"release tags {gen.TAGS} are not in this clone; `git fetch --tags` to check the manifest")
    assert list(LEGACY_MANIFEST) == gen.manifest()


def test_the_file_is_the_generators_rendering():
    assert gen.OUT.read_text() == gen.render(list(LEGACY_MANIFEST))


def test_no_entry_is_a_pattern():
    names = [e["sql_name"] for e in LEGACY_MANIFEST if e["sql_name"]] + [e["class_name"] for e in LEGACY_MANIFEST]
    assert [n for n in names if re.search(r"[*?%\[\]]", n)] == []


def test_both_benchmark_classes_and_the_shipped_user_classes_are_on_it():
    classes = {e["class_name"] for e in LEGACY_MANIFEST}
    assert {"Graph.KG.Benchmark", "Graph.KG.BenchFormat", "User.CypherFn", "User.funcSTRSPLIT", "User.funcJSONKEYS",
            "User.SQL", "User.Exec", "User.PageRankEmbedded", "iris.vector.graph.GraphOperators"} <= classes


def test_the_451_namespace_counts():
    """Measured on a v4.5.1 namespace (T003): 46 SQLUser functions, 4 views; plus 19 created on first Arno use."""
    at_451 = [e for e in LEGACY_MANIFEST if e["last_release"] == "4.5.1"]
    sql = [e for e in at_451 if e["sql_name"] and e["kind"] != "view" and not e["lazy"]]
    assert len(sql) == 46
    assert len([e for e in at_451 if e["kind"] == "view"]) == 4
    assert len([e for e in at_451 if e["lazy"]]) == 19


#: Created by 4.4.0-4.5.1 and never called: the translator answers head()/last() inline, and both bodies fail when
#: called. 4.6.0 creates neither, so the manifest says they have no replacement ("").
RETIRED = {"LIST_HEAD", "LIST_LAST"}


def test_list_head_and_list_last_are_retired_in_the_manifest():
    entries = {e["sql_name"]: e for e in LEGACY_MANIFEST}
    assert {n: entries[f"SQLUser.{n}"]["replacement"] for n in RETIRED} == {n: "" for n in RETIRED}
    assert set(gen.RETIRED_FUNCTIONS) == RETIRED


def test_list_head_and_list_last_are_not_created():
    from iris_vector_graph.schema import GraphSchema

    ddl = "\n".join(GraphSchema.get_procedures_sql_list(table_schema="Graph_KG"))
    assert [n for n in sorted(RETIRED) if re.search(rf"FUNCTION\s+\w+\.{n}\b", ddl)] == []


def test_the_translator_calls_neither():
    translator = (REPO / "iris_vector_graph" / "cypher" / "translator.py").read_text()
    assert [n for n in sorted(RETIRED) if n in translator] == []
