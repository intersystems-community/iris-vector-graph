"""Spec 235: the Synthea fixture tooling in tests/e2e/interp_fixture.py. No IRIS."""

from __future__ import annotations

import collections
import copy
import glob
import hashlib
import json
import os
import re

import pytest

from tests.e2e import interp_fixture as ifx

SYNTHEA_OUT = os.path.expanduser("~/.cache/ivg-235/synthea-10/fhir")
SOURCE_MD = os.path.join(os.path.dirname(ifx.FIXTURE), "SOURCE.md")
NPI = "http://hl7.org/fhir/sid/us-npi"
SYN = "https://github.com/synthetichealth/synthea"


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


def _by_key(resources):
    return {f"{r['resourceType']}/{r['id']}": r for r in resources}


def _entry(uuid, resource):
    return {"fullUrl": f"urn:uuid:{uuid}", "resource": resource}


def _bundle(*entries, kind="transaction"):
    return {"resourceType": "Bundle", "type": kind, "entry": list(entries)}


def _support():
    org = {"resourceType": "Organization", "id": "org1", "identifier": [{"system": SYN, "value": "org1"}]}
    other = {"resourceType": "Organization", "id": "org2", "identifier": [{"system": SYN, "value": "org2"}]}
    loc = {
        "resourceType": "Location",
        "id": "loc1",
        "identifier": [{"system": SYN, "value": "loc1"}],
        "managingOrganization": {"reference": f"Organization?identifier={SYN}|org1"},
    }
    doc = {"resourceType": "Practitioner", "id": "doc1", "identifier": [{"system": NPI, "value": "999"}]}
    return [
        _bundle(_entry("o1", org), _entry("o2", other), _entry("l1", loc), kind="batch"),
        _bundle(_entry("d1", doc), kind="batch"),
    ]


def _patient_bundle(pid, uuid_obs="u-obs"):
    patient = {"resourceType": "Patient", "id": pid, "text": {"div": "<div/>"}}
    enc = {
        "resourceType": "Encounter",
        "id": f"{pid}-enc",
        "subject": {"reference": f"urn:uuid:{pid}"},
        "participant": [{"individual": {"reference": f"Practitioner?identifier={NPI}|999"}}],
        "location": [{"location": {"reference": f"Location?identifier={SYN}|loc1"}}],
    }
    obs = {
        "resourceType": "Observation",
        "id": f"{pid}-obs",
        "subject": {"reference": f"urn:uuid:{pid}"},
        "encounter": {"reference": f"urn:uuid:{pid}-enc"},
    }
    doc = {
        "resourceType": "DocumentReference",
        "id": f"{pid}-doc",
        "subject": {"reference": f"urn:uuid:{pid}"},
        "content": [{"attachment": {"contentType": "text/plain", "data": "aGVsbG8="}}],
    }
    report = {
        "resourceType": "DiagnosticReport",
        "id": f"{pid}-dr",
        "subject": {"reference": f"urn:uuid:{pid}"},
        "presentedForm": [{"contentType": "text/plain", "data": "aGVsbG8="}],
    }
    claim = {"resourceType": "Claim", "id": f"{pid}-claim", "patient": {"reference": f"urn:uuid:{pid}"}}
    eob = {
        "resourceType": "ExplanationOfBenefit",
        "id": f"{pid}-eob",
        "claim": {"reference": f"urn:uuid:{pid}-claim"},
    }
    prov = {
        "resourceType": "Provenance",
        "id": f"{pid}-prov",
        "target": [
            {"reference": f"urn:uuid:{pid}"},
            {"reference": f"urn:uuid:{uuid_obs}"},
            {"reference": f"urn:uuid:{pid}-claim"},
            {"reference": f"urn:uuid:{pid}-eob"},
        ],
    }
    return _bundle(
        _entry(pid, patient),
        _entry(f"{pid}-enc", enc),
        _entry(uuid_obs, obs),
        _entry(f"{pid}-doc", doc),
        _entry(f"{pid}-dr", report),
        _entry(f"{pid}-claim", claim),
        _entry(f"{pid}-eob", eob),
        _entry(f"{pid}-prov", prov),
    )


