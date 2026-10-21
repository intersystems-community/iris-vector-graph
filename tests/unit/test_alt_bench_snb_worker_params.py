"""snb_worker query --params-from: time the parameter sets an earlier run recorded, not a fresh sample.

A comparison holds per parameter set (row counts, signatures) only when both runs used the same sets. The sampler's
post and tag picks follow the dataset's files, so a run against a re-copied dataset drew other posts and tags than the
run it is compared with (IS6 agreed on 1 of 20 sets, each one row on both sides). The worker reuses a recorded run's
"params" instead.
"""

import json
from types import SimpleNamespace

from scripts.bench.alt import snb_worker


def test_recorded_params_are_used_as_they_are(tmp_path, monkeypatch):
    recorded = [{"personId": "1", "postId": "p9"}, {"personId": "2", "postId": "p3"}]
    f = tmp_path / "query_B_1_IS6.json"
    f.write_text(json.dumps({"ns": "X", "params": recorded}))
    monkeypatch.setattr(snb_worker.queries, "sample_params", lambda *a, **k: (_ for _ in ()).throw(AssertionError("sampled")))
    assert snb_worker.query_params(SimpleNamespace(params_from=str(f), root="r", params=20, seed=42)) == recorded


def test_without_a_file_the_sample_is_drawn(monkeypatch):
    monkeypatch.setattr(snb_worker.queries, "sample_params", lambda root, n, seed: [{"root": root, "n": n, "seed": seed}])
    args = SimpleNamespace(params_from=None, root="r", params=7, seed=3)
    assert snb_worker.query_params(args) == [{"root": "r", "n": 7, "seed": 3}]


def test_the_query_command_takes_the_option():
    import argparse

    seen = {}
    monkey = snb_worker.run_query
    try:
        snb_worker.run_query = lambda args: seen.setdefault("args", args)
        snb_worker.main(["query", "--ns", "N", "--root", "r", "--out", "o", "--params-from", "f.json"])
    finally:
        snb_worker.run_query = monkey
    assert isinstance(seen["args"], argparse.Namespace) and seen["args"].params_from == "f.json"
