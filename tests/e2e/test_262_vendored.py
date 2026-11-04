"""Spec 262 US6 (T058): a project that vendors IVG's sources keeps its own `User.*` classes through the 4.6.0 upgrade.

opsreview copies IVG's `iris_src/src` into its repository with local fixes, and keeps `User.*` classes of its own that
define `SQLUser` functions. IVG262UPG is built at v4.5.1, then gains two hand-made classes of that shape: a
`User.funcSTRSPLIT` (its own `SQLUser.STR_SPLIT`) and a `User.CypherFn` whose `IVGREGEXMATCH` projects as
`SQLUser.CypherFn_IVGREGEXMATCH`. 4.6.0's deploy and `initialize_schema()` run; IVG answers its known Cypher, and both
classes are as they were: the same definition (`^oddDEF`, `TimeChanged`) and the same answers.
"""

from __future__ import annotations

import pytest

from tests.e2e import ns262

pytestmark = [pytest.mark.e2e, ns262.requires_container]

NS = "IVG262UPG"
#: The seed's `knows` edges in the default graph (generate_old_snapshot): Ada -> Bes, Bes -> Cyd.
CMP_QUERY = "MATCH (a)-[:knows]->(b) WHERE a.name < b.name RETURN a.name, b.name"
CMP_ANSWER = [["Ada", "Bes"], ["Bes", "Cyd"]]

STRSPLIT = """/// opsreview's own copy (spec 262 T058 fixture): a local fix and a marker.
Class User.funcSTRSPLIT Extends %RegisteredObject
{

ClassMethod Origin() As %String
{
	Quit "opsreview"
}

ClassMethod STRSPLIT(s As %String, d As %String) As %String [ SqlName = STR_SPLIT, SqlProc ]
{
	Set out = "", n = $Length(s, d)
	For i = 1:1:n { Set out = out _ $Select(i > 1: "|", 1: "") _ $Piece(s, d, i) }
	Quit out
}

}
"""

CYPHERFN = """/// opsreview's own CypherFn (spec 262 T058 fixture).
Class User.CypherFn Extends %RegisteredObject
{

ClassMethod Origin() As %String
{
	Quit "opsreview"
}

ClassMethod IVGREGEXMATCH(s As %String, p As %String) As %Integer [ SqlProc ]
{
	Quit $Match(s, p) + 10
}

}
"""

CLASSES = ("User.funcSTRSPLIT", "User.CypherFn")


def _load(nat, text: str) -> None:
    stream = nat.classMethodObject("%Stream.GlobalCharacter", "%New")
    stream.invoke("Write", text)
    stream.invoke("Rewind")
    status = nat.classMethodValue("%SYSTEM.OBJ", "LoadStream", stream, "ck-d")
    assert int(nat.classMethodValue("%SYSTEM.Status", "IsOK", status)), nat.classMethodValue(
        "%SYSTEM.Status", "GetErrorText", status)


def _definition(conn, nat, name: str) -> dict:
    """What a byte comparison of the class needs: its definition global's digest and when it was last saved."""
    changed = ns262.rows(conn, "SELECT TimeChanged FROM %Dictionary.ClassDefinition WHERE Name = ?", [name])
    return {"oddDEF": ns262.subtree_digest(nat, "^oddDEF", name), "TimeChanged": changed}


def _answers(conn, nat) -> dict:
    return {
        "STR_SPLIT": ns262.rows(conn, "SELECT SQLUser.STR_SPLIT('a,b', ',')")[0][0],
        "CypherFn_IVGREGEXMATCH": ns262.rows(conn, "SELECT SQLUser.CypherFn_IVGREGEXMATCH('abc', 'a.c')")[0][0],
        "origins": [nat.classMethodValue(c, "Origin") for c in CLASSES],
    }


@pytest.fixture(scope="module")
def vendored():
    from build_release_namespace import build

    from iris_vector_graph import IRISGraphEngine

    build("v4.5.1", NS)
    admin = ns262.connect(NS)
    nat = ns262.native(ns262.connect(NS))  # its own connection: createIRIS beside DDL corrupts binds
    _load(nat, STRSPLIT)
    _load(nat, CYPHERFN)
    before = {c: _definition(admin, nat, c) for c in CLASSES}
    answers_before = _answers(admin, nat)
    engine = IRISGraphEngine(admin, embedding_dimension=8, namespace=NS, native_cypher=False)
    status = engine.initialize_schema()
    yield {"conn": admin, "nat": nat, "engine": engine, "status": status, "before": before,
           "answers_before": answers_before}
    admin.close()


def test_the_handmade_classes_answer_as_opsreview_wrote_them(vendored):
    assert vendored["answers_before"] == {"STR_SPLIT": "a|b", "CypherFn_IVGREGEXMATCH": 11,
                                          "origins": ["opsreview", "opsreview"]}


def test_ivg_answers_its_known_cypher_after_the_upgrade(vendored):
    rows = vendored["engine"].execute_cypher(CMP_QUERY, {}, raise_on_error=True).rows
    assert sorted([str(v) for v in r] for r in rows) == CMP_ANSWER
    assert vendored["status"]["schema"] == "Graph_KG"


def test_the_handmade_classes_are_unchanged(vendored):
    conn, nat = vendored["conn"], vendored["nat"]
    assert {c: _definition(conn, nat, c) for c in CLASSES} == vendored["before"]
    assert _answers(conn, nat) == vendored["answers_before"]


def test_ivgs_own_str_split_is_the_ivg_schemas(vendored):
    assert ns262.rows(vendored["conn"], "SELECT Graph_KG.STR_SPLIT('a,b', ',')")[0][0] == '["a","b"]'
