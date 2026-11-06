"""Unit tests for graph_setup step definitions."""
from unittest.mock import MagicMock, patch
import pytest


class TestGraphSetupSteps:
    def test_empty_graph_sets_scenario_label(self):
        """Given an empty graph: context gets a fresh scenario_label."""
        from tests.tck.steps.graph_setup import step_empty_graph

        ctx = MagicMock()
        ctx.scenario_label = None
        step_empty_graph(ctx)
        assert ctx.scenario_label is not None
        assert ctx.scenario_label.startswith("TCK_")

    def test_any_graph_sets_scenario_label(self):
        """Given any graph: context gets a scenario_label."""
        from tests.tck.steps.graph_setup import step_any_graph

        ctx = MagicMock()
        ctx.scenario_label = None
        step_any_graph(ctx)
        assert ctx.scenario_label is not None

    def test_having_executed_runs_query(self):
        """Given having executed: runs a query on the engine without raising."""
        from tests.tck.steps.graph_setup import step_having_executed

        ctx = MagicMock()
        ctx.scenario_label = "TCK_abc123"
        ctx.engine.execute_cypher = MagicMock(return_value=MagicMock(rows=[], error=None))
        step_having_executed(ctx, "CREATE (n:Person {name: 'Alice'})")
        ctx.engine.execute_cypher.assert_called_once()

    def test_binary_tree_1_sets_label(self):
        """Given the binary-tree-1 graph: sets scenario_label to session tree label."""
        from tests.tck.steps.graph_setup import step_named_graph

        ctx = MagicMock()
        ctx.named_graphs = {"binary-tree-1": "TCK_BINARY_TREE_1"}
        ctx.engine.execute_cypher = MagicMock(return_value=MagicMock(rows=[[13]], error=None))
        step_named_graph(ctx, "binary-tree-1")
        assert ctx.scenario_label == "TCK_BINARY_TREE_1"
