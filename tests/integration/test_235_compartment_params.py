"""Spec 235 US1: Graph.KG.FHIRGraph.CompartmentParams, called directly over TCP in
IVGFHIR (constitution IV). The expected sets come from the base R4
CompartmentDefinition shipped in the container (research R1)."""

from __future__ import annotations

import json

import pytest

from tests.e2e.fhir_conftest import GRAPH, _fhir_session, fhir_conn  # noqa: F401

COMPDEF = "/usr/irissys/dev/fhir/fhir-metadata/packages/hl7.fhir.r4.core/CompartmentDefinition-patient.json"


def _params(conn, rtype, compartment=None):
    import iris

    args = [GRAPH, rtype] + ([compartment] if compartment is not None else [])
    out = json.loads(iris.createIRIS(conn).classMethodValue("Graph.KG.FHIRGraph", "CompartmentParams", *args))
    assert out.get("status") != "error", out
    return out


def _read_file(conn, path):
    import iris

    irisobj = iris.createIRIS(conn)
    stream = irisobj.classMethodObject("%Stream.FileCharacter", "%New")
    stream.invokeVoid("LinkToFile", path)
    chunks = []
    while not stream.get("AtEnd"):
        chunks.append(stream.invoke("Read", 32000))
    return "".join(chunks)


@pytest.mark.parametrize(
    "rtype, want",
    [
        ("Observation", ["performer", "subject"]),
        ("Provenance", ["patient"]),
        ("Patient", ["link"]),
        ("Medication", []),
    ],
)
def test_params(fhir_conn, rtype, want):  # noqa: F811
    out = _params(fhir_conn, rtype)
    assert out == {"status": "ok", "type": rtype, "params": want}


def test_unknown_compartment_is_empty(fhir_conn):  # noqa: F811
    assert _params(fhir_conn, "Observation", "nosuchcompartment")["params"] == []


def test_equals_base_compartment_definition(fhir_conn):  # noqa: F811
    compdef = json.loads(_read_file(fhir_conn, COMPDEF))
    want = {r["code"]: sorted(r.get("param", [])) for r in compdef["resource"]}
    with_params = {t for t, p in want.items() if p}
    assert len(with_params) == 66
    got = {t: _params(fhir_conn, t)["params"] for t in want}
    diffs = {t: (p, got[t]) for t, p in want.items() if got[t] != p}
    assert not diffs, diffs
