"""``IVG_TCK_CAPTURE=<dir>``: every scenario's actual result, recorded once so a
new scoring rule can be tried offline (``scripts/tck/rescore.py``) instead of a
fresh IRIS run. Capture must never raise and never change a verdict.
"""
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from tests.tck import capture


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("IVG_TCK_CAPTURE", raising=False)


class _Status:
    def __init__(self, name):
        self.name = name


class _Feature:
    def __init__(self, name, filename):
        self.name = name
        self.filename = filename


class _Scenario:
    def __init__(self, name, feature, status="passed"):
        self.name = name
        self.feature = feature
        self.status = _Status(status)


def _feature(area="create"):
    return _Feature("Create1", f"/repo/tests/tck/features/clauses/{area}/Create1.feature")


class TestFlagReading:
    def test_default_disabled(self):
        assert capture.capture_dir() is None
        assert capture.enabled() is False

    def test_enabled_when_set(self, monkeypatch, tmp_path):
        monkeypatch.setenv("IVG_TCK_CAPTURE", str(tmp_path))
        assert capture.capture_dir() == str(tmp_path)
        assert capture.enabled() is True


class TestNoteIsANoOpWhenDisabled:
    def test_note_does_not_touch_context(self):
        ctx = SimpleNamespace()
        capture.note(ctx, foo="bar")
        assert not hasattr(ctx, "_capture_fields")

    def test_note_query_does_not_set_last_query(self):
        ctx = SimpleNamespace()
        capture.note_query(ctx, "MATCH (n) RETURN n")
        assert not hasattr(ctx, "last_query")

    def test_flush_writes_nothing(self, tmp_path):
        ctx = SimpleNamespace(last_result=None, last_error=None)
        capture.flush(ctx, _Scenario("s", _feature()))
        assert list(tmp_path.iterdir()) == []


class TestNoteWhenEnabled:
    def test_note_merges_fields(self, monkeypatch, tmp_path):
        monkeypatch.setenv("IVG_TCK_CAPTURE", str(tmp_path))
        ctx = SimpleNamespace()
        capture.note(ctx, a=1)
        capture.note(ctx, b=2)
        assert ctx._capture_fields == {"a": 1, "b": 2}

    def test_note_query_sets_last_query(self, monkeypatch, tmp_path):
        monkeypatch.setenv("IVG_TCK_CAPTURE", str(tmp_path))
        ctx = SimpleNamespace()
        capture.note_query(ctx, "MATCH (n) RETURN n")
        assert ctx.last_query == "MATCH (n) RETURN n"


class TestTagUntagRoundTrip:
    @pytest.mark.parametrize("v", [1, 1.5, True, False, None, "x", "1", Decimal("2.50")])
    def test_scalar_round_trip(self, v):
        got = capture.untag_scalar(capture.tag(v))
        if isinstance(v, Decimal):
            assert got == v
        else:
            assert got == v and type(got) is type(v)

    def test_list_round_trips_with_types_preserved(self):
        v = [1, "1", True, 1.5, None]
        tagged = capture.tag(v)
        assert tagged["t"] == "list"
        got = capture.untag(tagged)
        assert got == v
        assert [type(x) for x in got] == [type(x) for x in v]

    def test_map_round_trips(self):
        v = {"a": 1, "b": "x", "c": [1, 2]}
        got = capture.untag(capture.tag(v))
        assert got == v

    def test_json_serializable(self):
        tagged = capture.tag({"a": [1, "1", Decimal("3")], "b": None})
        json.dumps(tagged)  # must not raise


