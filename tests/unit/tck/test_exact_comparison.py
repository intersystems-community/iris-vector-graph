"""The TCK result comparison is exact: no subset nodes, no ignored columns, no
direction-blind paths, null only equals null, 1 and 1.0 are different values."""
import json

from tests.tck.steps.comparison import TCKResultTable, TCKValue, parse_expected


def _table(columns, rows, ordered=False, list_unordered=False):
    return TCKResultTable(
        columns=columns,
        rows=[[TCKValue.parse(c) for c in r] for r in rows],
        ordered=ordered,
        list_unordered=list_unordered,
    )


def _cmp(columns, expected_rows, actual_rows, actual_cols=None, hydrator=None, **kw):
    t = _table(columns, expected_rows, **kw)
    return t.compare(actual_rows, actual_cols or columns, hydrator=hydrator)


def _blob(nid, labels, props=None):
    return {
        "_id": nid,
        "_labels": labels + ["TCK_abcd1234"],
        "_props": [json.dumps({"key": k, "value": v}) for k, v in (props or {}).items()],
    }


def _node_cols(var, nid, labels, props=None):
    b = _blob(nid, labels, props)
    return {f"{var}_id": nid, f"{var}_labels": json.dumps(b["_labels"]), f"{var}_props": json.dumps(b["_props"])}


class FakeHydrator:
    """node id -> (labels, typed props); edges as (s, type, o, props)."""

    def __init__(self, nodes, edges=()):
        self.nodes = nodes
        self.edges_ = list(edges)

    def node(self, nid):
        if nid not in self.nodes:
            return None
        labels, props = self.nodes[nid]
        return {"labels": list(labels), "props": dict(props)}

    def edges(self, s, o, rel_type):
        return [dict(p) for (es, et, eo, p) in self.edges_ if es == s and eo == o and et == rel_type]


class TestParseExpected:
    def test_node_path_rel_are_structured(self):
        n = parse_expected("(:A:B {name: 'x'})")
        assert n.labels == frozenset({"A", "B"}) and n.props == {"name": "x"}
        r = parse_expected("[:T {w: 2}]")
        assert r.type == "T" and r.props == {"w": 2}
        p = parse_expected("<(:A)-[:T]->(:B)<-[:S]-()>")
        assert [x.labels for x in p.nodes] == [frozenset({"A"}), frozenset({"B"}), frozenset()]
        assert [(r.type, fwd) for r, fwd in p.rels] == [("T", True), ("S", False)]

    def test_quoted_pattern_is_a_string(self):
        assert parse_expected("'(:A)'") == "(:A)"

    def test_numbers_keep_their_type(self):
        assert type(parse_expected("1")) is int
        assert type(parse_expected("1.0")) is float
        assert type(parse_expected("[1, 1.0]")[1]) is float


class TestSubsetNodeFails:
    def test_extra_label_fails(self):
        row = _node_cols("n", "id1", ["A", "B"])
        assert _cmp(["n"], [["(:A)"]], [row], list(row)) is not None

    def test_extra_property_fails(self):
        row = _node_cols("n", "id1", ["A"], {"name": "x", "num": "1"})
        assert _cmp(["n"], [["(:A {name: 'x'})"]], [row], list(row)) is not None

    def test_exact_node_passes(self):
        row = _node_cols("n", "id1", ["A", "B"], {"name": "x"})
        assert _cmp(["n"], [["(:A:B {name: 'x'})"]], [row], list(row)) is None

    def test_hydrated_node_type_is_checked(self):
        row = _node_cols("n", "id1", ["A"], {"num": "1"})
        hyd = FakeHydrator({"id1": (["A"], {"num": 1.0})})
        assert _cmp(["n"], [["(:A {num: 1})"]], [row], list(row), hydrator=hyd) is not None
        hyd = FakeHydrator({"id1": (["A"], {"num": 1})})
        assert _cmp(["n"], [["(:A {num: 1})"]], [row], list(row), hydrator=hyd) is None

    def test_node_in_list_is_exact(self):
        cell = json.dumps([json.dumps(_blob("id1", ["A", "B"]))])
        assert _cmp(["l"], [["[(:A)]"]], [{"l": cell}]) is not None
        cell = json.dumps([json.dumps(_blob("id1", ["A"]))])
        assert _cmp(["l"], [["[(:A)]"]], [{"l": cell}]) is None


