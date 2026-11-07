"""A TCK scenario whose graph was not built fails, with the reason (4.6.0 R310).

`step_having_executed` used to wrap each setup query in `except Exception: pass`
("setup queries may fail if already exists"), and never read `IVGResult.error`, so a
scenario whose setup failed ran on a graph that was not built and passed or failed for
the wrong reason. Setup, the named-graph build, the ^KG sync after them, the session
start (schema, flush, multigraph mode) and the teardown now raise; the only handlers
left that drop an exception are listed in `_ALLOWED_SWALLOWS`, each with its reason.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

TCK = Path(__file__).resolve().parents[3] / "tests" / "tck"


def _ctx(result=None, exc=None):
    engine = MagicMock()
    if exc is not None:
        engine.execute_cypher.side_effect = exc
    else:
        engine.execute_cypher.return_value = (
            result if result is not None else SimpleNamespace(rows=[], columns=[], error=None)
        )
    return SimpleNamespace(engine=engine, scenario_label="TCK_abc12345", _capture_fields={})


class TestHavingExecuted:
    def test_a_setup_query_that_raises_fails_the_step_with_the_error(self):
        from tests.tck.steps.graph_setup import SetupFailed, step_having_executed

        ctx = _ctx(exc=RuntimeError("SQLCODE -400 <SUBSCRIPT>"))
        with pytest.raises(SetupFailed) as ei:
            step_having_executed(ctx, "CREATE (a {var: 'start'})")
        msg = str(ei.value)
        assert "SQLCODE -400 <SUBSCRIPT>" in msg
        assert "CREATE (a" in msg
        assert "RuntimeError" in msg

    def test_a_setup_query_answering_ivgresult_error_fails_the_step(self):
        """The store answers a failed statement with IVGResult.error and no rows, not a raise."""
        from tests.tck.steps.graph_setup import SetupFailed, step_having_executed

        ctx = _ctx(result=SimpleNamespace(rows=[], columns=[], error="SQLCODE -1 bad"))
        with pytest.raises(SetupFailed, match="SQLCODE -1 bad"):
            step_having_executed(ctx, "CREATE (:A)")

    def test_setup_asks_the_engine_to_raise(self):
        from tests.tck.steps.graph_setup import step_having_executed

        ctx = _ctx()
        step_having_executed(ctx, "CREATE (:A)")
        kwargs = ctx.engine.execute_cypher.call_args.kwargs
        assert kwargs.get("raise_on_error") is True

    def test_a_setup_query_that_succeeds_runs_the_labelled_query_and_syncs(self):
        from tests.tck.steps.graph_setup import step_having_executed

        ctx = _ctx()
        step_having_executed(ctx, "CREATE (:A)")
        sent = ctx.engine.execute_cypher.call_args.args[0]
        assert sent == "CREATE (:A:TCK_abc12345)"
        ctx.engine.sync.assert_called_once_with()

    def test_a_failed_setup_does_not_sync(self):
        from tests.tck.steps.graph_setup import SetupFailed, step_having_executed

        ctx = _ctx(exc=RuntimeError("boom"))
        with pytest.raises(SetupFailed):
            step_having_executed(ctx, "CREATE (:A)")
        ctx.engine.sync.assert_not_called()

    def test_a_failed_sync_fails_the_step(self):
        """A stale ^KG answers variable-length reads over the graph before the setup."""
        from tests.tck.steps.graph_setup import SetupFailed, step_having_executed

        ctx = _ctx()
        ctx.engine.sync.side_effect = RuntimeError("TraversalBuild failed")
        with pytest.raises(SetupFailed, match="TraversalBuild failed"):
            step_having_executed(ctx, "CREATE (:A)")

    def test_the_failure_is_recorded_for_the_capture(self, monkeypatch, tmp_path):
        from tests.tck.steps.graph_setup import SetupFailed, step_having_executed

        monkeypatch.setenv("IVG_TCK_CAPTURE", str(tmp_path))
        ctx = _ctx(exc=RuntimeError("boom"))
        with pytest.raises(SetupFailed):
            step_having_executed(ctx, "CREATE (:A)")
        [rec] = ctx._capture_fields["setup_errors"]
        assert rec["query"] == "CREATE (:A:TCK_abc12345)"
        assert "boom" in rec["error"]


class TestNamedGraph:
    def _ctx(self, count_result=None, count_exc=None, build_exc=None, build_error=None):
        ctx = _ctx()
        calls = []

        def run(q, params, **kw):
            calls.append(q)
            if q.startswith("MATCH"):
                if count_exc:
                    raise count_exc
                return count_result or SimpleNamespace(rows=[[0]], columns=["cnt"], error=None)
            if build_exc:
                raise build_exc
            return SimpleNamespace(rows=[], columns=[], error=build_error)

        ctx.engine.execute_cypher.side_effect = run
        ctx.calls = calls
        return ctx

    def test_a_named_graph_that_fails_to_build_raises(self):
        from tests.tck.environment import _ensure_named_graph
        from tests.tck.steps.graph_setup import SetupFailed

        ctx = self._ctx(build_exc=RuntimeError("SQLCODE -119"))
        with pytest.raises(SetupFailed, match="SQLCODE -119"):
            _ensure_named_graph(ctx, "binary-tree-1", "TCK_BINARY_TREE_1")

    def test_a_named_graph_answering_ivgresult_error_raises(self):
        from tests.tck.environment import _ensure_named_graph
        from tests.tck.steps.graph_setup import SetupFailed

        ctx = self._ctx(build_error="SQLCODE -1")
        with pytest.raises(SetupFailed, match="SQLCODE -1"):
            _ensure_named_graph(ctx, "binary-tree-1", "TCK_BINARY_TREE_1")

    def test_a_failed_count_raises_rather_than_rebuilding_blind(self):
        from tests.tck.environment import _ensure_named_graph
        from tests.tck.steps.graph_setup import SetupFailed

        ctx = self._ctx(count_exc=RuntimeError("connection lost"))
        with pytest.raises(SetupFailed, match="connection lost"):
            _ensure_named_graph(ctx, "binary-tree-1", "TCK_BINARY_TREE_1")

    def test_an_unknown_named_graph_raises(self):
        from tests.tck.environment import _ensure_named_graph

        ctx = self._ctx()
        with pytest.raises(KeyError):
            _ensure_named_graph(ctx, "binary-tree-9", "TCK_X")

    def test_a_built_named_graph_is_not_rebuilt(self):
        from tests.tck.environment import _ensure_named_graph

        ctx = self._ctx(count_result=SimpleNamespace(rows=[[13]], columns=["cnt"], error=None))
        _ensure_named_graph(ctx, "binary-tree-1", "TCK_BINARY_TREE_1")
        assert len(ctx.calls) == 1

    def test_an_unbuilt_named_graph_is_built_and_synced(self):
        from tests.tck.environment import _ensure_named_graph

        ctx = self._ctx()
        _ensure_named_graph(ctx, "binary-tree-1", "TCK_BINARY_TREE_1")
        assert len(ctx.calls) == 2 and ":TCK_BINARY_TREE_1" in ctx.calls[1]
        ctx.engine.sync.assert_called_once_with()


class TestSessionAndTeardown:
    def _store_ctx(self, execute):
        cursor = MagicMock()
        cursor.execute.side_effect = execute
        cursor.fetchall.return_value = [("n1",)]
        conn = MagicMock()
        conn.cursor.return_value = cursor
        engine = MagicMock()
        engine._schema_prefix = "Graph_KG"
        engine._store = SimpleNamespace(conn=conn)
        return SimpleNamespace(engine=engine)

    def test_a_flush_that_fails_raises(self):
        """Rows a failed flush leaves are visible to every later `MATCH ()`."""
        from tests.tck.environment import _flush_all_tck_data

        def execute(sql, *a):
            raise RuntimeError("SQLCODE -124")

        with pytest.raises(RuntimeError, match="-124"):
            _flush_all_tck_data(self._store_ctx(execute))

    def test_a_teardown_that_fails_raises_after_trying_every_sweep(self):
        from tests.tck.environment import TeardownFailed, _teardown_label

        seen = []

        def execute(sql, *a):
            seen.append(sql.split()[0])
            if sql.startswith("DELETE FROM Graph_KG.rdf_edges WHERE s IN (?"):
                raise RuntimeError("SQLCODE -124")

        ctx = self._store_ctx(execute)
        with pytest.raises(TeardownFailed, match="-124"):
            _teardown_label(ctx, "TCK_abc")
        # the later sweeps still ran: one failure does not leave the rest behind
        assert seen.count("DELETE") >= 3

    def test_a_teardown_whose_cypher_delete_fails_raises(self):
        from tests.tck.environment import TeardownFailed, _teardown_label

        ctx = self._store_ctx(lambda sql, *a: None)
        ctx.engine.execute_cypher.side_effect = RuntimeError("delete failed")
        with pytest.raises(TeardownFailed, match="delete failed"):
            _teardown_label(ctx, "TCK_abc")

    def test_multigraph_mode_that_cannot_be_set_raises(self, monkeypatch):
        """The suite would otherwise measure the single-edge mode and call it the multigraph figure."""
        from tests.tck.environment import _enable_tck_multigraph

        monkeypatch.delenv("IVG_TCK_MULTIGRAPH", raising=False)
        ctx = SimpleNamespace(engine=MagicMock())
        ctx.engine.set_multigraph.side_effect = RuntimeError("no registry")
        with pytest.raises(RuntimeError, match="no registry"):
            _enable_tck_multigraph(ctx)


# (file relative to tests/tck, enclosing function) -> why dropping the exception there is safe.
_ALLOWED_SWALLOWS = {
    ("capture.py", "note"): "diagnostic capture only; it never decides a verdict",
    ("capture.py", "note_query"): "diagnostic capture only; it never decides a verdict",
    ("capture.py", "flush"): "diagnostic capture only, written after the verdict",
    ("side_effects.py", "capture"): "closing a cursor after its rows were read",
    ("steps/comparison.py", "_rows"): "closing a cursor after its rows were read",
    ("environment.py", "_connect"): "a host that is not OrbStack falls through to the next way to connect",
    ("environment.py", "after_all"): "closing the session connection and resetting multigraph after the last verdict",
    ("environment.py", "_reconnect"): "closing a connection already lost, before the fresh one; the reconnect is logged",
    ("conftest.py", "load_wip_registry"): "no deferral file means no deferrals, which is stricter, not looser",
}


def _swallows(path: Path):
    """(function, line) of every handler whose body only passes/continues, and every suppress(Exception)."""
    tree = ast.parse(path.read_text())
    out = []

    def visit(node, func):
        for child in ast.iter_child_nodes(node):
            f = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else func
            if isinstance(child, ast.ExceptHandler) and all(
                isinstance(s, (ast.Pass, ast.Continue)) for s in child.body
            ):
                out.append((f, child.lineno))
            if isinstance(child, ast.withitem):
                call = child.context_expr
                if (
                    isinstance(call, ast.Call)
                    and getattr(call.func, "attr", getattr(call.func, "id", "")) == "suppress"
                ):
                    out.append((f, call.lineno))
            visit(child, f)

    visit(tree, None)
    return out


def test_no_tck_harness_code_drops_an_exception_outside_the_allowed_list():
    found = []
    for path in sorted(TCK.rglob("*.py")):
        if "features" in path.parts:
            continue
        rel = path.relative_to(TCK).as_posix()
        for func, line in _swallows(path):
            if (rel, func) not in _ALLOWED_SWALLOWS:
                found.append(f"{rel}:{line} in {func}()")
    assert not found, "exceptions dropped in the TCK harness:\n  " + "\n  ".join(found)


def test_the_allowed_list_names_only_handlers_that_exist():
    present = set()
    for path in sorted(TCK.rglob("*.py")):
        if "features" in path.parts:
            continue
        rel = path.relative_to(TCK).as_posix()
        present |= {(rel, func) for func, _ in _swallows(path)}
    assert set(_ALLOWED_SWALLOWS) <= present, set(_ALLOWED_SWALLOWS) - present
