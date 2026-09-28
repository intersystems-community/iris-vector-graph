"""Spec 235: batched loading in tests/e2e/fhir_conftest.py. No IRIS: a fake loader."""

from __future__ import annotations

import pytest

from tests.e2e import fhir_conftest as fc


class FakeLoader:
    def __init__(self, reject=()):
        self.calls = []
        self.reject = set(reject)

    def dispatch(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == "POST":
            entries = [
                {"response": {"status": "400" if e["request"]["url"] in self.reject else "201"}}
                for e in body["entry"]
            ]
            return {"status": "200 OK", "body": {"type": "batch-response", "entry": entries}}
        return {"status": "200"}


def _rs(n):
    return [{"resourceType": "Patient", "id": f"p{i}"} for i in range(n)]


@pytest.fixture(autouse=True)
def no_sync(monkeypatch):
    synced = []
    monkeypatch.setattr(fc, "_sync", lambda conn: synced.append(conn))
    return synced


def test_batch_bundle():
    b = fc.batch_bundle(_rs(2), "PUT")
    assert b["resourceType"] == "Bundle" and b["type"] == "batch"
    assert b["entry"][1] == {
        "resource": {"resourceType": "Patient", "id": "p1"},
        "request": {"method": "PUT", "url": "Patient/p1"},
    }
    d = fc.batch_bundle(_rs(1), "DELETE")
    assert d["entry"] == [{"request": {"method": "DELETE", "url": "Patient/p0"}}]


def test_load_run_batches_in_order(no_sync):
    ld = FakeLoader()
    stored = fc.load_run("c", ld, _rs(5), batch=2)
    assert [len(c[2]["entry"]) for c in ld.calls] == [2, 2, 1]
    assert [c[:2] for c in ld.calls] == [("POST", "")] * 3
    assert stored == _rs(5) and no_sync == ["c"]


def test_load_run_batch_rejection_fails_and_tears_down():
    ld = FakeLoader(reject={"Patient/p3"})
    with pytest.raises(pytest.fail.Exception, match="Patient/p3"):
        fc.load_run("c", ld, _rs(5), batch=2)
    deletes = [e["request"]["url"] for m, _, b in ld.calls if m == "POST" for e in b["entry"]
               if e["request"]["method"] == "DELETE"]
    assert deletes == ["Patient/p4", "Patient/p2", "Patient/p1", "Patient/p0"]


def test_teardown_batched_reverse_order(no_sync):
    ld = FakeLoader()
    fc.teardown_run("c", ld, _rs(3), batch=2)
    urls = [[e["request"]["url"] for e in b["entry"]] for _, _, b in ld.calls]
    assert urls == [["Patient/p2", "Patient/p1"], ["Patient/p0"]]
    assert no_sync == ["c"]


def test_unbatched_unchanged(no_sync):
    ld = FakeLoader()
    fc.load_run("c", ld, _rs(2))
    assert [c[:2] for c in ld.calls] == [("PUT", "/Patient/p0"), ("PUT", "/Patient/p1")]


def test_batches_split_by_size(monkeypatch):
    # A batch Bundle travels as one string: IRIS caps those at 3.6 MB (<MAXSTRING>).
    monkeypatch.setattr(fc, "BATCH_BYTES", 350)
    big = [{"resourceType": "Patient", "id": f"p{i}", "pad": "x" * 100} for i in range(5)]
    assert [len(c) for c in fc.batches(big, 200)] == [2, 2, 1]
    assert [len(c) for c in fc.batches(_rs(5), 2)] == [2, 2, 1]
    one = [{"resourceType": "Patient", "id": "huge", "pad": "x" * 1000}]
    assert fc.batches(one, 200) == [one]
