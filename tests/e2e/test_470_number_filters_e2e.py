"""4.7.0 row 24: a number filter on a node property matches numbers only.

`rdf_props.val` keeps a number's type: a number is stored as an IRIS number (a `$DOUBLE` for a float, by the Python
API since 4.6.4 and by a Cypher write), a string as text, and the two differ in the value's `$LISTBUILD` type byte
(row 35). On 4.6.5 a number filter compared every spelling of the number with the text, so `WHERE n.p = 0.95` also
matched the strings `'.95'` and `'0.95'`, `<> 0.95` matched neither, and `<=`/`>=` matched them too. openCypher matches
only the number, `<>` holds for the strings, and a string never orders against a number (null). Now the filter reads
the type, on both routes (the native plans and SQL), for `=`, `<>`, `<`, `<=`, `>`, `>=`, the inline `{p: 0.95}`, a
parameter, and the number on either side.

The known answer: nodes num (`create_node`, 0.95), cyn (Cypher `CREATE`, 0.95), half (0.5), seven (7); strings str1
`'.95'`, str2 `'0.95'`, cys (Cypher, `'.95'`), halfs `'.5'`, sevens `'7'`; none (no p).

    IVG_R470_WR_NS=IVG470NUM pytest tests/e2e/test_470_number_filters_e2e.py
"""

from __future__ import annotations

import os

import pytest

from tests.e2e import reading_role460 as rr
from tests.e2e import scratch_vars

NS = os.environ.get("IVG_R470_WR_NS", "")
LABEL = "N470"
P = "n470_"  # node ids: these, after the prefix

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not rr.container_running(), reason=f"{rr.CONTAINER} is not running"),
    scratch_vars.requires("IVG_R470_WR_NS"),
]

NUMBERS = {"num", "cyn", "half", "seven"}
STRINGS = {"str1", "str2", "cys", "halfs", "sevens"}


@pytest.fixture(scope="module")
def eng():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = iris.connect("localhost", rr.PORT, NS, "_SYSTEM", "SYS")
    e = IRISGraphEngine(conn, embedding_dimension=rr.DIM)
    e.execute_cypher(f"MATCH (n:{LABEL}) DETACH DELETE n", raise_on_error=True)
    for nid, p in (("num", 0.95), ("half", 0.5), ("seven", 7), ("str1", ".95"), ("str2", "0.95"), ("halfs", ".5"),
                   ("sevens", "7")):
        assert e.create_node(P + nid, labels=[LABEL], properties={"p": p})
    assert e.create_node(P + "none", labels=[LABEL])
    e.execute_cypher(f"CREATE (:{LABEL} {{id: '{P}cyn', p: 0.95}}), (:{LABEL} {{id: '{P}cys', p: '.95'}})",
                     raise_on_error=True)
    yield e
    e.execute_cypher(f"MATCH (n:{LABEL}) DETACH DELETE n", raise_on_error=True)
    conn.close()


def _ids(eng, native, query, params=None) -> set:
    eng.native_cypher = native
    try:
        r = eng.execute_cypher(query, params or {}, raise_on_error=True)
    finally:
        eng.native_cypher = True
    return {row[0][len(P):] for row in r.rows}


ROUTES = pytest.mark.parametrize("native", (True, False), ids=("native", "sql"))


@ROUTES
@pytest.mark.parametrize("op, want", [
    ("=", {"num", "cyn"}),
    ("<>", {"half", "seven"} | STRINGS),
    ("<", {"half"}),
    ("<=", {"num", "cyn", "half"}),
    (">", {"seven"}),
    (">=", {"num", "cyn", "seven"}),
])
def test_a_number_comparison_reads_numbers_only(eng, native, op, want):
    assert _ids(eng, native, f"MATCH (n:{LABEL}) WHERE n.p {op} 0.95 RETURN n.id") == want


@ROUTES
@pytest.mark.parametrize("query, params", [
    (f"MATCH (n:{LABEL} {{p: 0.95}}) RETURN n.id", None),
    (f"MATCH (n:{LABEL}) WHERE n.p = $v RETURN n.id", {"v": 0.95}),
    (f"MATCH (n:{LABEL}) WHERE 0.95 = n.p RETURN n.id", None),
    ("MATCH (n {p: 0.95}) WHERE n.id STARTS WITH 'n470_' RETURN n.id", None),
], ids=("inline", "parameter", "reversed", "no_label"))
def test_every_spelling_of_the_equality_finds_the_numbers_only(eng, native, query, params):
    assert _ids(eng, native, query, params) == {"num", "cyn"}