class TestAssemble:
    def test_urn_uuid_rewritten_per_bundle(self):
        # Both bundles use the fullUrl urn:uuid:u-obs for different Observations.
        res, counts = ifx.assemble([_patient_bundle("pa"), _patient_bundle("pb")], _support())
        by = _by_key(res)
        assert by["Provenance/pa-prov"]["target"][1] == {"reference": "Observation/pa-obs"}
        assert by["Provenance/pb-prov"]["target"][1] == {"reference": "Observation/pb-obs"}
        assert by["Observation/pa-obs"]["encounter"] == {"reference": "Encounter/pa-enc"}
        assert counts["rewritten"] > 0

    def test_conditional_resolved(self):
        res, counts = ifx.assemble([_patient_bundle("pa")], _support())
        enc = _by_key(res)["Encounter/pa-enc"]
        assert enc["participant"][0]["individual"] == {"reference": "Practitioner/doc1"}
        assert enc["location"][0]["location"] == {"reference": "Location/loc1"}
        assert counts["conditional"] == 3  # participant, location, and the Location's managingOrganization

    def test_unresolvable_conditional_raises(self):
        b = _patient_bundle("pa")
        b["entry"][1]["resource"]["participant"][0]["individual"]["reference"] = f"Practitioner?identifier={NPI}|000"
        with pytest.raises(ValueError, match="000"):
            ifx.assemble([b], _support())

    def test_unresolvable_urn_raises(self):
        b = _patient_bundle("pa")
        b["entry"][2]["resource"]["encounter"]["reference"] = "urn:uuid:nowhere"
        with pytest.raises(ValueError, match="nowhere"):
            ifx.assemble([b], _support())

    def test_only_referenced_support_kept(self):
        res, _ = ifx.assemble([_patient_bundle("pa")], _support())
        keys = set(_by_key(res))
        # org1 is reached through the kept Location, transitively; org2 is never referenced.
        assert {"Practitioner/doc1", "Location/loc1", "Organization/org1"} <= keys
        assert "Organization/org2" not in keys

    def test_claim_and_eob_dropped_and_unreferenced(self):
        res, counts = ifx.assemble([_patient_bundle("pa")], _support())
        types = {r["resourceType"] for r in res}
        assert not types & ifx.DROP_TYPES
        prov = _by_key(res)["Provenance/pa-prov"]
        assert prov["target"] == [{"reference": "Patient/pa"}, {"reference": "Observation/pa-obs"}]
        assert not any(r.split("/")[0] in ifx.DROP_TYPES for r in _refs(res))
        assert counts["dropped_refs"] == 2

    def test_dropped_type_as_single_target_raises(self):
        # In Synthea a Claim is only ever referenced from a list (Provenance.target)
        # or from a dropped EOB; a single-valued reference to it would lose data.
        b = _patient_bundle("pa")
        b["entry"][2]["resource"]["basedOn"] = {"reference": "urn:uuid:pa-claim"}
        with pytest.raises(ValueError, match="Claim"):
            ifx.assemble([b], _support())

    def test_text_and_attachment_data_stripped(self):
        res, _ = ifx.assemble([_patient_bundle("pa")], _support())
        by = _by_key(res)
        assert "text" not in by["Patient/pa"]
        assert by["DocumentReference/pa-doc"]["content"][0]["attachment"] == {"contentType": "text/plain"}
        # An Attachment need not sit under an `attachment` key: DiagnosticReport.presentedForm is a list of them.
        assert by["DiagnosticReport/pa-dr"]["presentedForm"] == [{"contentType": "text/plain"}]
        blob = json.dumps(res)
        assert '"data"' not in blob and '"text": {' not in blob

    def test_one_seealso_link_both_ways(self):
        res, counts = ifx.assemble([_patient_bundle("pb"), _patient_bundle("pc"), _patient_bundle("pa")], _support())
        by = _by_key(res)
        assert by["Patient/pa"]["link"] == [{"other": {"reference": "Patient/pb"}, "type": "seealso"}]
        assert by["Patient/pb"]["link"] == [{"other": {"reference": "Patient/pa"}, "type": "seealso"}]
        assert "link" not in by["Patient/pc"]
        assert counts["links_added"] == 2

    def test_every_reference_in_set(self):
        res, _ = ifx.assemble([_patient_bundle("pa"), _patient_bundle("pb")], _support())
        keys = set(_by_key(res))
        for ref in _refs(res):
            assert re.fullmatch(r"[A-Z][A-Za-z]+/[A-Za-z0-9\-.]+", ref), ref
            assert ref in keys, ref

    def test_counts_per_type(self):
        res, counts = ifx.assemble([_patient_bundle("pa")], _support())
        per_type = collections.Counter(r["resourceType"] for r in res)
        assert counts["types"] == dict(sorted(per_type.items()))
        assert counts["kept"] == len(res)

    def test_input_not_mutated(self):
        patients, support = [_patient_bundle("pa")], _support()
        before = copy.deepcopy((patients, support))
        ifx.assemble(patients, support)
        assert (patients, support) == before

    def test_sorted_by_key(self):
        res, _ = ifx.assemble([_patient_bundle("pb"), _patient_bundle("pa")], _support())
        keys = [f"{r['resourceType']}/{r['id']}" for r in res]
        assert keys == sorted(keys)


