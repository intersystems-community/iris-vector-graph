"""Unit tests for results step definitions."""
from unittest.mock import MagicMock
import pytest


def _make_ctx(rows, columns=None):
    ctx = MagicMock()
    result = MagicMock()
    result.rows = rows
    result.columns = columns or list(rows[0].keys()) if rows else []
    ctx.last_result = result
    ctx.last_error = None
    return ctx


class TestResultSteps:
    def test_result_should_be_match(self):
        from tests.tck.steps.results import _assert_table

        ctx = _make_ctx([{"name": "Alice"}])
        table = MagicMock()
        table.headings = ["name"]
        row = MagicMock()
        row.__iter__ = MagicMock(return_value=iter(["'Alice'"]))
        table.rows = [row]

        # should not raise
        _assert_table(ctx, table, ordered=False, list_unordered=False)

    def test_result_should_be_empty(self):
        from tests.tck.steps.results import step_result_should_be_empty

        ctx = _make_ctx([])
        step_result_should_be_empty(ctx)  # should not raise

    def test_result_should_be_empty_fails_when_not_empty(self):
        from tests.tck.steps.results import step_result_should_be_empty

        ctx = _make_ctx([{"n": 1}])
        with pytest.raises(AssertionError):
            step_result_should_be_empty(ctx)

    def test_no_side_effects_passes(self):
        from tests.tck.steps.results import step_no_side_effects

        from tests.tck.side_effects import SIDE_EFFECT_COLUMNS

        ctx = MagicMock()
        ctx.last_error = None
        ctx.last_result.error = None
        # a measured zero delta (spec 229: the step no longer passes unconditionally)
        ctx.side_effects = {c: 0 for c in SIDE_EFFECT_COLUMNS}
        ctx.side_effects_unexpected = {}
        step_no_side_effects(ctx)

    def test_error_type_assertion_pass(self):
        from tests.tck.steps.results import step_error_type_raised

        from iris_vector_graph.cypher.parser import parse_query
        from iris_vector_graph.cypher.translator import translate_to_sql

        ctx = MagicMock()
        # A TypeError IVG itself raised; a bare TypeError("bad type") no longer
        # passes, because it carries no evidence IVG raised it (spec 229 US3).
        with pytest.raises(TypeError) as info:
            translate_to_sql(parse_query("RETURN labels(1)"), {}, engine=None)
        ctx.last_error = info.value
        # should not raise
        step_error_type_raised(ctx, "TypeError")

    def test_error_type_assertion_fail_wrong_type(self):
        from tests.tck.steps.results import step_error_type_raised

        ctx = MagicMock()
        ctx.last_error = ValueError("not a type error")
        with pytest.raises(AssertionError):
            step_error_type_raised(ctx, "TypeError")

    def test_error_type_assertion_fail_no_error(self):
        from tests.tck.steps.results import step_error_type_raised

        ctx = MagicMock()
        ctx.last_error = None
        with pytest.raises(AssertionError):
            step_error_type_raised(ctx, "TypeError")
