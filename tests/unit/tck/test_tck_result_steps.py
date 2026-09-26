"""Spec 229 Phases 3-4: a raising query fails its scenario; side effects are counted.

Contexts are plain namespaces, not MagicMocks: a MagicMock answers every attribute,
which is exactly how a step that should fail on a missing snapshot could pass.
"""
from types import SimpleNamespace

import pytest

from tests.tck.side_effects import SIDE_EFFECT_COLUMNS

ZERO = {c: 0 for c in SIDE_EFFECT_COLUMNS}


class _Table:
    def __init__(self, headings, rows=()):
        self.headings = list(headings)
        self.rows = [list(r) for r in rows]


def _ctx(rows=None, columns=None, error=None, result_error=None, delta=None, unexpected=None):
    result = None
    if rows is not None:
        result = SimpleNamespace(rows=rows, columns=columns or [], error=result_error)
    return SimpleNamespace(
        last_result=result,
        last_error=error,
        side_effects=delta,
        side_effects_unexpected=unexpected or {},
        side_effects_error=None,
    )


@pytest.fixture(autouse=True)
def _strict(monkeypatch):
    monkeypatch.delenv("IVG_TCK_LENIENT", raising=False)


# ---------------------------------------------------------------------------
# Phase 3 (US2): a raising query fails its scenario
# ---------------------------------------------------------------------------


class TestEmptyResultStep:
    def test_raised_error_fails_and_carries_the_text(self):
        from tests.tck.steps.results import step_result_should_be_empty

        ctx = _ctx(error=RuntimeError("SQLCODE -23 Label 'N0' is not listed"))
        with pytest.raises(AssertionError) as ei:
            step_result_should_be_empty(ctx)
        assert "RuntimeError" in str(ei.value) and "SQLCODE -23" in str(ei.value)

    def test_error_carried_on_the_result_fails(self):
        from tests.tck.steps.results import step_result_should_be_empty

        ctx = _ctx(rows=[], result_error="gds procedure 'gds.x' not shimmed")
        with pytest.raises(AssertionError, match="not shimmed"):
            step_result_should_be_empty(ctx)

    def test_zero_rows_passes(self):
        from tests.tck.steps.results import step_result_should_be_empty

        step_result_should_be_empty(_ctx(rows=[]))

    def test_rows_fail(self):
        from tests.tck.steps.results import step_result_should_be_empty

        with pytest.raises(AssertionError):
            step_result_should_be_empty(_ctx(rows=[[1]], columns=["n"]))

    def test_lenient_mode_keeps_the_old_early_return(self, monkeypatch):
        from tests.tck.steps.results import step_result_should_be_empty

        monkeypatch.setenv("IVG_TCK_LENIENT", "1")
        step_result_should_be_empty(_ctx(error=RuntimeError("boom")))


class TestResultTableSteps:
    @pytest.mark.parametrize(
        "step_name",
        [
            "step_result_any_order",
            "step_result_in_order",
            "step_result_list_unordered",
            "step_result_in_order_list_unordered",
        ],
    )
    def test_every_table_step_fails_on_a_raised_error(self, step_name):
        import tests.tck.steps.results as results

        ctx = _ctx(error=ValueError("prepare failed"))
        # expected `null` would match an absent column of an absent result
        ctx.table = _Table(["x"], [["null"]])
        with pytest.raises(AssertionError) as ei:
            getattr(results, step_name)(ctx)
        assert "ValueError" in str(ei.value) and "prepare failed" in str(ei.value)

    def test_table_step_fails_on_result_error(self):
        from tests.tck.steps.results import step_result_any_order

        ctx = _ctx(rows=[], result_error="not shimmed")
        ctx.table = _Table(["x"], [])
        with pytest.raises(AssertionError, match="not shimmed"):
            step_result_any_order(ctx)

    def test_table_step_fails_when_no_query_ran(self):
        from tests.tck.steps.results import step_result_any_order

        ctx = _ctx()
        ctx.table = _Table(["x"], [])
        with pytest.raises(AssertionError, match="no result"):
            step_result_any_order(ctx)

    def test_table_step_still_compares_a_real_result(self):
        from tests.tck.steps.results import step_result_any_order

        ctx = _ctx(rows=[[1]], columns=["x"])
        ctx.table = _Table(["x"], [["1"]])
        step_result_any_order(ctx)


class TestErrorExpectingStepUnchanged:
    def test_raised_exception_still_satisfies_an_error_step(self):
        from tests.tck.steps.results import step_error_type_raised

        step_error_type_raised(_ctx(error=TypeError("bad")), "TypeError")


# ---------------------------------------------------------------------------
# Phase 4 (US1): side effects are counted
# ---------------------------------------------------------------------------


