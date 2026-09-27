"""Spec 233: the genomics fixture tooling in tests/e2e/genomics_fixture.py. No IRIS."""

from __future__ import annotations

import collections
import hashlib
import os
import re

import pytest

from tests.e2e import genomics_fixture as gf

TARBALL = "/tmp/gr/p.tgz"
SOURCE_MD = os.path.join(os.path.dirname(gf.FIXTURE), "SOURCE.md")


def _refs(node):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "reference" and isinstance(v, str):
                yield v
            else:
                yield from _refs(v)
    elif isinstance(node, list):
        for v in node:
            yield from _refs(v)


def _bundle(bid, *entries):
    return {"resourceType": "Bundle", "id": bid, "type": "transaction", "entry": list(entries)}


def _entry(full_url, resource):
    return {"fullUrl": full_url, "resource": resource}


def _by_key(resources):
    return {f"{r['resourceType']}/{r['id']}": r for r in resources}


class TestAssemble:
    def test_parameters_dropped_bundles_expanded(self):
        examples = [
            {"resourceType": "Parameters", "id": "x"},
            {"resourceType": "Patient", "id": "p"},
            _bundle("b1", _entry("urn:uuid:1", {"resourceType": "Specimen", "id": "s"})),
        ]
        merged, counts = gf.assemble(examples)
        assert set(_by_key(merged)) == {"Patient/p", "Specimen/s"}
        assert counts["standalone"] == 1
        assert counts["bundle_entries"] == 1
        assert counts["kept"] == 2

    def test_duplicate_precedence(self):
        examples = [
            {"resourceType": "Patient", "id": "p", "gender": "female"},
            _bundle(
                "b2",
                _entry("urn:uuid:1", {"resourceType": "Patient", "id": "p", "gender": "male"}),
                _entry("urn:uuid:2", {"resourceType": "Specimen", "id": "s", "note": [{"text": "second"}]}),
            ),
            _bundle("b1", _entry("urn:uuid:3", {"resourceType": "Specimen", "id": "s", "note": [{"text": "first"}]})),
        ]
        merged, counts = gf.assemble(examples)
        got = _by_key(merged)
        assert got["Patient/p"]["gender"] == "female"
        assert got["Specimen/s"]["note"][0]["text"] == "first"
        assert counts["dup_standalone"] == 1
        assert counts["dup_bundle"] == 1
        assert counts["differing"] == 2

    def test_urn_scoped_to_its_bundle(self):
        examples = [
            _bundle(
                "b1",
                _entry("urn:uuid:X", {"resourceType": "Patient", "id": "p1"}),
                _entry("urn:uuid:o1", {"resourceType": "Observation", "id": "o1", "subject": {"reference": "urn:uuid:X"}}),
            ),
            _bundle(
                "b2",
                _entry("urn:uuid:X", {"resourceType": "Patient", "id": "p2"}),
                _entry("urn:uuid:o2", {"resourceType": "Observation", "id": "o2", "subject": {"reference": "urn:uuid:X"}}),
            ),
        ]
        got = _by_key(gf.assemble(examples)[0])
        assert got["Observation/o1"]["subject"]["reference"] == "Patient/p1"
        assert got["Observation/o2"]["subject"]["reference"] == "Patient/p2"

    def test_absolute_full_url_resolves(self):
        examples = [
            _bundle(
                "b1",
                _entry("http://example.org/fhir/Patient/p", {"resourceType": "Patient", "id": "p"}),
                _entry(
                    "http://example.org/fhir/Observation/o",
                    {"resourceType": "Observation", "id": "o", "subject": {"reference": "http://example.org/fhir/Patient/p"}},
                ),
            )
        ]
        got = _by_key(gf.assemble(examples)[0])
        assert got["Observation/o"]["subject"]["reference"] == "Patient/p"

    def test_long_id_shortened(self):
        long_id = "x" * 60
        short = long_id[:41] + "-" + hashlib.sha256(long_id.encode()).hexdigest()[:12]
        examples = [
            {"resourceType": "Observation", "id": long_id},
            {"resourceType": "DiagnosticReport", "id": "r", "result": [{"reference": f"Observation/{long_id}"}]},
        ]
        merged, counts = gf.assemble(examples)
        got = _by_key(merged)
        assert f"Observation/{short}" in got
        assert len(short) == 54
        assert got["DiagnosticReport/r"]["result"][0]["reference"] == f"Observation/{short}"
        assert counts["shortened"] == 1

    def test_missing_id_assigned_from_full_url(self):
        examples = [
            _bundle(
                "b1",
                _entry("urn:uuid:abc-123", {"resourceType": "Patient"}),
                _entry("http://example.org/fhir/Specimen/sp1", {"resourceType": "Specimen"}),
            )
        ]
        merged, counts = gf.assemble(examples)
        assert set(_by_key(merged)) == {"Patient/abc-123", "Specimen/sp1"}
        assert counts["assigned"] == 2
        assert gf.assemble(examples)[0] == merged

    def test_narrative_stripped_but_codeable_text_kept(self):
        examples = [
            {
                "resourceType": "Observation",
                "id": "o",
                "text": {"status": "generated", "div": "<div/>"},
                "code": {"text": "genomic"},
            }
        ]
        (obs,) = gf.assemble(examples)[0]
        assert "text" not in obs
        assert obs["code"]["text"] == "genomic"

    def test_contained_refs_counted_and_kept(self):
        examples = [
            {
                "resourceType": "Observation",
                "id": "o",
                "contained": [{"resourceType": "Specimen", "id": "s"}],
                "specimen": {"reference": "#s"},
            }
        ]
        merged, counts = gf.assemble(examples)
        assert merged[0]["specimen"]["reference"] == "#s"
        assert counts["contained_refs"] == 1

    def test_dangling_reference_raises(self):
        examples = [{"resourceType": "Observation", "id": "o", "subject": {"reference": "Patient/nobody"}}]
        with pytest.raises(ValueError, match="Patient/nobody"):
            gf.assemble(examples)


