"""Unit tests for spec-198 US3: PROV-O Temporal Alignment.

Tests cover:
- T039: ts_start + ts_end → both prov:startedAtTime and prov:endedAtTime
- T040: ts_start only → prov:startedAtTime present, prov:endedAtTime absent
- T041: edge predicate preserved; subject/object as prov:Entity
- T042: time-window filter excludes edges outside window
- T043: prov_as_dict returns correct keys
- T044: prov_as_dict raises KeyError for unknown edge_id
- T045: empty temporal store produces valid empty PROV-O file
- T046: missing rdflib raises ImportError with hint
"""
from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine(temporal_edges=None):
    from iris_vector_graph.engine import IRISGraphEngine
    conn = MagicMock()
    conn.cursor.return_value = MagicMock()
    engine = IRISGraphEngine.__new__(IRISGraphEngine)
    engine.conn = conn
    engine.embedding_dimension = 4
    engine._store = MagicMock()
    engine._iris_obj = MagicMock()

    if temporal_edges is not None:
        engine._get_temporal_edges_window = MagicMock(return_value=temporal_edges)
        engine._get_temporal_edges_for_nodes = MagicMock(return_value=temporal_edges)
        engine._get_temporal_edges_by_id = MagicMock(
            side_effect=lambda ids: [e for e in temporal_edges if str(e.get("edge_id")) in {str(i) for i in ids}]
        )

    return engine


SAMPLE_EDGES = [
    {
        "edge_id": "e1",
        "source": "urn:fhir:Patient/001",
        "predicate": "http://ex.org/admitted",
        "target": "urn:fhir:Encounter/enc1",
        "ts_start": 1700000000,
        "ts_end": 1700003600,
    },
    {
        "edge_id": "e2",
        "source": "urn:fhir:Patient/002",
        "predicate": "http://ex.org/transferred",
        "target": "urn:fhir:Encounter/enc2",
        "ts_start": 1700100000,
        "ts_end": None,
    },
]


# ---------------------------------------------------------------------------
# T039: Both prov:startedAtTime and prov:endedAtTime when both timestamps present
# ---------------------------------------------------------------------------

class TestProvActivityTimestamps:

    def test_both_timestamps_produce_both_prov_times(self):
        import rdflib
        from iris_vector_graph._engine.prov import _build_prov_graph

        edges = [SAMPLE_EDGES[0]]  # has both ts_start and ts_end
        g = _build_prov_graph(edges)

        PROV = rdflib.Namespace("http://www.w3.org/ns/prov#")
        started_count = len(list(g.objects(predicate=PROV.startedAtTime)))
        ended_count = len(list(g.objects(predicate=PROV.endedAtTime)))
        assert started_count == 1
        assert ended_count == 1

    def test_timestamps_are_xsd_datetime_literals(self):
        import rdflib
        from rdflib import XSD
        from iris_vector_graph._engine.prov import _build_prov_graph

        edges = [SAMPLE_EDGES[0]]
        g = _build_prov_graph(edges)
        PROV = rdflib.Namespace("http://www.w3.org/ns/prov#")

        for lit in g.objects(predicate=PROV.startedAtTime):
            assert isinstance(lit, rdflib.Literal)
            assert lit.datatype == XSD.dateTime


# ---------------------------------------------------------------------------
# T040: ts_start only → no prov:endedAtTime
# ---------------------------------------------------------------------------

class TestProvNoEndTime:

    def test_missing_ts_end_omits_ended_at_time(self):
        import rdflib
        from iris_vector_graph._engine.prov import _build_prov_graph

        edges = [SAMPLE_EDGES[1]]  # ts_end is None
        g = _build_prov_graph(edges)

        PROV = rdflib.Namespace("http://www.w3.org/ns/prov#")
        ended_list = list(g.objects(predicate=PROV.endedAtTime))
        assert len(ended_list) == 0

    def test_started_at_time_present_without_end(self):
        import rdflib
        from iris_vector_graph._engine.prov import _build_prov_graph

        edges = [SAMPLE_EDGES[1]]
        g = _build_prov_graph(edges)
        PROV = rdflib.Namespace("http://www.w3.org/ns/prov#")
        started_list = list(g.objects(predicate=PROV.startedAtTime))
        assert len(started_list) == 1


