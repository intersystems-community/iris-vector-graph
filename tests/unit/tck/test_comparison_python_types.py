"""4.6.0 R311: a TCK result cell matches only when the answer has the expected Python type (fixes67).

The matcher read a list or map expectation through `_json_or`, which json.loads any string: `RETURN [n.x, 'a']`
answering the text '[1,"a"]' passed as the list [1, 'a'], and so did 161 passing scenarios' list answers and 27 map
answers (the da666988 captures, both drivers). A top-level integer matched the text '1', and a float matched an IRIS
Decimal. openCypher's answer is the typed value, so the harness now asks for it: a list is a list, a map a dict, an
integer an int, a float a float, a boolean a bool, at every level. Node, relationship and path values keep their own
readers (a whole node is three columns of documented text). IVG_TCK_LENIENT=1 keeps the old scoring.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.tck.steps.comparison import TCKResultTable, TCKValue


def _cmp(cell, actual):
    t = TCKResultTable(columns=["v"], rows=[[TCKValue.parse(cell)]], ordered=False, list_unordered=False)
    return t.compare([{"v": actual}], ["v"])


@pytest.fixture(autouse=True)
def _not_lenient(monkeypatch):
    monkeypatch.delenv("IVG_TCK_LENIENT", raising=False)


@pytest.mark.parametrize("cell, actual", [
    ("[1, 'a']", [1, "a"]),
    ("{a: 1, b: ['x']}", {"a": 1, "b": ["x"]}),
    ("[[1], {k: 'a'}]", [[1], {"k": "a"}]),
    ("1", 1),
    ("1.5", 1.5),
    ("true", True),
    ("'[1]'", "[1]"),
    ("'1'", "1"),
    ("[]", []),
    ("{}", {}),
])
def test_the_typed_answer_matches(cell, actual):
    assert _cmp(cell, actual) is None


@pytest.mark.parametrize("cell, actual", [
    ("[1, 'a']", '[1,"a"]'),           # a list answered as its JSON text
    ("{a: 1}", '{"a": 1}'),            # a map answered as its JSON text
    ("[[1], {k: 'a'}]", ['[1]', '{"k":"a"}']),  # nested ones as text inside a real list
    ("{a: [1]}", {"a": "[1]"}),
    ("[]", "[]"),
    ("1", "1"),                        # an integer answered as text
    ("1", Decimal("1")),
    ("1.5", "1.5"),
    ("1.5", Decimal("1.5")),           # a float answered as an IRIS Decimal
    ("true", "true"),
    ("true", 1),
    ("1", True),
    ("'[1]'", [1]),                    # a string answered as the list it spells
    ("'{\"a\": 1}'", {"a": 1}),
])
def test_another_type_does_not(cell, actual):
    assert _cmp(cell, actual) is not None


def test_lenient_keeps_the_old_scoring(monkeypatch):
    monkeypatch.setenv("IVG_TCK_LENIENT", "1")
    assert _cmp("[1, 'a']", '[1,"a"]') is None
    assert _cmp("{a: 1}", '{"a": 1}') is None
    assert _cmp("1", "1") is None
    assert _cmp("1.5", Decimal("1.5")) is None


def test_a_collected_node_in_a_list_is_read_as_a_node():
    node = {"_id": "n1", "_labels": ["A"], "_props": []}
    assert _cmp("[(:A)]", [node]) is None


def test_a_whole_node_column_keeps_its_text_render():
    t = TCKResultTable(columns=["n"], rows=[[TCKValue.parse("(:A {k: 1})")]], ordered=False, list_unordered=False)
    row = {"n_id": "n1", "n_labels": '["A"]', "n_props": '["{\\"key\\":\\"k\\",\\"value\\":1}"]'}
    assert t.compare([row], ["n_id", "n_labels", "n_props"]) is None