@pytest.fixture(scope="module")
def vendored():
    return gf.load_fixture()


class TestVendoredFile:
    def test_size_and_keys(self, vendored):
        keys = [f"{r['resourceType']}/{r['id']}" for r in vendored]
        assert len(vendored) == 377
        assert len(set(keys)) == 377
        assert max(len(r["id"]) for r in vendored) <= 54

    def test_every_reference_resolves(self, vendored):
        keys = {f"{r['resourceType']}/{r['id']}" for r in vendored}
        for r in vendored:
            contained = {c["id"] for c in r.get("contained", [])}
            for ref in _refs(r):
                assert not ref.startswith(("urn:", "http")), ref
                if ref.startswith("#"):
                    assert ref[1:] in contained, ref
                else:
                    assert ref in keys, ref

    def test_no_narrative(self, vendored):
        assert not [r["id"] for r in vendored if "text" in r]

    def test_per_type_counts(self, vendored):
        counts = collections.Counter(r["resourceType"] for r in vendored)
        assert counts == {
            "Observation": 263,
            "DiagnosticReport": 15,
            "Patient": 14,
            "MolecularSequence": 14,
            "DocumentReference": 13,
            "Specimen": 12,
            "Procedure": 11,
            "Task": 9,
            "ServiceRequest": 8,
            "Organization": 7,
            "Practitioner": 7,
            "MedicationStatement": 2,
            "RiskAssessment": 1,
            "Device": 1,
        }

    def test_reproducible_from_package(self, vendored):
        if not os.path.exists(TARBALL):
            pytest.skip(f"{TARBALL} absent: a vendoring input, not a container (see SOURCE.md)")
        merged, counts = gf.assemble(gf.read_package(TARBALL))
        assert merged == vendored
        with open(SOURCE_MD) as fh:
            block = re.search(r"```text\n(.*?)```", fh.read(), re.S).group(1)
        recorded = {k.strip(): int(v) for k, v in (line.split(":") for line in block.strip().splitlines())}
        assert recorded == counts


