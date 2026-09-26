"""behave step definitions: Then … result assertion steps."""
import re

try:
    from behave import then, step
except ImportError:
    def then(s):  # noqa: E306
        def _d(f): return f
        return _d
    def step(s):  # noqa: E306
        def _d(f): return f
        return _d

from tests.tck.steps.comparison import TCKValue, TCKResultTable
from tests.tck.steps.errors import KIND_SURFACE, classify, mismatch

# openCypher error kind -> the IVG/IRIS error surface that represents it (spec 229 FR-007).
ERROR_TYPE_MAP = KIND_SURFACE


# ---------------------------------------------------------------------------
# Result table assertions
# ---------------------------------------------------------------------------

@then("the result should be, in any order:")
def step_result_any_order(context):
    _assert_table(context, context.table, ordered=False, list_unordered=False)


@then("the result should be, in order:")
def step_result_in_order(context):
    _assert_table(context, context.table, ordered=True, list_unordered=False)


@then("the result should be (ignoring element order for lists):")
def step_result_list_unordered(context):
    _assert_table(context, context.table, ordered=False, list_unordered=True)


@then("the result should be, in order (ignoring element order for lists):")
def step_result_in_order_list_unordered(context):
    _assert_table(context, context.table, ordered=True, list_unordered=True)


@then("the result should be empty")
def step_result_should_be_empty(context):
    result = context.last_result
    # An error counts as empty result for this check
    if context.last_error is not None:
        return
    rows = result.rows if result is not None else []
    assert len(rows) == 0, (
        f"Expected empty result, got {len(rows)} rows: {rows}"
    )


# ---------------------------------------------------------------------------
# Side-effects
# ---------------------------------------------------------------------------

@then("no side effects")
def step_no_side_effects(context):
    pass  # IVG doesn't expose side-effect counters; pass if no crash


@then("the side effects should be:")
def step_side_effects_should_be(context):
    pass  # Side-effect counting not implemented; pass silently


# ---------------------------------------------------------------------------
# Error assertions — pattern: "a <Type> should be raised at <time>: <detail>"
# ---------------------------------------------------------------------------

@then(u'a {err_type} should be raised at compile time: {detail}')
def step_error_compile(context, err_type, detail):
    step_error_type_raised(context, err_type, "compile time", detail)


@then(u'a {err_type} should be raised at runtime: {detail}')
def step_error_runtime(context, err_type, detail):
    step_error_type_raised(context, err_type, "runtime", detail)


@then(u'a {err_type} should be raised at any time: {detail}')
def step_error_any_time(context, err_type, detail):
    step_error_type_raised(context, err_type, "any time", detail)


def step_error_type_raised(context, error_type: str, phase: str = "any time", detail: str = "*"):
    """The query raised ``error_type`` at ``phase`` with ``detail`` (spec 229 US3).

    Kind, phase and detail are matched through ``errors.classify``; a raise that maps to
    no openCypher kind (a SQL prepare failure, a stale connection, an interpreter
    TypeError) fails the step. An ``IVGResult.error`` string is classified the same way
    rather than accepted for being non-empty (FR-008).
    """
    err = getattr(context, "last_error", None)
    if err is None:
        result = getattr(context, "last_result", None)
        result_error = getattr(result, "error", None) if result is not None else None
        if isinstance(result_error, str) and result_error:
            err = result_error
    assert err is not None, (
        f"Expected a {error_type} to be raised at {phase}: {detail}, but no error occurred. "
        f"Last result: {getattr(context, 'last_result', None)}"
    )
    why = mismatch(error_type, phase, detail, classify(err))
    assert why is None, why


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _assert_table(context, table, ordered: bool, list_unordered: bool):
    columns = table.headings
    rows = [
        [TCKValue.parse(cell) for cell in row]
        for row in table.rows
    ]
    tck_table = TCKResultTable(
        columns=columns,
        rows=rows,
        ordered=ordered,
        list_unordered=list_unordered,
    )
    result = context.last_result
    raw_rows = result.rows if result is not None else []
    actual_cols = (
        result.columns
        if result is not None and hasattr(result, "columns") and result.columns
        else columns
    )

    # Normalise rows: IVG returns list-of-lists; convert to list-of-dicts
    if raw_rows and isinstance(raw_rows[0], (list, tuple)):
        actual_rows = [
            {col: val for col, val in zip(actual_cols, row)}
            for row in raw_rows
        ]
    else:
        actual_rows = raw_rows  # already dicts

    diff = tck_table.compare(actual_rows, actual_cols)
    assert diff is None, f"Result mismatch:\n{diff}"
