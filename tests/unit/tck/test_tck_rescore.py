"""scripts/tck/rescore.py: rescore a TCK capture offline, with no IRIS.

Built on tiny hand-made capture records (the shape ``tests/tck/capture.py``
writes), not a live run.
"""
import json


from scripts.tck import rescore as R
from tests.tck.capture import tag


def _int_cell(v):
    return tag(v)


def _str_cell(v):
    return tag(v)


def _table_rec(scenario, exp_cols, exp_rows_raw, actual_rows, ordered=True,
               list_unordered=False, error=None, verdict=True, area="create"):
    return {
        "scenario": scenario,
        "area": area,
        "expected_result": {
            "columns": exp_cols,
            "rows": exp_rows_raw,
            "ordered": ordered,
            "list_unordered": list_unordered,
        },
        "actual_columns": exp_cols,
        "actual_rows": actual_rows,
        "side_effects": None,
        "side_effects_unexpected": {},
        "expected_side_effects": None,
        "expected_error": None,
        "error": error,
        "verdict": verdict,
    }


class TestLoadRecords:
    def test_reads_every_jsonl_file(self, tmp_path):
        (tmp_path / "create.jsonl").write_text(
            json.dumps({"scenario": "a", "verdict": True}) + "\n"
        )
        (tmp_path / "delete.jsonl").write_text(
            json.dumps({"scenario": "b", "verdict": False}) + "\n"
        )
        recs = R.load_records(str(tmp_path))
        assert {r["scenario"] for r in recs} == {"a", "b"}

    def test_ignores_blank_lines(self, tmp_path):
        (tmp_path / "create.jsonl").write_text(
            json.dumps({"scenario": "a", "verdict": True}) + "\n\n"
        )
        recs = R.load_records(str(tmp_path))
        assert len(recs) == 1


class TestScalarLeniencyByMode:
    def test_int_text_matches_under_default(self):
        rec = _table_rec("S::int-as-text", ["n"], [["1"]], [[_str_cell("1")]])
        assert R.score_scenario(rec, "default").passed is True

    def test_int_text_fails_under_typed(self):
        rec = _table_rec("S::int-as-text", ["n"], [["1"]], [[_str_cell("1")]])
        v = R.score_scenario(rec, "typed")
        assert v.passed is False
        assert v.tag == "int->str"

    def test_native_int_passes_under_typed(self):
        rec = _table_rec("S::int-native", ["n"], [["1"]], [[_int_cell(1)]])
        assert R.score_scenario(rec, "typed").passed is True

    def test_bool_text_fails_under_typed(self):
        rec = _table_rec("S::bool-as-text", ["b"], [["true"]], [[tag("true")]])
        v = R.score_scenario(rec, "typed")
        assert v.passed is False and v.tag == "bool->str"

    def test_str_column_unaffected_by_typed(self):
        rec = _table_rec("S::str", ["s"], [["'x'"]], [[tag("x")]])
        assert R.score_scenario(rec, "typed").passed is True


class TestListsAndMapsAlreadyTyped:
    def test_list_of_ints_from_text_members_fails_regardless_of_mode(self):
        # Inner list members were never text-lenient (text_ok=False inside a list),
        # so a text member fails under every mode -- this is not new leniency.
        rec = _table_rec("S::list", ["l"], [["[1, 2]"]], [[tag([1, "2"])]])
        assert R.score_scenario(rec, "default").passed is False
        assert R.score_scenario(rec, "typed").passed is False

    def test_list_of_native_ints_passes(self):
        rec = _table_rec("S::list-ok", ["l"], [["[1, 2]"]], [[tag([1, 2])]])
        assert R.score_scenario(rec, "default").passed is True
        assert R.score_scenario(rec, "typed").passed is True

    def test_jsontext_list_matches_a_list_expectation(self):
        """The real capture shape: IVG returns a list as JSON text, so capture
        keeps it as `jsontext` (raw + decoded) -- the rescorer must unwrap it
        to `decoded` because `ev` is a list."""
        from tests.tck.capture import tag_maybe_json

        rec = _table_rec("S::list-jsontext", ["l"], [["[1, 2]"]], [[tag_maybe_json("[1, 2]")]])
        assert R.score_scenario(rec, "default").passed is True
        assert R.score_scenario(rec, "typed").passed is True

    def test_jsontext_map_matches_a_map_expectation(self):
        from tests.tck.capture import tag_maybe_json

        rec = _table_rec("S::map-jsontext", ["m"], [["{a: 1}"]], [[tag_maybe_json('{"a": 1}')]])
        assert R.score_scenario(rec, "default").passed is True

    def test_jsontext_falls_back_to_raw_text_for_a_string_expectation(self):
        """When `ev` is a plain string (not list/dict), the rescorer compares
        against the original text, not the decoded structure."""
        from tests.tck.capture import tag_maybe_json

        rec = _table_rec("S::str-that-looks-like-json", ["s"], [["'[1, 2]'"]], [[tag_maybe_json("[1, 2]")]])
        assert R.score_scenario(rec, "default").passed is True