class TestFlushAssemblesARecord:
    def _write(self, tmp_path, ctx, scenario, monkeypatch):
        monkeypatch.setenv("IVG_TCK_CAPTURE", str(tmp_path))
        capture.flush(ctx, scenario)
        path = tmp_path / f"{capture._area(scenario)}.jsonl"
        lines = path.read_text().splitlines()
        assert len(lines) == 1
        return json.loads(lines[0])

    def test_basic_fields(self, monkeypatch, tmp_path):
        ctx = SimpleNamespace(
            last_result=None, last_error=None, last_query="MATCH (n) RETURN n",
            side_effects={"+nodes": 1}, side_effects_unexpected={},
        )
        rec = self._write(tmp_path, ctx, _Scenario("Create a node", _feature()), monkeypatch)
        assert rec["scenario"] == "Create1::Create a node"
        assert rec["area"] == "create"
        assert rec["query"] == "MATCH (n) RETURN n"
        assert rec["side_effects"] == {"+nodes": 1}
        assert rec["error"] is None
        assert rec["verdict"] is True

    def test_captures_noted_fields(self, monkeypatch, tmp_path):
        monkeypatch.setenv("IVG_TCK_CAPTURE", str(tmp_path))
        ctx = SimpleNamespace(last_result=None, last_error=None)
        capture.note(ctx, expected_result={"empty": True}, actual_columns=["n"])
        rec = self._write(tmp_path, ctx, _Scenario("s", _feature()), monkeypatch)
        assert rec["expected_result"] == {"empty": True}
        assert rec["actual_columns"] == ["n"]

    def test_error_classified(self, monkeypatch, tmp_path):
        ctx = SimpleNamespace(last_result=None, last_error=RuntimeError("SQLCODE: <-23> [Location: <Prepare>]"))
        rec = self._write(tmp_path, ctx, _Scenario("s", _feature()), monkeypatch)
        assert rec["error"]["class"] == "RuntimeError"
        assert "-23" in rec["error"]["message"]

    def test_failing_scenario_verdict_false(self, monkeypatch, tmp_path):
        ctx = SimpleNamespace(last_result=None, last_error=None)
        rec = self._write(tmp_path, ctx, _Scenario("s", _feature(), status="failed"), monkeypatch)
        assert rec["verdict"] is False

    def test_flush_clears_capture_fields(self, monkeypatch, tmp_path):
        monkeypatch.setenv("IVG_TCK_CAPTURE", str(tmp_path))
        ctx = SimpleNamespace(last_result=None, last_error=None)
        capture.note(ctx, x=1)
        capture.flush(ctx, _Scenario("s", _feature()))
        assert not hasattr(ctx, "_capture_fields")

    def test_flush_appends_multiple_scenarios(self, monkeypatch, tmp_path):
        monkeypatch.setenv("IVG_TCK_CAPTURE", str(tmp_path))
        ctx = SimpleNamespace(last_result=None, last_error=None)
        capture.flush(ctx, _Scenario("s1", _feature()))
        capture.flush(ctx, _Scenario("s2", _feature()))
        path = tmp_path / "create.jsonl"
        assert len(path.read_text().splitlines()) == 2

    def test_flush_never_raises_on_bad_scenario(self, monkeypatch, tmp_path):
        monkeypatch.setenv("IVG_TCK_CAPTURE", str(tmp_path))
        ctx = SimpleNamespace(last_result=None, last_error=None)
        capture.flush(ctx, object())  # no .name / .feature / .status at all

    def test_flush_never_raises_when_dir_uncreatable(self, monkeypatch):
        monkeypatch.setenv("IVG_TCK_CAPTURE", "/nonexistent-root/definitely-not-writable")
        ctx = SimpleNamespace(last_result=None, last_error=None)
        capture.flush(ctx, _Scenario("s", _feature()))  # must not raise


class TestGroupedColumns:
    def test_node_columns_collapse_to_the_tck_name(self):
        assert capture.grouped_columns(["a_id", "a_labels", "a_props", "b_id", "b_labels", "b_props"]) == ["a", "b"]

    def test_rel_columns_collapse_to_the_tck_name(self):
        assert capture.grouped_columns(["r_s", "r_p", "r_o_id"]) == ["r"]

    def test_plain_columns_pass_through(self):
        assert capture.grouped_columns(["n", "m"]) == ["n", "m"]