class TestSideEffectSteps:
    def test_matching_table_passes(self):
        from tests.tck.steps.results import step_side_effects_should_be

        ctx = _ctx(rows=[], delta=dict(ZERO, **{"+nodes": 1}))
        ctx.table = _Table(["+nodes", "1"])
        step_side_effects_should_be(ctx)

    def test_mismatch_names_column_expected_and_observed(self):
        from tests.tck.steps.results import step_side_effects_should_be

        ctx = _ctx(rows=[], delta=dict(ZERO, **{"+nodes": 2}))
        ctx.table = _Table(["+nodes", "1"])
        with pytest.raises(AssertionError) as ei:
            step_side_effects_should_be(ctx)
        msg = str(ei.value)
        assert "+nodes" in msg and "expected 1" in msg and "observed 2" in msg

    def test_unlisted_column_must_be_zero(self):
        from tests.tck.steps.results import step_side_effects_should_be

        ctx = _ctx(rows=[], delta=dict(ZERO, **{"+nodes": 1, "+properties": 1}))
        ctx.table = _Table(["+nodes", "1"])
        with pytest.raises(AssertionError, match=r"\+properties"):
            step_side_effects_should_be(ctx)

    def test_no_side_effects_fails_on_any_delta_and_names_the_table(self):
        from tests.tck.steps.results import step_no_side_effects

        ctx = _ctx(rows=[], delta=dict(ZERO, **{"+nodes": 1}))
        with pytest.raises(AssertionError) as ei:
            step_no_side_effects(ctx)
        assert "+nodes" in str(ei.value) and "nodes" in str(ei.value)

    def test_no_side_effects_passes_on_zero_delta(self):
        from tests.tck.steps.results import step_no_side_effects

        step_no_side_effects(_ctx(rows=[], delta=dict(ZERO)))

    def test_no_side_effects_fails_on_unexpected_table_write(self):
        from tests.tck.steps.results import step_no_side_effects

        ctx = _ctx(rows=[], delta=dict(ZERO), unexpected={"rdf_reifications": 1})
        with pytest.raises(AssertionError, match="rdf_reifications"):
            step_no_side_effects(ctx)

    def test_unmapped_column_fails(self):
        from tests.tck.steps.results import step_side_effects_should_be

        ctx = _ctx(rows=[], delta=dict(ZERO))
        ctx.table = _Table(["+indexes", "1"])
        with pytest.raises(AssertionError, match="unmapped side-effect column"):
            step_side_effects_should_be(ctx)

    def test_missing_snapshot_fails(self):
        from tests.tck.steps.results import step_no_side_effects

        with pytest.raises(AssertionError, match="snapshot"):
            step_no_side_effects(_ctx(rows=[], delta=None))

    def test_error_is_reported_before_counts(self):
        from tests.tck.steps.results import step_side_effects_should_be

        # a rolled-back query: delta is zero, the scenario expected a write
        ctx = _ctx(error=RuntimeError("SQLCODE -23"), delta=dict(ZERO))
        ctx.table = _Table(["+relationships", "1"])
        with pytest.raises(AssertionError) as ei:
            step_side_effects_should_be(ctx)
        msg = str(ei.value)
        assert "SQLCODE -23" in msg and "observed" not in msg

    def test_error_is_reported_by_no_side_effects_too(self):
        from tests.tck.steps.results import step_no_side_effects

        ctx = _ctx(error=RuntimeError("boom"), delta=dict(ZERO))
        with pytest.raises(AssertionError, match="boom"):
            step_no_side_effects(ctx)

    def test_lenient_mode_passes_silently(self, monkeypatch):
        from tests.tck.steps.results import step_no_side_effects, step_side_effects_should_be

        monkeypatch.setenv("IVG_TCK_LENIENT", "1")
        ctx = _ctx(rows=[], delta=dict(ZERO, **{"+nodes": 5}))
        ctx.table = _Table(["+nodes", "1"])
        step_no_side_effects(ctx)
        step_side_effects_should_be(ctx)


class TestQueryStepCapturesDelta:
    def _ctx(self, engine):
        return SimpleNamespace(scenario_label="TCK_abc12345", params={}, engine=engine, _tck_procedures={})

    def test_delta_is_captured_around_the_query(self, monkeypatch):
        import tests.tck.steps.query as q
        from tests.tck.side_effects import SideEffects

        states = iter([
            SideEffects.from_rows(nodes=[], labels=[], props=[], edges=[], reifications=0),
            SideEffects.from_rows(nodes=[("", "n1")], labels=[], props=[], edges=[], reifications=0),
        ])
        monkeypatch.setattr(q.SideEffects, "capture", classmethod(lambda cls, conn, **kw: next(states)))
        engine = SimpleNamespace(conn=object(), execute_cypher=lambda *a, **k: SimpleNamespace(rows=[], columns=[]))
        ctx = self._ctx(engine)
        q.step_executing_query(ctx, "CREATE ()")
        assert ctx.side_effects["+nodes"] == 1
        assert ctx.side_effects_error is None

    def test_delta_captured_even_when_the_query_raises(self, monkeypatch):
        import tests.tck.steps.query as q
        from tests.tck.side_effects import SideEffects

        empty = SideEffects.from_rows(nodes=[], labels=[], props=[], edges=[], reifications=0)
        monkeypatch.setattr(q.SideEffects, "capture", classmethod(lambda cls, conn, **kw: empty))

        def boom(*a, **k):
            raise RuntimeError("x")

        ctx = self._ctx(SimpleNamespace(conn=object(), execute_cypher=boom))
        q.step_executing_query(ctx, "CREATE ()")
        assert isinstance(ctx.last_error, RuntimeError)
        assert ctx.side_effects == ZERO

    def test_capture_failure_is_recorded_not_swallowed(self, monkeypatch):
        import tests.tck.steps.query as q

        def bad(cls, conn, **kw):
            raise RuntimeError("count failed")

        monkeypatch.setattr(q.SideEffects, "capture", classmethod(bad))
        engine = SimpleNamespace(conn=object(), execute_cypher=lambda *a, **k: SimpleNamespace(rows=[], columns=[]))
        ctx = self._ctx(engine)
        q.step_executing_query(ctx, "RETURN 1")
        assert ctx.side_effects is None
        assert "count failed" in ctx.side_effects_error
