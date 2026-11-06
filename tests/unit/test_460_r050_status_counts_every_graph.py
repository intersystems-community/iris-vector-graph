"""4.6.0 R050 (Python-newcomer walkthrough W8): the status report says its counts are every graph's.

With 14 nodes and 26 edges in the default graph and 2 and 1 in graph `lab`, `node_count()` and `edge_count()` answered
14 and 26 (the default graph) and `status().report()` "16 nodes · 27 edges" (every graph), and neither said which.
"""

from __future__ import annotations

from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.status import EngineStatus, TableCounts


def test_the_report_line_says_every_graph():
    report = EngineStatus(tables=TableCounts(nodes=16, edges=27)).report()
    assert "16 nodes · 27 edges in every graph" in report, report


def test_the_count_methods_say_default_graph_and_point_to_the_total():
    for method in (IRISGraphEngine.node_count, IRISGraphEngine.edge_count):
        assert "the default graph" in method.__doc__
        assert "status()" in method.__doc__, method.__name__
    assert "every graph" in IRISGraphEngine.status.__doc__