class TestNodeMatching:
    def test_node_labels_and_props_match(self):
        actual = {"t": "node", "labels": ["Person"], "props": {"name": tag("Alice")}}
        rec = _table_rec("S::node", ["p"], [["(:Person {name: 'Alice'})"]], [[actual]])
        assert R.score_scenario(rec, "default").passed is True

    def test_node_prop_typing_is_unaffected_by_typed_mode(self):
        """Node/relationship property values compare through stored()-style
        leniency (storage rendering), which `typed` deliberately leaves alone --
        only the outermost result cell tightens under that mode."""
        actual = {"t": "node", "labels": ["Person"], "props": {"age": tag("30")}}
        rec = _table_rec("S::node-age", ["p"], [["(:Person {age: 30})"]], [[actual]])
        assert R.score_scenario(rec, "default").passed is True
        assert R.score_scenario(rec, "typed").passed is True

    def test_wrong_labels_fail_every_mode(self):
        actual = {"t": "node", "labels": ["Other"], "props": {}}
        rec = _table_rec("S::node-wrong-label", ["p"], [["(:Person)"]], [[actual]])
        assert R.score_scenario(rec, "default").passed is False


class TestEmptyResultAndErrors:
    def test_empty_result_zero_rows_passes(self):
        rec = _table_rec("S::empty", [], [], [])
        rec["expected_result"] = {"empty": True}
        rec["actual_rows"] = []
        assert R.score_scenario(rec, "default").passed is True

    def test_empty_result_with_error_fails_default(self):
        rec = _table_rec("S::empty-err", [], [], [])
        rec["expected_result"] = {"empty": True}
        rec["error"] = {"class": "RuntimeError", "message": "boom", "kind": None, "phase": "runtime"}
        assert R.score_scenario(rec, "default").passed is False
        assert R.score_scenario(rec, "typed").passed is False

    def test_empty_result_with_error_passes_lenient(self):
        rec = _table_rec("S::empty-err-lenient", [], [], [])
        rec["expected_result"] = {"empty": True}
        rec["error"] = {"class": "RuntimeError", "message": "boom", "kind": None, "phase": "runtime"}
        assert R.score_scenario(rec, "lenient").passed is True


class TestSideEffects:
    def test_matching_delta_passes(self):
        rec = _table_rec("S::se", [], [], [])
        rec["expected_result"] = None
        rec["expected_side_effects"] = {"+nodes": 1}
        rec["side_effects"] = {"+nodes": 1, "-nodes": 0, "+relationships": 0, "-relationships": 0,
                                "+labels": 0, "-labels": 0, "+properties": 0, "-properties": 0}
        assert R.score_scenario(rec, "default").passed is True

    def test_mismatched_delta_fails(self):
        rec = _table_rec("S::se-bad", [], [], [])
        rec["expected_result"] = None
        rec["expected_side_effects"] = {"+nodes": 1}
        rec["side_effects"] = {"+nodes": 0, "-nodes": 0, "+relationships": 0, "-relationships": 0,
                                "+labels": 0, "-labels": 0, "+properties": 0, "-properties": 0}
        v = R.score_scenario(rec, "default")
        assert v.passed is False and v.tag == "side_effects"

    def test_lenient_always_passes_side_effects(self):
        rec = _table_rec("S::se-lenient", [], [], [])
        rec["expected_result"] = None
        rec["expected_side_effects"] = {"+nodes": 1}
        rec["side_effects"] = {"+nodes": 0}
        assert R.score_scenario(rec, "lenient").passed is True