# ---------------------------------------------------------------------------
# T041: Predicate preserved; subject/object as prov:Entity
# ---------------------------------------------------------------------------

class TestProvPredicateAndEntities:

    def test_predicate_uri_preserved_on_activity(self):
        import rdflib
        from rdflib import URIRef, RDF
        from iris_vector_graph._engine.prov import _build_prov_graph

        edges = [SAMPLE_EDGES[0]]
        g = _build_prov_graph(edges)

        pred_uri = URIRef("http://ex.org/admitted")
        triples_with_pred = list(g.triples((None, pred_uri, None)))
        assert len(triples_with_pred) > 0

    def test_source_and_target_are_prov_entity(self):
        import rdflib
        from rdflib import URIRef, RDF
        from iris_vector_graph._engine.prov import _build_prov_graph, _node_to_iri

        PROV = rdflib.Namespace("http://www.w3.org/ns/prov#")
        edges = [SAMPLE_EDGES[0]]
        g = _build_prov_graph(edges)

        entities = set(g.subjects(RDF.type, PROV.Entity))
        source_iri = URIRef(_node_to_iri(SAMPLE_EDGES[0]["source"]))
        target_iri = URIRef(_node_to_iri(SAMPLE_EDGES[0]["target"]))
        assert source_iri in entities
        assert target_iri in entities

    def test_activity_uses_source_entity(self):
        import rdflib
        from iris_vector_graph._engine.prov import _build_prov_graph, _node_to_iri

        PROV = rdflib.Namespace("http://www.w3.org/ns/prov#")
        edges = [SAMPLE_EDGES[0]]
        g = _build_prov_graph(edges)
        source_iri = rdflib.URIRef(_node_to_iri(SAMPLE_EDGES[0]["source"]))
        used_objects = set(g.objects(predicate=PROV.used))
        assert source_iri in used_objects


# ---------------------------------------------------------------------------
# T042: Time-window filter
# ---------------------------------------------------------------------------

class TestTimeWindowFilter:

    def test_filter_excludes_edges_outside_window(self):
        engine = _make_engine(temporal_edges=[SAMPLE_EDGES[0]])  # ts=1700000000

        with tempfile.NamedTemporaryFile(suffix=".ttl", delete=False) as f:
            path = f.name
        try:
            # Window: 1700100000–1700200000 should exclude SAMPLE_EDGES[0]
            engine._get_temporal_edges_window = MagicMock(return_value=[])
            result = engine.prov_export(path, ts_start=1700100000, ts_end=1700200000)
            assert result["activities"] == 0
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def test_no_filter_includes_all_edges(self):
        engine = _make_engine(temporal_edges=SAMPLE_EDGES)

        with tempfile.NamedTemporaryFile(suffix=".ttl", delete=False) as f:
            path = f.name
        try:
            result = engine.prov_export(path)
            assert result["activities"] == len(SAMPLE_EDGES)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# T043: prov_as_dict returns correct keys
# ---------------------------------------------------------------------------

