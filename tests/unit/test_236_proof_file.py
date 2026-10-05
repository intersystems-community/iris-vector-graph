"""Spec 236: the proof file loads, or routing falls back to the Owners and says why."""

from __future__ import annotations

import copy
import json
import logging

import pytest

from iris_vector_graph import arno_readers
from iris_vector_graph.arno_readers import Proof, load_proof, validate_proof
from tests.unit._236_helpers import proof_doc, schema_errors

LOGGER = "iris_vector_graph.arno_readers"


def _write(tmp_path, text, name="proof.json"):
    path = tmp_path / name
    path.write_text(text if isinstance(text, str) else json.dumps(text))
    return path


def test_a_missing_file_is_an_empty_proof_and_a_logged_diagnostic(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        proof = load_proof(tmp_path / "nope.json")
    assert proof.algorithms == {}
    assert any("not found" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("text", ["", "{", "not json", "[]", "null", "42"])
def test_an_unparseable_file_is_an_empty_proof_and_a_logged_diagnostic(tmp_path, caplog, text):
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        proof = load_proof(_write(tmp_path, text))
    assert proof.algorithms == {}
    assert any("invalid" in r.getMessage() for r in caplog.records)


def test_a_schema_invalid_file_is_an_empty_proof_and_a_logged_diagnostic(tmp_path, caplog):
    doc = proof_doc(pagerank=0)  # min_revision must be >= 1
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        proof = load_proof(_write(tmp_path, doc))
    assert proof.algorithms == {}
    assert any("min_revision" in r.getMessage() for r in caplog.records)


def test_a_valid_file_loads_with_its_minimums(tmp_path):
    proof = load_proof(_write(tmp_path, proof_doc(pagerank=1, wcc=2)))
    assert proof.min_revision("pagerank") == 1
    assert proof.min_revision("wcc") == 2
    assert proof.min_revision("cdlp") is None


def test_the_proof_file_in_the_repo_is_valid():
    proof = load_proof(arno_readers._DEFAULT_PROOF)
    assert set(proof.algorithms) <= set(arno_readers.IN_SCOPE)
    assert schema_errors(json.loads(arno_readers._DEFAULT_PROOF.read_text())) == []


def test_the_default_proof_is_read_once_per_process(tmp_path, monkeypatch):
    path = _write(tmp_path, proof_doc(pagerank=1))
    monkeypatch.setenv(arno_readers.PROOF_ENV, str(path))
    arno_readers._default_proof.cache_clear()
    reads = []
    real = arno_readers._read_proof_file
    monkeypatch.setattr(arno_readers, "_read_proof_file", lambda p: (reads.append(p), real(p))[1])
    try:
        first = arno_readers.default_proof()
        second = arno_readers.default_proof()
        assert first is second and len(reads) == 1
        assert first.min_revision("pagerank") == 1
    finally:
        arno_readers._default_proof.cache_clear()


def test_a_name_ivg_does_not_route_is_not_accepted_from_a_file():
    doc = proof_doc(pagerank=1)
    doc["algorithms"]["betweenness"] = copy.deepcopy(doc["algorithms"]["pagerank"])
    assert validate_proof(doc)  # rejected outright, matching the schema's propertyNames enum
    assert schema_errors(doc)


# ------------------------------------------- the hand-written validator matches the schema


def _mutations():
    base = proof_doc(pagerank=1, cdlp=2)
    yield "valid", base

    def mut(label, fn):
        doc = copy.deepcopy(base)
        fn(doc)
        yield label, doc

    yield from mut("no library", lambda d: d.pop("library"))
    yield from mut("no proven_at", lambda d: d.pop("proven_at"))
    yield from mut("no algorithms", lambda d: d.pop("algorithms"))
    yield from mut("bad date", lambda d: d.update(proven_at="yesterday"))
    yield from mut("bad version", lambda d: d["library"].update(version="arno 1"))
    yield from mut("bad sha", lambda d: d["library"]["sha256"].update(arm64="abc"))
    yield from mut("upper sha", lambda d: d["library"]["sha256"].update(arm64="A" * 64))
    yield from mut("no sha256", lambda d: d["library"].pop("sha256"))
    yield from mut("rev zero", lambda d: d["algorithms"]["pagerank"].update(min_revision=0))
    yield from mut("rev str", lambda d: d["algorithms"]["pagerank"].update(min_revision="1"))
    yield from mut("rev bool", lambda d: d["algorithms"]["pagerank"].update(min_revision=True))
    yield from mut("few fixtures", lambda d: d["algorithms"]["pagerank"].update(fixtures=["a"] * 4))
    yield from mut("few repeats", lambda d: d["algorithms"]["pagerank"].update(repeats=4))
    yield from mut("no repeats", lambda d: d["algorithms"]["pagerank"].pop("repeats"))
    yield from mut("sim high", lambda d: d["algorithms"]["cdlp"].update(cdlp_similarity=1.5))
    yield from mut("sim ok", lambda d: d["algorithms"]["cdlp"].update(cdlp_similarity=0.9))
    yield from mut(
        "dropped ok",
        lambda d: d["algorithms"]["cdlp"].update(dropped=[{"fixture": "x", "reason": "unstable"}]),
    )
    yield from mut(
        "dropped bad", lambda d: d["algorithms"]["cdlp"].update(dropped=[{"fixture": "x"}])
    )
    yield from mut("unknown algorithm", lambda d: d["algorithms"].update(betweenness={}))
    yield from mut("empty algorithms", lambda d: d.update(algorithms={}))


@pytest.mark.parametrize("label,doc", list(_mutations()), ids=lambda v: v if isinstance(v, str) else "")
def test_validate_proof_agrees_with_the_committed_schema(label, doc):
    assert bool(validate_proof(doc)) == bool(schema_errors(doc)), (label, validate_proof(doc), schema_errors(doc))


def test_proof_from_dict_refuses_an_invalid_document():
    with pytest.raises(ValueError):
        Proof.from_dict({"library": {}})