class TestSerializeActualRows:
    def test_plain_scalars_tagged(self):
        rows = capture.serialize_actual_rows([{"n": 1}, {"n": "x"}], ["n"])
        assert rows == [[{"t": "int", "v": 1}], [{"t": "str", "v": "x"}]]

    def test_node_columns_grouped_and_labeled(self):
        actual_row = {
            "p_id": "n1",
            "p_labels": json.dumps(["Person", "TCK_abcd1234"]),
            "p_props": json.dumps([{"key": "name", "value": "Alice"}]),
        }
        rows = capture.serialize_actual_rows([actual_row], ["p_id", "p_labels", "p_props"])
        assert len(rows) == 1 and len(rows[0]) == 1
        cell = rows[0][0]
        assert cell["t"] == "node"
        assert cell["labels"] == ["Person"]  # TCK_* isolation label dropped
        assert cell["props"] == {"name": {"t": "str", "v": "Alice"}}

    def test_null_node_column_group_is_null(self):
        actual_row = {"p_id": None, "p_labels": None, "p_props": None}
        rows = capture.serialize_actual_rows([actual_row], ["p_id", "p_labels", "p_props"])
        assert rows == [[{"t": "null", "v": None}]]

    def test_plain_json_list_column_kept_as_jsontext(self):
        """IVG returns a list-valued result as JSON text; capture keeps the raw
        text and the decoded list, since it does not yet know whether the
        expected value is a list or (rarely) that literal string."""
        rows = capture.serialize_actual_rows([{"l": "[1, 2]"}], ["l"])
        cell = rows[0][0]
        assert cell["t"] == "jsontext"
        assert cell["raw"] == "[1, 2]"
        assert cell["decoded"] == {"t": "list", "v": [{"t": "int", "v": 1}, {"t": "int", "v": 2}]}

    def test_plain_json_map_column_kept_as_jsontext(self):
        rows = capture.serialize_actual_rows([{"m": '{"a": 1}'}], ["m"])
        cell = rows[0][0]
        assert cell["t"] == "jsontext"
        assert cell["decoded"] == {"t": "map", "v": {"a": {"t": "int", "v": 1}}}

    def test_plain_string_that_is_not_json_stays_str(self):
        rows = capture.serialize_actual_rows([{"s": "hello"}], ["s"])
        assert rows == [[{"t": "str", "v": "hello"}]]

    def test_rel_returned_as_one_json_column(self):
        """A relationship value that came back as a single {"type","props"}
        JSON column, not the split r_s/r_p/r_o_id group."""
        actual_row = {"r": json.dumps({"type": "KNOWS", "props": {"since": 2020}})}
        rows = capture.serialize_actual_rows([actual_row], ["r"])
        assert rows == [[{"t": "rel", "type": "KNOWS", "props": {"since": {"t": "int", "v": 2020}}}]]

    def test_plain_map_with_a_type_key_is_a_map(self):
        """A literal map that merely has a ``type`` key (Literals7/8 [18]'s
        donut map) is a map, not a relationship."""
        actual_row = {"m": json.dumps({"id": 1, "type": "donut", "name": "Glazed"})}
        rows = capture.serialize_actual_rows([actual_row], ["m"])
        assert rows[0][0]["t"] == "jsontext"
        assert rows[0][0]["decoded"]["t"] == "map"
        assert rows[0][0]["decoded"]["v"]["type"] == {"t": "str", "v": "donut"}

    def test_id_and_type_only_map_is_a_map(self):
        """``{"id", "type"}`` (Literals7/8 [18]'s nested batter maps) has no
        ``props``, so it is a map too."""
        actual_row = {"m": json.dumps({"id": "1001", "type": "Regular"})}
        rows = capture.serialize_actual_rows([actual_row], ["m"])
        assert rows[0][0]["decoded"]["t"] == "map"

    def test_node_property_holding_a_json_list_is_jsontext(self):
        actual_row = {
            "p_id": "n1",
            "p_labels": json.dumps(["Person"]),
            "p_props": json.dumps([{"key": "scores", "value": "[1, 2, 3]"}]),
        }
        rows = capture.serialize_actual_rows([actual_row], ["p_id", "p_labels", "p_props"])
        cell = rows[0][0]
        assert cell["props"]["scores"]["t"] == "jsontext"
        assert cell["props"]["scores"]["decoded"]["t"] == "list"


class TestTagMaybeJson:
    def test_non_json_string_tagged_plainly(self):
        assert capture.tag_maybe_json("hello") == {"t": "str", "v": "hello"}

    def test_json_list_text_becomes_jsontext(self):
        got = capture.tag_maybe_json("[1, 2]")
        assert got["t"] == "jsontext" and got["raw"] == "[1, 2]"
        assert got["decoded"] == capture.tag([1, 2])

    def test_json_map_text_becomes_jsontext(self):
        got = capture.tag_maybe_json('{"a": 1}')
        assert got["t"] == "jsontext"
        assert got["decoded"] == capture.tag({"a": 1})

    def test_non_string_values_pass_through_to_tag(self):
        assert capture.tag_maybe_json(1) == capture.tag(1)
        assert capture.tag_maybe_json(None) == capture.tag(None)


class TestSerializeExpectedTable:
    def test_raw_cell_strings_and_flags(self):
        from tests.tck.steps.comparison import TCKValue

        row = [TCKValue.parse("1"), TCKValue.parse("'x'")]
        table = capture.serialize_expected_table(["n", "s"], [row], ordered=True, list_unordered=False)
        assert table == {
            "columns": ["n", "s"],
            "rows": [["1", "'x'"]],
            "ordered": True,
            "list_unordered": False,
        }
