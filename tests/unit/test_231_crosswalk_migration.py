"""Spec 231 FR-015, US4: fhir_bridges rows move into code_crosswalk.

Every bridge becomes a `related` crosswalk row in the default graph with
source 'fhir_bridges' and source_version = bridge_type; the short system names the
bridges table uses become the FHIR system URIs the token tables hold. The upsert
makes the migration re-runnable. Until 5.0 a bridge written through the API is
written to both tables.
"""

from __future__ import annotations

import warnings
from unittest.mock import MagicMock

import pytest

from iris_vector_graph.engine import IRISGraphEngine


@pytest.fixture
def eng():
    conn = MagicMock()
    engine = IRISGraphEngine(conn, embedding_dimension=4)
    return engine, conn.cursor.return_value


def test_system_uri_mapping():
    from iris_vector_graph._engine.fhir_graph import code_system_uri

    assert code_system_uri("ICD10CM") == "http://hl7.org/fhir/sid/icd-10-cm"
    assert code_system_uri("icd10cm") == "http://hl7.org/fhir/sid/icd-10-cm"
    assert code_system_uri("SNOMEDCT") == "http://snomed.info/sct"
    assert code_system_uri("LOINC") == "http://loinc.org"
    assert code_system_uri("RXNORM") == "http://www.nlm.nih.gov/research/umls/rxnorm"
    # Already a URI, or unknown: kept as is rather than guessed.
    assert code_system_uri("http://example.org/cs") == "http://example.org/cs"
    assert code_system_uri("LOCAL") == "LOCAL"


def test_migration_copies_every_row_as_related(eng):
    engine, cur = eng
    cur.fetchall.return_value = [
        ("E11.9", "MESH:D003924", "ICD10CM", "icd10_to_mesh", 1.0),
        ("I10", "MESH:D006973", "ICD10CM", "icd10_to_mesh", 0.8),
    ]
    out = engine.migrate_fhir_bridges_to_crosswalk()
    assert out == {"migrated": 2}
    upserts = [c for c in cur.execute.call_args_list if "INSERT OR UPDATE" in c.args[0]]
    assert len(upserts) == 2
    assert upserts[0].args[1] == [
        "http://hl7.org/fhir/sid/icd-10-cm", "E11.9", "", "MESH:D003924",
        "related", "fhir_bridges", "icd10_to_mesh", 1.0,
    ]


def test_migration_is_an_upsert(eng):
    """Re-running writes the same keys again rather than failing on the PK."""
    engine, cur = eng
    cur.fetchall.return_value = [("E11.9", "MESH:D003924", "ICD10CM", "icd10_to_mesh", 1.0)]
    engine.migrate_fhir_bridges_to_crosswalk()
    engine.migrate_fhir_bridges_to_crosswalk()
    upserts = [c for c in cur.execute.call_args_list if "INSERT OR UPDATE" in c.args[0]]
    assert len(upserts) == 2 and upserts[0].args == upserts[1].args


def test_bridge_add_writes_through_and_warns(eng):
    engine, cur = eng
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        engine.fhir_bridge_add("E11.9", "MESH:D003924")
    assert any(issubclass(x.category, DeprecationWarning) for x in w)
    sqls = [c.args[0] for c in cur.execute.call_args_list]
    assert any("Graph_KG.fhir_bridges" in s for s in sqls)
    assert any("Graph_KG.code_crosswalk" in s for s in sqls)


def test_get_kg_anchors_warns(eng):
    engine, cur = eng
    cur.fetchall.return_value = []
    with pytest.warns(DeprecationWarning):
        engine.get_kg_anchors(["E11.9"])
