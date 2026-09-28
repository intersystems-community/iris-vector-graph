"""Spec 235 US5: the oracle's CATEGORY_LABEL_SYSTEMS equals FHIRGraph's CATEGORYSYSTEMS
parameter and research R8's six URIs."""

from __future__ import annotations

import json
import os
import re

from tests.e2e.interp_fixture import CATEGORY_LABEL_SYSTEMS

CLS = os.path.join(
    os.path.dirname(__file__), "..", "..", "iris_src", "src", "Graph", "KG", "FHIRGraph.cls"
)
R8 = (
    "http://terminology.hl7.org/CodeSystem/observation-category",
    "http://terminology.hl7.org/CodeSystem/condition-category",
    "http://hl7.org/fhir/us/core/CodeSystem/us-core-documentreference-category",
    "http://hl7.org/fhir/us/core/CodeSystem/careplan-category",
    "http://hl7.org/fhir/us/core/CodeSystem/condition-category",
    "http://hl7.org/fhir/us/core/CodeSystem/us-core-category",
)


def _parameter():
    with open(CLS) as fh:
        m = re.search(r"^Parameter CATEGORYSYSTEMS = (.+);$", fh.read(), re.M)
    assert m, "Parameter CATEGORYSYSTEMS missing"
    # The value is an ObjectScript string holding a JSON array; "" escapes ".
    return json.loads(m.group(1)[1:-1].replace('""', '"'))


def test_oracle_equals_r8():
    assert tuple(CATEGORY_LABEL_SYSTEMS) == R8


def test_class_equals_oracle():
    assert tuple(_parameter()) == tuple(CATEGORY_LABEL_SYSTEMS)