def _source_md():
    with open(SOURCE_MD) as fh:
        return fh.read()


def _counts_block():
    """The `key: value` lines of SOURCE.md's first ```text block."""
    m = re.search(r"## Counts\s+```text\n(.*?)```", _source_md(), re.S)
    assert m, "SOURCE.md has no Counts block"
    return dict(line.split(": ", 1) for line in m.group(1).strip().splitlines())


@pytest.fixture(scope="module")
def vendored():
    with open(ifx.FIXTURE) as fh:
        return json.load(fh)


class TestVendoredFile:
    def test_collection_bundle(self, vendored):
        assert vendored["resourceType"] == "Bundle"
        assert vendored["type"] == "collection"

    def test_unique_keys_and_id_length(self, vendored):
        keys = [f"{e['resource']['resourceType']}/{e['resource']['id']}" for e in vendored["entry"]]
        assert len(keys) == len(set(keys))
        assert max(len(e["resource"]["id"]) for e in vendored["entry"]) <= ifx.MAX_ID

    def test_references_closed(self, vendored):
        res = [e["resource"] for e in vendored["entry"]]
        keys = set(_by_key(res))
        bad = [r for r in _refs(res) if r not in keys]
        assert not bad, bad[:10]

    def test_no_urn_conditional_text_or_data(self):
        with open(ifx.FIXTURE) as fh:
            raw = fh.read()
        res = ifx.load_fixture()
        # Identifier values may be urn:uuid strings; references may not.
        assert not [r for r in _refs(res) if r.startswith("urn:") or "?identifier=" in r]
        for r in res:
            assert "text" not in r, r["id"]
        assert '"data":' not in raw

    def test_one_linked_pair(self):
        links = {
            f"Patient/{r['id']}": [x["other"]["reference"] for x in r.get("link", [])]
            for r in ifx.load_fixture()
            if r["resourceType"] == "Patient"
        }
        linked = {k: v for k, v in links.items() if v}
        assert len(linked) == 2
        (a, [b]), (c, [d]) = sorted(linked.items())
        assert (a, b) == (d, c)

    def test_counts_equal_source_md(self):
        counts = _counts_block()
        per_type = collections.Counter(r["resourceType"] for r in ifx.load_fixture())
        for t, n in per_type.items():
            assert int(counts[t]) == n, t
        assert int(counts["kept"]) == sum(per_type.values())

    def test_sha256_equals_source_md(self):
        with open(ifx.FIXTURE, "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        assert digest in _source_md()

    @pytest.mark.skipif(not os.path.isdir(SYNTHEA_OUT), reason="Synthea output not generated (SOURCE.md exemption)")
    def test_reproducible_from_synthea_output(self, tmp_path):
        out = tmp_path / "fixture.json"
        ifx.vendor(SYNTHEA_OUT, str(out))
        with open(ifx.FIXTURE, "rb") as a, open(out, "rb") as b:
            assert a.read() == b.read()

    def test_synthea_output_has_both_bundle_kinds(self):
        if not os.path.isdir(SYNTHEA_OUT):
            pytest.skip("Synthea output not generated (SOURCE.md exemption)")
        names = [os.path.basename(p) for p in glob.glob(os.path.join(SYNTHEA_OUT, "*.json"))]
        assert any(n.startswith("hospitalInformation") for n in names)
        assert any(n.startswith("practitionerInformation") for n in names)


def test_load_order_puts_referenced_types_first():
    rs = [
        {"resourceType": t, "id": str(i)}
        for i, t in enumerate(["Observation", "Encounter", "Patient", "Organization", "Condition", "Location"])
    ]
    got = [r["resourceType"] for r in ifx.load_order(rs)]
    assert got == ["Organization", "Location", "Patient", "Encounter", "Observation", "Condition"]