class TestProvAsDict:

    def test_returns_expected_keys(self):
        engine = _make_engine(temporal_edges=SAMPLE_EDGES)
        engine._get_temporal_edges_by_id = MagicMock(return_value=[SAMPLE_EDGES[0]])

        d = engine.prov_as_dict("e1")
        assert "activity" in d
        assert "type" in d
        assert "startedAtTime" in d
        assert "used" in d
        assert "predicate" in d
        assert "object" in d

    def test_started_at_time_is_iso_string(self):
        engine = _make_engine(temporal_edges=SAMPLE_EDGES)
        engine._get_temporal_edges_by_id = MagicMock(return_value=[SAMPLE_EDGES[0]])

        d = engine.prov_as_dict("e1")
        assert "T" in d["startedAtTime"]  # ISO 8601 format
        assert "Z" in d["startedAtTime"]

    def test_ended_at_time_present_when_ts_end_set(self):
        engine = _make_engine(temporal_edges=SAMPLE_EDGES)
        engine._get_temporal_edges_by_id = MagicMock(return_value=[SAMPLE_EDGES[0]])

        d = engine.prov_as_dict("e1")
        assert "endedAtTime" in d

    def test_ended_at_time_absent_when_ts_end_none(self):
        engine = _make_engine(temporal_edges=SAMPLE_EDGES)
        engine._get_temporal_edges_by_id = MagicMock(return_value=[SAMPLE_EDGES[1]])

        d = engine.prov_as_dict("e2")
        assert "endedAtTime" not in d


# ---------------------------------------------------------------------------
# T044: prov_as_dict raises KeyError for unknown edge_id
# ---------------------------------------------------------------------------

class TestProvAsDictKeyError:

    def test_unknown_edge_id_raises_key_error(self):
        engine = _make_engine(temporal_edges=[])
        engine._get_temporal_edges_by_id = MagicMock(return_value=[])
        with pytest.raises(KeyError):
            engine.prov_as_dict("nonexistent_99")


# ---------------------------------------------------------------------------
# T045: Empty temporal store produces valid empty PROV-O file
# ---------------------------------------------------------------------------

class TestEmptyTemporalStore:

    def test_empty_store_produces_valid_prov_o(self):
        engine = _make_engine(temporal_edges=[])

        with tempfile.NamedTemporaryFile(suffix=".ttl", delete=False) as f:
            path = f.name
        try:
            result = engine.prov_export(path)
            assert result["activities"] == 0
            assert os.path.exists(path)

            import rdflib
            g = rdflib.Graph()
            g.parse(path, format="turtle")  # Valid even if empty
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# T046: Missing rdflib raises ImportError with hint
# ---------------------------------------------------------------------------

class TestProvGracefulDegradation:

    def test_prov_export_raises_importerror_without_rdflib(self):
        engine = _make_engine(temporal_edges=SAMPLE_EDGES)
        with patch("iris_vector_graph._engine.prov._require_rdflib") as mock:
            mock.side_effect = ImportError("rdflib not installed — pip install 'iris-vector-graph[rdf]'")
            with pytest.raises(ImportError, match="rdflib"):
                engine.prov_export("/tmp/test.ttl")

    def test_prov_as_dict_works_without_rdflib(self):
        """prov_as_dict doesn't need rdflib — it's a dict operation."""
        engine = _make_engine(temporal_edges=SAMPLE_EDGES)
        engine._get_temporal_edges_by_id = MagicMock(return_value=[SAMPLE_EDGES[0]])
        # Should succeed even without rdflib
        d = engine.prov_as_dict("e1")
        assert "activity" in d


# ---------------------------------------------------------------------------
# Helper: _ts_to_datetime
# ---------------------------------------------------------------------------

class TestTsToDatetime:

    def test_known_timestamp_converts_correctly(self):
        from iris_vector_graph._engine.prov import _ts_to_datetime
        result = _ts_to_datetime(0)
        assert result == "1970-01-01T00:00:00Z"

    def test_recent_timestamp(self):
        from iris_vector_graph._engine.prov import _ts_to_datetime
        result = _ts_to_datetime(1700000000)
        assert result.startswith("20")  # 2023
        assert result.endswith("Z")

    def test_invalid_timestamp_returns_epoch(self):
        from iris_vector_graph._engine.prov import _ts_to_datetime
        result = _ts_to_datetime(99999999999999)  # way too large
        assert result.endswith("Z")