class TestRelationshipExact:
    def test_extra_rel_property_fails(self):
        v = json.dumps({"type": "T", "props": {"w": "2"}})
        assert _cmp(["r"], [["[:T]"]], [{"r": v}]) is not None

    def test_rel_exact_passes(self):
        v = json.dumps({"type": "T", "props": {"w": "2"}})
        assert _cmp(["r"], [["[:T {w: 2}]"]], [{"r": v}]) is None

    def test_rel_type_mismatch_fails(self):
        v = json.dumps({"type": "S", "props": {}})
        assert _cmp(["r"], [["[:T]"]], [{"r": v}]) is not None


class TestColumnsExact:
    def test_extra_column_fails(self):
        assert _cmp(["a"], [["1"]], [{"a": 1, "b": 2}], ["a", "b"]) is not None

    def test_missing_column_fails(self):
        assert _cmp(["a", "b"], [["1", "2"]], [{"a": 1}], ["a"]) is not None

    def test_column_order_fails(self):
        assert _cmp(["a", "b"], [["1", "2"]], [{"b": 2, "a": 1}], ["b", "a"]) is not None

    def test_column_name_is_exact(self):
        assert _cmp(["toInteger(x)"], [["1"]], [{"tointeger(x)": 1}], ["tointeger(x)"]) is not None

    def test_node_triplet_collapses_to_its_column(self):
        row = {"x": 1, **_node_cols("n", "id1", ["A"])}
        assert _cmp(["x", "n"], [["1", "(:A)"]], [row], list(row)) is None


class TestPathIdentity:
    def _path(self, nodes, rels):
        return json.dumps({"nodes": nodes, "rels": rels})

    def test_reversed_path_fails(self):
        hyd = FakeHydrator({"a": (["A"], {}), "b": (["B"], {})}, [("b", "T", "a", {})])
        v = self._path(["a", "b"], ["T"])
        assert _cmp(["p"], [["<(:A)-[:T]->(:B)>"]], [{"p": v}], hydrator=hyd) is not None
        assert _cmp(["p"], [["<(:A)<-[:T]-(:B)>"]], [{"p": v}], hydrator=hyd) is None

    def test_path_node_labels_checked(self):
        hyd = FakeHydrator({"a": (["A"], {}), "b": (["C"], {})}, [("a", "T", "b", {})])
        v = self._path(["a", "b"], ["T"])
        assert _cmp(["p"], [["<(:A)-[:T]->(:B)>"]], [{"p": v}], hydrator=hyd) is not None

    def test_path_node_props_checked(self):
        hyd = FakeHydrator({"a": (["A"], {"num": 1}), "b": (["B"], {})}, [("a", "T", "b", {})])
        v = self._path(["a", "b"], ["T"])
        assert _cmp(["p"], [["<(:A)-[:T]->(:B)>"]], [{"p": v}], hydrator=hyd) is not None
        assert _cmp(["p"], [["<(:A {num: 1})-[:T]->(:B)>"]], [{"p": v}], hydrator=hyd) is None

    def test_path_rel_props_checked(self):
        hyd = FakeHydrator({"a": ([], {}), "b": ([], {})}, [("a", "T", "b", {"w": "2"})])
        v = self._path(["a", "b"], ["T"])
        assert _cmp(["p"], [["<()-[:T]->()>"]], [{"p": v}], hydrator=hyd) is not None
        assert _cmp(["p"], [["<()-[:T {w: 2}]->()>"]], [{"p": v}], hydrator=hyd) is None

    def test_path_length_checked(self):
        hyd = FakeHydrator({"a": ([], {})})
        v = self._path(["a"], [])
        assert _cmp(["p"], [["<()-[:T]->()>"]], [{"p": v}], hydrator=hyd) is not None
        assert _cmp(["p"], [["<()>"]], [{"p": v}], hydrator=hyd) is None

    def test_path_without_hydrator_fails(self):
        v = self._path(["a", "b"], ["T"])
        assert _cmp(["p"], [["<()-[:T]->()>"]], [{"p": v}]) is not None


