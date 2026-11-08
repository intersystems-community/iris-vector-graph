"""4.6.3: the fingerprints of every text IVG shipped under a legacy-manifest name are the release tags' texts.

`remove_legacy_objects` keeps a same-named SQLUser function, view or User class whose text no release shipped (4.5.x
overwrote a namespace's own JSON_VALUE; an operator who installs theirs again must not lose it to the cleanup). The
known answers below are copied from the releases' own source (v4.5.1's and v2.0.0's `schema.py` DDL, v4.5.1's
`User.Exec.cls`) and from what IRIS 2026.3.0AI stored for them in a namespace v4.5.1 built (IVG463L, measured
2026-11-08), not from the generator's output. The live half of the check is `gen_legacy_bodies.py --live`.
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "dev"))

import gen_legacy_bodies as gen  # noqa: E402

from iris_vector_graph.legacy_bodies import CLASS_SOURCES, ROUTINE_BODIES, VIEW_QUERIES  # noqa: E402
from iris_vector_graph.legacy_manifest import LEGACY_MANIFEST  # noqa: E402
from iris_vector_graph.legacy_text import (  # noqa: E402
    class_fingerprint,
    routine_fingerprint,
    view_fingerprint,
    view_query,
)

#: v4.5.1 schema.py: the body between the braces of its `CREATE OR REPLACE FUNCTION` of JSON_VALUE in SQLUser.
JSON_VALUE_451 = ('Set obj = ##class(%Library.DynamicObject).%FromJSON(j), key = $Extract(p, 3, *), val = obj.%Get(key) '
                  'If $IsObject(val) { Quit val.%ToJSON() } Quit val _ ""')
#: v2.0.0 schema.py's JSON_VALUE: an older body under the same name.
JSON_VALUE_200 = 'Set obj = ##class(%Library.DynamicObject).%FromJSON(j), key = $Extract(p, 3, *) Quit obj.%Get(key) _ ""'
#: ROUTINE_DEFINITION of v4.5.1's SQLUser.RAND as IRIS stored it.
RAND_451_STORED = "\r\n\tQuit $RANDOM(1000000) / 1000000.0 "
#: An operator's own JSON_VALUE, one that reads a nested path ('$.a.b'), and their own RAND.
JSON_VALUE_USER = ('Set obj = ##class(%Library.DynamicObject).%FromJSON(j), path = $Extract(p, 3, *) '
                   'For i = 1:1:$Length(path, ".") { Set obj = obj.%Get($Piece(path, ".", i)) Quit:\'$IsObject(obj) } '
                   'Quit obj _ ""')
RAND_USER = "Quit $RANDOM(100) / 100"
#: VIEW_DEFINITION of v4.5.1's SQLUser.nodes as IRIS stored it.
NODES_VIEW_STORED = ('(node_id,created_at)\r\n SELECT node_id, created_at FROM Graph_KG.nodes '
                     '/*#OPTIONS {"xDBC":1,"xDBCServerProtocol":12} */')
#: v4.5.1's iris_src/src/User.Exec.cls, as `GetTextAsString` returned it.
EXEC_451_TEXT = ("/// User.Exec — Minimal ObjectScript execution helper for Python SDK.\r\n"
                 "/// Allows Python to run arbitrary ObjectScript via classMethodVoid.\r\n"
                 "Class User.Exec Extends %RegisteredObject\r\n{\r\n\r\n"
                 "ClassMethod Run(cmd As %String) As %String\r\n{\r\n    Xecute cmd\r\n    Return \"\"\r\n}\r\n\r\n}\r\n\r\n")


def test_the_file_is_what_the_tags_ship():
    if not gen.tags_available():
        pytest.skip("release tags are not in this clone; `git fetch --tags` to check the fingerprints")
    found = gen.bodies()
    assert found["templated"] == []
    assert (found["routines"], found["views"], found["classes"]) == (
        {k: list(v) for k, v in ROUTINE_BODIES.items()}, {k: list(v) for k, v in VIEW_QUERIES.items()},
        {k: list(v) for k, v in CLASS_SOURCES.items()})


def test_the_file_is_the_generators_rendering():
    found = {"routines": ROUTINE_BODIES, "views": VIEW_QUERIES, "classes": CLASS_SOURCES}
    assert gen.OUT.read_text() == gen.render(found)


def test_every_manifest_function_view_and_class_has_a_text():
    routines = {e["sql_name"].split(".", 1)[1] for e in LEGACY_MANIFEST if e["sql_name"] and e["kind"] != "view"}
    views = {e["sql_name"].split(".", 1)[1] for e in LEGACY_MANIFEST if e["kind"] == "view"}
    assert routines - set(ROUTINE_BODIES) == set()
    assert views - set(VIEW_QUERIES) == set()
    assert gen.CLASSES - set(CLASS_SOURCES) == set()


def test_each_releases_json_value_is_ivgs_and_the_operators_is_not():
    assert routine_fingerprint(JSON_VALUE_451) in ROUTINE_BODIES["JSON_VALUE"]
    assert routine_fingerprint(JSON_VALUE_200) in ROUTINE_BODIES["JSON_VALUE"]
    assert routine_fingerprint(JSON_VALUE_USER) not in ROUTINE_BODIES["JSON_VALUE"]


def test_the_stored_form_of_a_body_matches_the_ddl_form():
    assert routine_fingerprint(RAND_451_STORED) == routine_fingerprint("Quit $RANDOM(1000000) / 1000000.0")
    assert routine_fingerprint(RAND_451_STORED) in ROUTINE_BODIES["RAND"]
    assert routine_fingerprint(RAND_USER) not in ROUTINE_BODIES["RAND"]


def test_a_stored_view_definition_matches_the_ddl_query():
    assert view_fingerprint(view_query(NODES_VIEW_STORED)) in VIEW_QUERIES["nodes"]
    assert view_fingerprint(view_query("(id)\r\n SELECT id FROM MyApp.nodes")) not in VIEW_QUERIES["nodes"]


def test_a_class_source_matches_and_its_short_declaration_too():
    assert class_fingerprint("User.Exec", EXEC_451_TEXT) in CLASS_SOURCES["User.Exec"]
    short = EXEC_451_TEXT.replace("Class User.Exec ", "Class Exec ")
    assert class_fingerprint("User.Exec", short) == class_fingerprint("User.Exec", EXEC_451_TEXT)
    assert class_fingerprint("User.Exec", EXEC_451_TEXT.replace("Xecute cmd", "Write cmd")) not in CLASS_SOURCES[
        "User.Exec"]
