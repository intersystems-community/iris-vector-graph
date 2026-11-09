"""4.6.0 R307, offline: the value matrix's documented answers (tests/oracle/spec.py) hold together.

The E2E matrix (tests/e2e/test_460_r307_value_matrix_e2e.py) asserts what tests/oracle/spec.py says. These check the
spec itself: that every phrase it cites is in the file it names, that its openCypher helpers give the answers Java and
openCypher give (worked by hand here), and that it reproduces the worked examples of docs/KNOWN_ISSUES.md.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

from tests.oracle import spec
from tests.oracle.values import BY_KEY, GROUPS, VALUES

REPO = Path(__file__).resolve().parents[2]


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text)


@pytest.mark.parametrize("name", sorted(spec.CITATIONS))
def test_every_citation_is_in_its_file(name):
    path, phrase = spec.CITATIONS[name]
    assert _squash(phrase) in _squash((REPO / path).read_text()), f"{name}: not in {path}: {phrase}"


def test_every_cell_has_a_documented_source():
    for store in ("node", "rel"):
        for writer, v, reader in spec.cells(store):
            got = spec.expect(store, writer, v, reader)
            assert got.source in spec.CITATIONS or got.source == spec.OPENCYPHER, (store, writer, v.key, reader, got)
        for writer in spec.WRITERS[store]:
            for group in spec.ORDER_GROUPS:
                src = spec.order(store, writer, group).source
                assert src in spec.CITATIONS or src == spec.OPENCYPHER


def test_the_value_set_covers_the_task():
    """R307 item 1, by group."""
    have = {g: [v.key for v in VALUES if v.group == g] for g in GROUPS}
    assert all(have.values()), have
    assert {"i2p53", "ibig", "fnan", "finf", "f1em7", "s007", "s1e3", "sd95", "sP", "sPat", "sP1D", "sPT1H",
            "sP1Y2M", "dP1D", "tdate1", "tdt", "strue", "sempty", "snul", "sastral", "squote", "sbs", "lnest",
            "lempty", "lmix", "m1"} <= set(BY_KEY)


@pytest.mark.parametrize(
    "x,text",
    [
        (0.95, "0.95"), (1.0, "1.0"), (2.0, "2.0"), (-0.5, "-0.5"), (1e-7, "1.0E-7"), (1e20, "1.0E20"),
        (1e7, "1.0E7"), (9999999.0, "9999999.0"), (0.001, "0.001"), (0.0001, "1.0E-4"), (0.0015, "0.0015"),
        (123.456, "123.456"), (2.0**53, "9.007199254740992E15"), (0.1 + 0.2, "0.30000000000000004"),
        (0.0, "0.0"), (math.nan, "NaN"), (math.inf, "Infinity"), (-math.inf, "-Infinity"), (-1e-7, "-1.0E-7"),
    ],
)
def test_java_double_to_string(x, text):
    """Java's Double.toString, which openCypher's toString() of a float follows."""
    assert spec.java_double(x) == text


def test_durations_sort_by_length():
    """PT1H (1 h) < PT1H30M < P1D (24 h) < PT36H < P14D < P1Y2M (14 average months); text order is the reverse."""
    want = ["PT1H", "PT1H30M", "P1D", "PT36H", "P14D", "P1Y2M"]
    assert sorted(want, key=spec.duration_seconds) == want
    assert spec.duration_seconds("P1D") == spec.duration_seconds("PT24H")
    assert spec.duration_seconds("P2W") == spec.duration_seconds("P14D")
    with pytest.raises(ValueError):
        spec.duration_seconds("Pat")


@pytest.mark.parametrize(
    "store,writer,group,keys",
    [
        # durations by length, the TCK's ISO text aside
        ("node", "cy_create", "dur", ["dPT1H", "dPT1H30M", "dP1D", "dPT36H", "dP2W", "dP1Y2M"]),
        ("rel", "cy_set", "dur", ["dPT1H", "dPT1H30M", "dP1D", "dPT36H", "dP2W", "dP1Y2M"]),
        # a node's duration text sorts as a duration (CHANGELOG R050), so by length
        ("node", "api_create", "pdurstr", ["sPT1H", "sP1D", "sP1Y2M"]),
        # duration text sorts as a duration on a relationship too (KNOWN_ISSUES)
        ("rel", "api_edge", "pdurstr", ["sPT1H", "sP1D", "sP1Y2M"]),
        # datetime < localdatetime < date (openCypher orderability)
        ("node", "cy_create", "date", ["tdt", "tldt", "tdate0", "tdate1"]),
        ("node", "cy_create", "bool", ["bfalse", "btrue"]),
        # [] first; then by first element: a list < a string < a number; [1, 'a'] < [1, 2] (string < number)
        ("node", "cy_create_p", "list", ["lempty", "lnest", "lstr", "lf", "lmix", "li"]),
        # a nested list literal is no property (Set1 [10]), so the literal writers have no lnest
        ("node", "cy_create", "list", ["lempty", "lstr", "lf", "lmix", "li"]),
        # Cypher numbers by value, NaN after +infinity; no literal for NaN or infinity
        ("node", "cy_create_p", "num",
         ["fminf", "im1", "fm05", "i0", "f1em7", "f095", "f1", "f2", "i7", "i2p53", "ibig", "f1e20", "finf", "fnan"]),
        ("node", "cy_create", "num",
         ["im1", "fm05", "i0", "f1em7", "f095", "f1", "f2", "i7", "i2p53", "ibig", "f1e20"]),
        # 4.6.4: the API's numbers are numbers, sorted as Cypher's are, but NaN and the infinities, which it still
        # stores as the text 'nan', 'inf' and '-inf': strings spelling no number, which sort before every number
        ("node", "api_create", "num",
         ["fminf", "finf", "fnan", "im1", "fm05", "i0", "f1em7", "f095", "f1", "f2", "i7", "i2p53", "ibig", "f1e20"]),
        # a string that spells a number sorts as the number whoever wrote it (KNOWN_ISSUES; openCypher would sort the
        # text, '.95' < '0.95' < '007' < '1.0' < '1e3' < '7')
        ("node", "cy_create", "numstr", ["s095", "sd95", "s10", "s007", "s7", "s1e3"]),
        ("rel", "cy_create", "numstr", ["s095", "sd95", "s10", "s007", "s7", "s1e3"]),
        # the API's text sorts as the number (DATA_FORMATS): .95 = 0.95 < 1.0 < 007 = 7 < 1e3, ties by key
        ("node", "api_create", "numstr", ["s095", "sd95", "s10", "s007", "s7", "s1e3"]),
    ],
)
def test_order_by_hand(store, writer, group, keys):
    assert spec.order(store, writer, group).value == keys


def _e(store, writer, key, reader):
    return spec.expect(store, writer, BY_KEY[key], reader).value


def _typed(x):
    return type(x).__name__, x


def test_known_issues_number_examples():
    """docs/KNOWN_ISSUES.md, "A property cannot tell a number from a string that spells it", worked examples."""
    # m1 by create_node, m2 by CREATE: m.score 0.95 for both from 4.6.4 (create_node's read '0.95' before); toString
    # both '0.95'
    assert _typed(_e("node", "api_create", "f095", "ret")) == ("float", 0.95)
    assert _typed(_e("node", "cy_create", "f095", "ret")) == ("float", 0.95)
    assert _e("node", "api_create", "f095", "tostring") == _e("node", "cy_create", "f095", "tostring") == "0.95"
    # properties(n) reads both as 0.95, and the string "0.95" too
    assert _typed(_e("node", "api_create", "f095", "props")) == ("float", 0.95)
    assert _typed(_e("node", "cy_create", "s095", "props")) == ("float", 0.95)
    assert _typed(_e("node", "cy_create", "sd95", "props")) == ("str", ".95")
    # q1 0.95, q2 ".95", q3 "0.95" by create_edge: r.score 0.95, '.95', 0.95; toString '0.95', '.95', '0.95'
    assert [_typed(_e("rel", "api_edge", k, "ret")) for k in ("f095", "sd95", "s095")] == [
        ("float", 0.95), ("str", ".95"), ("float", 0.95)]
    assert [_e("rel", "api_edge", k, "tostring") for k in ("f095", "sd95", "s095")] == ["0.95", ".95", "0.95"]
    # SET r.score = 1.0 stores the JSON number 1: toString '1'
    assert _typed(_e("rel", "cy_set", "f1", "ret")) == ("int", 1)
    assert _e("rel", "cy_set", "f1", "tostring") == "1"
    # '7' + 1 is 8; 'a7' + 1 concatenates
    assert _typed(_e("node", "cy_create", "s7", "arith")) == ("int", 8)
    assert _e("node", "cy_create", "sPat", "arith") == "Pat1"
    # SET r.p = '007' stores the string
    assert _typed(_e("rel", "cy_set", "s007", "ret")) == ("str", "007")


def test_opencypher_where_the_docs_are_silent():
    assert _typed(_e("node", "cy_create", "f1", "ret")) == ("float", 1.0)
    assert _e("node", "cy_create", "f1em7", "tostring") == "1.0E-7"
    # 4.6.4: the API's 1e-7 is a number, as Cypher's is (it was the stored string '1e-07')
    assert _e("node", "api_create", "f1em7", "tostring") == "1.0E-7"
    assert _e("node", "cy_create", "dP2W", "ret") == "P14D"
    assert _typed(_e("node", "cy_create", "sTrue", "ret")) == ("str", "True")
    assert _typed(_e("node", "api_create", "btrue", "ret")) == ("bool", True)
    assert _e("node", "cy_create", "lmix", "list_fns") == {
        "size": 4, "head": 1, "last": True, "first": 1, "any": True, "all": True}
    assert _e("node", "cy_create", "lempty", "list_fns")["head"] is None
    assert _e("node", "cy_create", "sastral", "size_str") == 1
    assert _e("node", "cy_create", "fnan", "eq_param") is False
    assert _e("node", "cy_create_p", "i2p53", "arith") == 2**53 + 2
    assert _e("node", "cy_create", "i7", "any_beside") is True


def test_strings_the_stored_form_cannot_tell_apart():
    """KNOWN_ISSUES: 'true' / 'false' read as booleans and '\\u0000' as '', on every writer and store."""
    for store, writer in (("node", "api_create"), ("node", "cy_create"), ("rel", "api_edge"), ("rel", "cy_set_p")):
        assert _typed(_e(store, writer, "strue", "ret")) == ("bool", True)
        assert _typed(_e(store, writer, "snul", "props")) == ("str", "")
    # a Cypher CREATE's float on a relationship is Python's text: an exponent form, NaN and infinity stay strings
    assert _typed(_e("rel", "cy_create", "f1em7", "ret")) == ("str", "1e-07")
    assert _typed(_e("rel", "cy_create_p", "fnan", "ret")) == ("str", "nan")
    assert _typed(_e("rel", "cy_set_p", "finf", "ret")) == ("str", "inf")
    assert _typed(_e("rel", "cy_create", "f095", "ret")) == ("float", 0.95)
    assert _typed(_e("rel", "cy_create", "f1em7", "arith")) == ("float", 1e-7 + 1)


def test_write_errors():
    assert spec.write_error("node", "cy_create", BY_KEY["m1"]).value == "TypeError"
    assert spec.write_error("rel", "cy_set", BY_KEY["lnest"]).value == "TypeError"
    assert spec.write_error("rel", "cy_merge", BY_KEY["m1"]) is None        # KNOWN_ISSUES: a map literal is stored
    assert spec.write_error("rel", "cy_create", BY_KEY["dP1D"]).value == "GraphWriteError"
    assert spec.write_error("node", "cy_create", BY_KEY["dP1D"]) is None
    assert spec.write_error("node", "cy_set_p", BY_KEY["m1"]) is None       # a map parameter is stored as JSON
    assert spec.write_error("node", "api_create", BY_KEY["mnest"]) is None  # the Python API stores JSON
    assert spec.write_error("rel", "api_edge", BY_KEY["fnan"]).value == "ValueError"
    assert spec.write_error("rel", "ndjson_edge", BY_KEY["finf"]).value == "ValueError"  # import_graph_ndjson raises it
    assert spec.write_error("node", "api_create", BY_KEY["fnan"]) is None   # a node's NaN (its text 'nan' before 4.6.4)


def test_writers_and_values():
    assert not spec.writes("node", "cy_create", BY_KEY["fnan"])     # no literal
    assert spec.writes("node", "cy_set_p", BY_KEY["fnan"])
    assert not spec.writes("node", "api_create", BY_KEY["dP1D"])    # no Python duration
    assert spec.writes("rel", "cy_merge", BY_KEY["dP1D"])
    assert not spec.applies("node", "eq_lit", BY_KEY["finf"])
    assert not spec.applies("rel", "get_node", BY_KEY["i7"])


@pytest.mark.parametrize(
    "got,want,ok",
    [
        (1, 1, True), (1, 1.0, False), (True, 1, False), ("1", 1, False), (math.nan, math.nan, True),
        ([1, 2.0], [1, 2.0], True), ([1, 2], [1, 2.0], False), ({"a": [True]}, {"a": [True]}, True),
        ({"a": 1}, {"a": True}, False), (None, None, True), ("", None, False),
    ],
)
def test_same_is_typed(got, want, ok):
    assert spec.same(got, want) is ok