class TestNullIsOnlyNull:
    def test_none_vs_empty_list_fails(self):
        assert _cmp(["x"], [["[]"]], [{"x": None}]) is not None

    def test_empty_list_vs_null_fails(self):
        assert _cmp(["x"], [["null"]], [{"x": "[]"}]) is not None

    def test_none_vs_empty_string_fails(self):
        assert _cmp(["x"], [["''"]], [{"x": None}]) is not None

    def test_none_vs_zero_fails(self):
        assert _cmp(["x"], [["0"]], [{"x": None}]) is not None

    def test_null_equals_null(self):
        assert _cmp(["x"], [["null"]], [{"x": None}]) is None

    def test_null_inside_list(self):
        assert _cmp(["x"], [["[null]"]], [{"x": "[null]"}]) is None
        assert _cmp(["x"], [["[null]"]], [{"x": "[[]]"}]) is not None


class TestNumericTypes:
    def test_int_vs_float_fails(self):
        assert _cmp(["x"], [["1"]], [{"x": 1.0}]) is not None
        assert _cmp(["x"], [["1.0"]], [{"x": 1}]) is not None

    def test_int_float_in_list(self):
        assert _cmp(["x"], [["[1]"]], [{"x": "[1.0]"}]) is not None
        assert _cmp(["x"], [["[1.0]"]], [{"x": "[1]"}]) is not None
        assert _cmp(["x"], [["[1, 2.5]"]], [{"x": "[1, 2.5]"}]) is None

    def test_int_float_in_map(self):
        assert _cmp(["x"], [["{a: 1}"]], [{"x": '{"a": 1.0}'}]) is not None

    def test_numeric_text_keeps_type(self):
        assert _cmp(["x"], [["1"]], [{"x": "1"}]) is None
        assert _cmp(["x"], [["1.0"]], [{"x": "1"}]) is not None
        assert _cmp(["x"], [["1"]], [{"x": "1.0"}]) is not None

    def test_string_vs_number_fails(self):
        assert _cmp(["x"], [["'1'"]], [{"x": 1}]) is not None

    def test_bool_vs_int_fails(self):
        assert _cmp(["x"], [["true"]], [{"x": 1}]) is not None

    def test_truncating_float_fails(self):
        assert _cmp(["x"], [["1"]], [{"x": 1.5}]) is not None

    def test_nan_equals_nan(self):
        assert _cmp(["x"], [["NaN"]], [{"x": float("nan")}]) is None


class TestOrdering:
    def test_in_order_is_honoured(self):
        rows = [{"x": 2}, {"x": 1}]
        assert _cmp(["x"], [["1"], ["2"]], rows, ordered=True) is not None
        assert _cmp(["x"], [["1"], ["2"]], rows, ordered=False) is None

    def test_list_order_matters_unless_ignored(self):
        rows = [{"x": "[2, 1]"}]
        assert _cmp(["x"], [["[1, 2]"]], rows) is not None
        assert _cmp(["x"], [["[1, 2]"]], rows, list_unordered=True) is None

    def test_unordered_is_a_multiset(self):
        rows = [{"x": 1}, {"x": 1}, {"x": 2}]
        assert _cmp(["x"], [["1"], ["2"], ["2"]], rows) is not None
        assert _cmp(["x"], [["1"], ["2"], ["1"]], rows) is None

    def test_zero_rows_expected(self):
        assert _cmp(["x"], [], [{"x": 1}]) is not None
        assert _cmp(["x"], [], []) is None
