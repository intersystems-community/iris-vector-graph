"""Spec 236, Story 2: the proof file never claims more than the evidence supports.

Runs in USER on the enterprise container: the library's readers walk USER's ``^KG``
whatever namespace the caller is in, so USER is the only place they can be compared.
``IVG_REQUIRE_ARNO=1`` (the pre-release step) makes an absent library a failure.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path

import pytest

from iris_vector_graph import _arno_gate as gate
from iris_vector_graph import arno_readers

pytestmark = [pytest.mark.e2e]

ROOT = Path(__file__).resolve().parents[2]


def _load_runner():
    spec = importlib.util.spec_from_file_location("arno_reader_gate", ROOT / "scripts" / "arno_reader_gate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def runner():
    return _load_runner()


@pytest.fixture(scope="module")
def live(runner):
    """``(engine, dbapi, native, readers)``; skip or fail per IVG_REQUIRE_ARNO when absent."""
    action = gate.missing_library_action(os.environ)

    def absent(why: str):
        msg = f"Arno library unavailable: {why}"
        if action == "fail":
            pytest.fail(msg + " (IVG_REQUIRE_ARNO=1)")
        pytest.skip(msg)

    try:
        engine, native, dbapi = runner.connect()
    except Exception as exc:  # noqa: BLE001
        absent(str(exc))
    if not engine._store._detect_arno():
        absent("_detect_arno() is false")
    readers = arno_readers.read_reader_report(engine._store)
    yield engine, dbapi, native, readers
    native.classMethodValue("Graph.KG.Eraser", "EraseAll")
    dbapi.commit()


def _proof_doc() -> dict:
    return json.loads((ROOT / "docs" / "arno-reader-proof.json").read_text())


def test_the_committed_proof_does_not_claim_a_revision_the_loaded_library_lacks(live):
    *_, readers = live
    doc = _proof_doc()
    assert arno_readers.validate_proof(doc) == []
    assert gate.check_proof_against_report(doc, readers) == []


def test_a_tampered_proof_claiming_a_higher_revision_fails_the_check(live):
    *_, readers = live
    doc = copy.deepcopy(_proof_doc())
    name = next(iter(readers.keys() & set(arno_readers.IN_SCOPE)))
    doc["algorithms"][name] = {
        "min_revision": readers[name] + 1,
        "fixtures": list("abcde"),
        "repeats": 5,
    }
    problems = gate.check_proof_against_report(doc, readers)
    assert problems and name in problems[0]


def test_the_check_command_fails_on_a_tampered_proof(live, runner, tmp_path, monkeypatch, capsys):
    *_, readers = live
    doc = copy.deepcopy(_proof_doc())
    name = sorted(readers.keys() & set(arno_readers.IN_SCOPE))[0]
    doc["algorithms"][name] = {"min_revision": readers[name] + 1, "fixtures": list("abcde"), "repeats": 5}
    tampered = tmp_path / "proof.json"
    tampered.write_text(json.dumps(doc))
    monkeypatch.setattr(runner, "PROOF_PATH", tampered)
    assert runner.main(["--check"]) == 1
    assert "proof requires revision" in capsys.readouterr().err


def test_the_check_command_writes_nothing(live, runner, tmp_path, monkeypatch):
    target = tmp_path / "proof.json"
    target.write_text(json.dumps(_proof_doc()))
    before = target.read_bytes()
    monkeypatch.setattr(runner, "PROOF_PATH", target)
    assert runner.main(["--check"]) == 0
    assert target.read_bytes() == before


ALGORITHMS = list(arno_readers.IN_SCOPE)


@pytest.fixture(scope="module")
def results(live, runner):
    engine, dbapi, native, _ = live
    return runner.run_gate(engine, dbapi, native)


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_the_proof_file_agrees_with_the_evidence_for_each_algorithm(results, algorithm):
    """Proven implies five identical native calls equivalent to the Owner on every fixture;
    failing implies not proven. Both directions, so neither a stale claim nor a wrongly
    withheld one survives."""
    res = results[algorithm]
    proven = algorithm in _proof_doc()["algorithms"]
    failures = [f"{d['fixture']}: {d['reason']}" for d in res["details"] if not d["ok"] and not d["dropped"]]
    if proven:
        assert res["passed"], f"{algorithm} is in the proof file but fails: {failures}"
    else:
        assert not res["passed"], (
            f"{algorithm} passes on every fixture but is not in the proof file: "
            "run scripts/arno_reader_gate.py --write-proof"
        )


def test_every_algorithm_is_tried_on_all_five_fixtures_five_times(results, runner):
    for algorithm, res in results.items():
        assert res["repeats"] == 5
        assert [d["fixture"] for d in res["details"]] == list(runner.GRAPHS)
