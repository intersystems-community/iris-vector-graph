"""The shortest-path benchmarks (bench.py Q5/Q6) need two different nodes; a pair that is (seed, seed) times a trivial answer."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests" / "benchmarks"))

import bench_utils  # noqa: E402


class FakeIris:
    def __init__(self, rows):
        self.rows = rows

    def classMethodValue(self, cls, method, *args):
        return json.dumps(self.rows)


def test_prefers_a_node_at_the_requested_distance_that_is_not_the_seed():
    rows = [{"step": 1, "o": "n1"}, {"step": 3, "o": "n0"}, {"step": 3, "o": "n7"}]
    assert bench_utils.pick_shortest_path_pair(FakeIris(rows), "n0", target_distance=3) == ("n0", "n7")


def test_falls_back_to_the_farthest_node_other_than_the_seed():
    rows = [{"step": 1, "o": "n1"}, {"step": 2, "o": "n0"}, {"step": 2, "o": "n5"}]
    assert bench_utils.pick_shortest_path_pair(FakeIris(rows), "n0") == ("n0", "n5")


def test_returns_none_when_the_only_reachable_node_is_the_seed():
    assert bench_utils.pick_shortest_path_pair(FakeIris([{"step": 1, "o": "n0"}]), "n0") == ("n0", None)