class TestExpectedEdges:
    def test_report_with_two_results_and_patient_subject(self):
        res = [
            {"resourceType": "Patient", "id": "p"},
            {"resourceType": "Observation", "id": "o1"},
            {"resourceType": "Observation", "id": "o2"},
            {"resourceType": "Specimen", "id": "s"},
            {
                "resourceType": "DiagnosticReport",
                "id": "r",
                "subject": {"reference": "Patient/p"},
                "specimen": [{"reference": "Specimen/s"}],
                "result": [{"reference": "Observation/o1"}, {"reference": "Observation/o2"}],
            },
        ]
        assert gf.expected_edges(res) == {
            ("DiagnosticReport/r", "subject", "Patient/p"),
            ("DiagnosticReport/r", "patient", "Patient/p"),
            ("DiagnosticReport/r", "specimen", "Specimen/s"),
            ("DiagnosticReport/r", "result", "Observation/o1"),
            ("DiagnosticReport/r", "result", "Observation/o2"),
        }

    def test_implication_derived_from_and_grouper_has_member(self):
        res = [
            {"resourceType": "Observation", "id": "v"},
            {"resourceType": "Observation", "id": "imp", "derivedFrom": [{"reference": "Observation/v"}]},
            {"resourceType": "Observation", "id": "grp", "hasMember": [{"reference": "Observation/imp"}]},
        ]
        assert gf.expected_edges(res) == {
            ("Observation/imp", "derived-from", "Observation/v"),
            ("Observation/grp", "has-member", "Observation/imp"),
        }

    def test_non_patient_subject_gives_subject_only(self):
        res = [
            {"resourceType": "Device", "id": "d"},
            {"resourceType": "Observation", "id": "o", "subject": {"reference": "Device/d"}},
        ]
        assert gf.expected_edges(res) == {("Observation/o", "subject", "Device/d")}

    def test_nested_paths(self):
        res = [
            {"resourceType": "Practitioner", "id": "pr"},
            {"resourceType": "Observation", "id": "o"},
            {"resourceType": "Device", "id": "d"},
            {"resourceType": "Procedure", "id": "x", "performer": [{"actor": {"reference": "Practitioner/pr"}}]},
            {
                "resourceType": "Provenance",
                "id": "pv",
                "target": [{"reference": "Observation/o"}],
                "agent": [{"who": {"reference": "Device/d"}}],
                "entity": [{"role": "source", "what": {"reference": "Observation/o"}}],
            },
            {"resourceType": "Task", "id": "t", "for": {"reference": "Observation/o"}},
        ]
        assert gf.expected_edges(res) == {
            ("Procedure/x", "performer", "Practitioner/pr"),
            ("Provenance/pv", "target", "Observation/o"),
            ("Provenance/pv", "agent", "Device/d"),
            ("Provenance/pv", "entity", "Observation/o"),
            ("Task/t", "subject", "Observation/o"),
        }

    def test_contained_and_unindexed_elements_skipped(self):
        res = [
            {"resourceType": "Observation", "id": "v"},
            {
                "resourceType": "Observation",
                "id": "o",
                "contained": [{"resourceType": "Specimen", "id": "s"}],
                "specimen": {"reference": "#s"},
                "extension": [{"url": "x", "valueReference": {"reference": "Observation/v"}}],
            },
        ]
        assert gf.expected_edges(res) == set()

    def test_unindexed_refs_named_by_path(self):
        res = [
            {"resourceType": "Observation", "id": "v"},
            {"resourceType": "Task", "id": "t", "reasonReference": [{"reference": "Observation/v"}]},
            {
                "resourceType": "Observation",
                "id": "o",
                "derivedFrom": [{"reference": "Observation/v"}],
                "extension": [{"url": "x", "valueReference": {"reference": "Observation/v"}}],
                "contained": [{"resourceType": "Specimen", "id": "s"}],
                "specimen": {"reference": "#s"},
            },
        ]
        assert gf.unindexed_refs(res) == {
            ("Task/t", "reasonReference", "Observation/v"),
            ("Observation/o", "extension.valueReference", "Observation/v"),
        }

    def test_vendored_unindexed_count(self, vendored):
        paths = collections.Counter(p for _, p, _ in gf.unindexed_refs(vendored))
        assert sum(paths.values()) == 62
        assert paths["extension.valueReference"] == 33
        assert paths["reasonReference"] == 9

    def test_vendored_has_result_and_derived_from(self, vendored):
        params = {p for _, p, _ in gf.expected_edges(vendored)}
        assert {"result", "derived-from", "has-member", "subject", "patient"} <= params


class TestModelResult:
    INPUTS = ["Observation/somatic-var-1", "Observation/somatic-var-2"]

    @pytest.fixture
    def trio(self):
        return gf.model_result(7, "Patient/somaticPatient", self.INPUTS)

    def test_ids_and_types(self, trio):
        assert [(r["resourceType"], r["id"]) for r in trio] == [
            ("Device", "model-v7"),
            ("Observation", "pred-v7"),
            ("Provenance", "prov-v7"),
        ]

    def test_device(self, trio):
        dev = trio[0]
        assert dev["version"][0]["value"] == "7"
        assert dev["deviceName"][0]["name"] == "genomic-risk-model"

    def test_prediction(self, trio):
        pred = trio[1]
        assert pred["status"] == "final"
        assert pred["subject"] == {"reference": "Patient/somaticPatient"}
        assert isinstance(pred["valueQuantity"]["value"], (int, float))

    def test_provenance(self, trio):
        prov = trio[2]
        assert prov["target"] == [{"reference": "Observation/pred-v7"}]
        assert prov["agent"][0]["who"] == {"reference": "Device/model-v7"}
        assert [e["what"]["reference"] for e in prov["entity"]] == self.INPUTS
        assert {e["role"] for e in prov["entity"]} == {"source"}
        assert prov["recorded"]

    def test_versions_do_not_share_ids(self):
        v7 = {r["id"] for r in gf.model_result(7, "Patient/p", [])}
        v8 = {r["id"] for r in gf.model_result(8, "Patient/p", [])}
        assert not v7 & v8

    def test_every_reference_resolves_in_the_trio_or_inputs(self, trio):
        keys = {f"{r['resourceType']}/{r['id']}" for r in trio} | set(self.INPUTS) | {"Patient/somaticPatient"}
        assert {ref for r in trio for ref in _refs(r)} <= keys