@ROUTES
@pytest.mark.parametrize("value", (7, 7.0), ids=("int", "float"))
def test_a_whole_number_finds_the_number_not_its_text(eng, native, value):
    assert _ids(eng, native, f"MATCH (n:{LABEL}) WHERE n.p = $v RETURN n.id", {"v": value}) == {"seven"}
    assert _ids(eng, native, f"MATCH (n:{LABEL}) WHERE n.p <> $v RETURN n.id", {"v": value}) == \
        (NUMBERS - {"seven"}) | STRINGS


@ROUTES
def test_a_string_literal_still_finds_the_strings_only(eng, native):
    assert _ids(eng, native, f"MATCH (n:{LABEL}) WHERE n.p = '.95' RETURN n.id") == {"str1", "cys"}


def test_an_earlier_release_s_text_number_is_found_once_retyped(eng):
    """The text 4.6.3's create_node stored for 0.95 is a string to the filter (the visible change, CHANGELOG); the
    documented retyping (`SET n.p = toFloat(n.p)`, docs/migration) makes it the number, which the filter finds."""
    nid = P + "old"
    assert eng.create_node(nid, labels=[LABEL], properties={"p": "0.95"})  # 4.6.3's stored form of 0.95
    try:
        q = f"MATCH (n:{LABEL}) WHERE n.id = '{nid}' AND n.p = 0.95 RETURN n.id"
        assert eng.execute_cypher(q, raise_on_error=True).rows == []
        eng.execute_cypher(f"MATCH (n:{LABEL}) WHERE n.id = '{nid}' SET n.p = toFloat(n.p)", raise_on_error=True)
        assert [r[0] for r in eng.execute_cypher(q, raise_on_error=True).rows] == [nid]
    finally:
        eng.delete_node(nid)


@ROUTES
@pytest.mark.parametrize("cond, want", [("NOT n.p < 0.9", {"num", "cyn", "seven"}), ("NOT n.p > 0.9", {"half"}),
                                        ("NOT (n.p >= 0.5 AND n.p <= 1)", {"seven"})])
def test_a_negated_comparison_is_null_for_a_string(eng, native, cond, want):
    """A string never orders against a number: null, and NOT null is null, so the row is dropped either way."""
    assert _ids(eng, native, f"MATCH (n:{LABEL}) WHERE {cond} RETURN n.id") == want


@ROUTES
def test_a_comparison_returned_as_a_value_is_null_for_a_string(eng, native):
    eng.native_cypher = native
    try:
        rows = eng.execute_cypher(f"MATCH (n:{LABEL}) WHERE n.p IS NOT NULL RETURN n.id, n.p < 0.6, n.p >= 0.6",
                                  raise_on_error=True).rows
    finally:
        eng.native_cypher = True
    got = {r[0][len(P):]: (r[1], r[2]) for r in rows}
    assert got == {"num": (False, True), "cyn": (False, True), "half": (True, False), "seven": (False, True),
                   **{s: (None, None) for s in STRINGS}}


@ROUTES
def test_a_var_length_source_held_to_a_number_by_id_is_a_number(eng, native):
    """`WHERE s.id = 5` on a variable-length path goes to the BFS routes, which read the id property's holders
    (query.id_holders): a node whose id property is the number 5, not the node whose id is the string '5'."""
    import uuid

    tag = "V" + uuid.uuid4().hex[:6]
    try:
        eng.execute_cypher(f"CREATE (:{tag} {{id: 5}})-[:R470]->(:{tag} {{id: '{tag}_a'}})", raise_on_error=True)
        assert eng.create_node("5" + tag, labels=[tag], properties={"id": "5"})  # the string '5'
        assert eng.create_node(tag + "_b", labels=[tag])
        assert eng.create_edge("5" + tag, "R470", tag + "_b")
        eng.native_cypher = native
        r = eng.execute_cypher(f"MATCH (s:{tag})-[*1..2]->(m) WHERE s.id = 5 RETURN m.id", raise_on_error=True)
        assert [row[0] for row in r.rows] == [f"{tag}_a"]
    finally:
        eng.native_cypher = True
        eng.execute_cypher(f"MATCH (n:{tag}) DETACH DELETE n", raise_on_error=True)
