"""An engine stand-in for translator tests whose namespace holds one graph.

With no engine the translator cannot tell how many graphs the namespace holds, so it writes SQL for several: each read
and write correlates its joins on graph_id, and a write with no USE GRAPH captures its node ids with their graphs and
runs once per graph (tests/e2e/test_460_all_graphs_writes_e2e.py). Tests of a statement's shape in one graph pass
this instead, and get the SQL a one-graph namespace runs.
"""

from __future__ import annotations


class OneGraphEngine:
    """Answers the translator's graph-count probe with "one graph", and has no table or relationship mappings."""

    def _more_than_one_graph(self) -> bool:
        return False

    def get_table_mapping(self, label):
        return None

    def get_rel_mapping(self, *args):
        return None


ONE_GRAPH = OneGraphEngine()