class TestErrorKind:
    def test_matching_kind_and_phase_passes(self):
        rec = _table_rec("S::err-ok", [], [], [])
        rec["expected_result"] = None
        rec["expected_error"] = {"kind": "SyntaxError", "phase": "compile time", "detail": "*"}
        rec["error"] = {"class": "CypherParseError", "message": "bad", "kind": "SyntaxError",
                         "phase": "compile time", "detail": None}
        assert R.score_scenario(rec, "default").passed is True

    def test_wrong_kind_fails(self):
        rec = _table_rec("S::err-bad", [], [], [])
        rec["expected_result"] = None
        rec["expected_error"] = {"kind": "ConstraintVerificationFailed", "phase": "runtime", "detail": "*"}
        rec["error"] = {"class": "RuntimeError", "message": "prep fail", "kind": None,
                         "phase": "compile time", "detail": None}
        v = R.score_scenario(rec, "default")
        assert v.passed is False and v.tag == "wrong_error_kind"

    def test_no_error_raised_fails(self):
        rec = _table_rec("S::err-missing", [], [], [])
        rec["expected_result"] = None
        rec["expected_error"] = {"kind": "TypeError", "phase": "runtime", "detail": "*"}
        rec["error"] = None
        v = R.score_scenario(rec, "default")
        assert v.passed is False and v.tag == "error_not_raised"

    def test_error_kind_unaffected_by_lenient_mode(self):
        """Spec 229 US3: error-kind matching is unconditional in the shipped
        harness -- IVG_TCK_LENIENT=1 does not loosen it, so neither does this mode."""
        rec = _table_rec("S::err-lenient", [], [], [])
        rec["expected_result"] = None
        rec["expected_error"] = {"kind": "ConstraintVerificationFailed", "phase": "runtime", "detail": "*"}
        rec["error"] = {"class": "RuntimeError", "message": "prep fail", "kind": None,
                         "phase": "compile time", "detail": None}
        assert R.score_scenario(rec, "lenient").passed is False


class TestRescoreReport:
    def test_default_mode_reproduces_recorded_verdicts(self):
        """The sanity check the task asks for: rescoring in default mode must
        agree with what was recorded, or the capture/rescore pipeline is wrong."""
        recs = [
            _table_rec("S::ok", ["n"], [["1"]], [[_int_cell(1)]], verdict=True),
            _table_rec("S::also-ok", ["s"], [["'x'"]], [[tag("x")]], verdict=True),
        ]
        report = R.rescore(recs, "default")
        assert report.passed == 2
        assert report.mismatches == {}

    def test_typed_mode_reports_mismatch_grouped_by_type_pair(self):
        recs = [
            _table_rec("S::int-as-text", ["n"], [["1"]], [[_str_cell("1")]], verdict=True),
            _table_rec("S::also-int-as-text", ["n"], [["2"]], [[_str_cell("2")]], verdict=True),
            _table_rec("S::fine", ["n"], [["1"]], [[_int_cell(1)]], verdict=True),
        ]
        report = R.rescore(recs, "typed")
        assert report.passed == 1  # only "fine"
        assert set(report.mismatches) == {"int->str"}
        assert sorted(report.mismatches["int->str"]) == ["S::also-int-as-text", "S::int-as-text"]

    def test_a_scenario_recorded_as_failing_and_still_failing_is_not_a_mismatch(self):
        rec = _table_rec("S::already-failing", ["n"], [["1"]], [[tag(2)]], verdict=False)
        report = R.rescore([rec], "default")
        assert report.mismatches == {}

    def test_untested_scenario_excluded_not_scored(self):
        """The one upstream-@ignore scenario (Graph5 [2]): environment.py skips
        it before any step, so nothing was captured -- summarize.py excludes it
        from both the numerator and denominator, and so must rescore."""
        rec = {
            "scenario": "Graph5 - Node and edge label expressions::[2] Single-labels expression on relationships",
            "area": "graph", "query": None, "side_effects": None,
            "side_effects_unexpected": {}, "error": None, "verdict": False,
        }
        good = _table_rec("S::ok", ["n"], [["1"]], [[_int_cell(1)]], verdict=True)
        report = R.rescore([rec, good], "default")
        assert report.total == 1  # the untested scenario is not eligible
        assert report.passed == 1
        assert report.excluded == [rec["scenario"]]
        assert report.mismatches == {}


class TestIsUntested:
    def test_true_when_nothing_was_captured(self):
        assert R.is_untested({"query": None, "expected_result": None,
                               "expected_side_effects": None, "expected_error": None}) is True

    def test_false_when_a_query_ran(self):
        rec = _table_rec("S::ok", ["n"], [["1"]], [[tag(1)]])
        assert R.is_untested(rec) is False

    def test_false_when_only_a_side_effects_assertion_ran(self):
        assert R.is_untested({"query": None, "expected_result": None,
                               "expected_side_effects": {}, "expected_error": None}) is False


class TestCLI:
    def test_main_prints_score_and_exits_zero(self, tmp_path, capsys):
        rec = _table_rec("S::ok", ["n"], [["1"]], [[_int_cell(1)]], verdict=True)
        (tmp_path / "create.jsonl").write_text(json.dumps(rec) + "\n")
        rc = R.main([str(tmp_path), "--mode", "default"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "default: 1 / 1" in out

    def test_main_fails_on_empty_dir(self, tmp_path):
        assert R.main([str(tmp_path)]) == 1
