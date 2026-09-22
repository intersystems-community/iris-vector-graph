"""`Graph.KG.PageRank.RunJson` has to honour `bidir` and `revWeight`, not just accept them.

The Python seam was fixed first (`tests/unit/test_ppr_bidirectional_passthrough.py`):
`IRISGraphStore.execute_ppr` now passes all five arguments the ObjectScript signature
declares. That changed nothing on a deployed install, because `PageRank.cls` documented
both as `Reserved (unused, kept for API compat)` and the body only ever walked
`^KG("out", …)`. A bidirectional request therefore still came back forward-only — with no
error, which is the failure mode worth a test of its own.

This file drives the ObjectScript directly rather than through
`kg_PERSONALIZED_PAGERANK`, so a regression cannot hide behind the engine's Python
fallback: the fallback computes the reverse half correctly and would mask an inert
`bidir` completely.

Semantics are the fallback's (`_engine/algorithms.py:176-191`): a node's divisor counts
its forward *and* reverse edges, and a reverse hop carries `revWeight` as a multiplier on
the contribution, not on the divisor.
"""

import json
import os
import uuid

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestRunJsonReverseEdges:
    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        from iris_vector_graph.engine import IRISGraphEngine

        self.conn = iris_connection
        self.engine = IRISGraphEngine(iris_connection)
        self.run = uuid.uuid4().hex[:8]
        self.a = f"RJREV_{self.run}:A"
        self.b = f"RJREV_{self.run}:B"
        self.engine.create_node(self.a)
        self.engine.create_node(self.b)
        self.engine.create_edge(self.a, "connects", self.b)
        yield
        cursor = self.conn.cursor()
        like = f"RJREV_{self.run}:%"
        cursor.execute(
            "DELETE FROM Graph_KG.rdf_edges WHERE s LIKE ? OR o_id LIKE ?", [like, like]
        )
        for table, column in (
            ("Graph_KG.rdf_props", "s"),
            ("Graph_KG.rdf_labels", "s"),
            ("Graph_KG.nodes", "node_id"),
        ):
            cursor.execute(f"DELETE FROM {table} WHERE {column} LIKE ?", [like])
        self.conn.commit()

    def _run_json(self, seeds, bidir, rev_weight=1.0):
        raw = self.engine._store._call_classmethod(
            "Graph.KG.PageRank",
            "RunJson",
            json.dumps(seeds),
            "0.85",
            "20",
            "1" if bidir else "0",
            str(float(rev_weight)),
        )
        return {row["id"]: row["score"] for row in json.loads(str(raw))}

    def test_forward_only_cannot_reach_the_source(self):
        """Baseline: the edge points A -> B, so seeding B reaches nothing."""
        scores = self._run_json([self.b], bidir=False)
        assert self.b in scores
        assert self.a not in scores

    def test_bidir_reaches_the_source(self):
        scores = self._run_json([self.b], bidir=True)
        assert self.a in scores, (
            f"bidir=1 must traverse ^KG(\"in\") back to {self.a}; got {scores}"
        )
        assert scores[self.a] > 0

    def test_rev_weight_scales_the_reverse_contribution(self):
        """Lower weight, lower score — but not proportionally.

        Measured on the live container: A scores 0.4417 at weight 1.0 and 0.0998 at 0.5,
        a ratio of 0.23 rather than 0.5. The power method compounds, so A's reduced rank
        also feeds less back into B on the next pass and the effect is not linear in the
        weight. Asserting a halving here would be asserting a property PPR does not have.
        """
        full = self._run_json([self.b], bidir=True, rev_weight=1.0)
        half = self._run_json([self.b], bidir=True, rev_weight=0.5)
        assert half[self.a] < full[self.a]

    def test_rev_weight_zero_is_forward_only(self):
        """`reverse_edge_weight = 0` means no reverse contribution, not a divisor of zero."""
        scores = self._run_json([self.b], bidir=True, rev_weight=0.0)
        assert scores.get(self.a, 0) == 0

    def test_forward_reach_is_unchanged_by_bidir(self):
        """Adding the reverse half must not cost the forward half."""
        forward = self._run_json([self.a], bidir=False)
        both = self._run_json([self.a], bidir=True)
        assert self.b in forward
        assert self.b in both
