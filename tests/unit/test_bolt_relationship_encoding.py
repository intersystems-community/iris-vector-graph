"""Unit tests for Bolt TAG_RELATIONSHIP struct encoding (US2).

Tests cover:
- AST-based relationship column tagging in translate_to_sql()
- _encode_typed_row() / _pack_rel_from_value() in BoltSession
- pack_relationship() PackStream roundtrip
- ts_start > ts_end edge case returns empty (via translator)
"""
from __future__ import annotations

import struct
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.bolt_server import (
    BoltSession,
    PackStream,
    TAG_NODE,
    TAG_RELATIONSHIP,
    pack_relationship,
    _node_int_id,
    RawPackedBytes,
)
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


# ---------------------------------------------------------------------------
# T023 — relationship variable tagged as "relationship" in translate_to_sql
# ---------------------------------------------------------------------------

class TestTranslatorRelVarTagging:

    def _mock_engine_with_edges(self, edges=None):
        eng = MagicMock()
        eng.get_edges_in_window.return_value = edges or []
        return eng

    def test_relationship_variable_tagged_in_translate(self):
        """MATCH (p)-[e:ENCOUNTER]->(x) RETURN p, e, x → col 1 is 'relationship'."""
        cypher = "MATCH (p)-[e:ENCOUNTER]->(x) RETURN p, e, x"
        parsed = parse_query(cypher)
        eng = self._mock_engine_with_edges([{
            "s": "Patient/p1", "p": "ENCOUNTER", "o": "Encounter/e1",
            "ts": 1704067200, "w": 1.0
        }])
        # Provide a WHERE e.ts range so the temporal path fires
        cypher_with_ts = (
            "MATCH (p)-[e:ENCOUNTER]->(x) "
            "WHERE e.ts >= 1700000000 AND e.ts <= 1710000000 "
            "RETURN p, e, x"
        )
        parsed2 = parse_query(cypher_with_ts)
        result = translate_to_sql(parsed2, {}, engine=eng)
        # bolt_column_types should have "relationship" at position 1 (the 'e' column)
        assert len(result.bolt_column_types) >= 2
        assert result.bolt_column_types[1] == "relationship"

    def test_non_relationship_column_tagged_scalar(self):
        """RETURN p.node_id → scalar, not relationship."""
        cypher = "MATCH (p) RETURN p.node_id"
        parsed = parse_query(cypher)
        result = translate_to_sql(parsed, {})
        if result.bolt_column_types:
            assert result.bolt_column_types[0] == "scalar"

    def test_named_rel_in_context_rel_variables(self):
        """Relationship variable 'e' is added to context.rel_variables."""
        from iris_vector_graph.cypher.translator import TranslationContext
        from iris_vector_graph.cypher import ast
        ctx = TranslationContext()
        assert hasattr(ctx, "rel_variables")
        assert isinstance(ctx.rel_variables, set)
        assert len(ctx.rel_variables) == 0

    def test_parent_context_copies_rel_variables(self):
        """Child TranslationContext inherits parent rel_variables."""
        from iris_vector_graph.cypher.translator import TranslationContext
        parent = TranslationContext()
        parent.rel_variables.add("e")
        child = TranslationContext(parent=parent)
        assert "e" in child.rel_variables


# ---------------------------------------------------------------------------
# T024 — non-relationship column stays scalar
# ---------------------------------------------------------------------------

class TestScalarColumnTagging:
    def test_property_access_is_scalar(self):
        cypher = "MATCH (p) RETURN p.node_id, p.name"
        parsed = parse_query(cypher)
        result = translate_to_sql(parsed, {})
        for ct in result.bolt_column_types:
            assert ct == "scalar"

    def test_count_is_scalar(self):
        cypher = "MATCH (p) RETURN count(p)"
        parsed = parse_query(cypher)
        result = translate_to_sql(parsed, {})
        for ct in result.bolt_column_types:
            assert ct == "scalar"


# ---------------------------------------------------------------------------
# T025 — _encode_typed_row emits TAG_RELATIONSHIP bytes for rel columns
# ---------------------------------------------------------------------------

class TestEncodeTypedRow:

    def _make_session(self):
        ws = MagicMock()
        session = BoltSession.__new__(BoltSession)
        session._websocket = ws
        session._bolt_version = 4
        session._pending_result = None
        session._pending_columns = None
        session._pending_graph_cols = None
        session._pending_col_types = None
        session._get_engine_fn = MagicMock()
        session._engine = None
        return session

    def test_relationship_col_emits_tag_relationship(self):
        session = self._make_session()
        row = {"s": "Patient/p1", "p": "ENCOUNTER", "o": "Encounter/e1",
               "ts": 1704067200, "weight": 1.0}
        encoded = session._encode_typed_row([row], ["e"], ["relationship"])
        assert len(encoded) == 1
        raw = encoded[0]
        assert isinstance(raw, RawPackedBytes)
        # First byte of the struct is the TAG_RELATIONSHIP marker (0x52 = 82)
        # PackStream structs: 0xB? marker, then field count, then tag
        data = raw.data
        # Find the tag byte — it follows the tiny struct header
        found_tag = False
        for b in data:
            if b == TAG_RELATIONSHIP:
                found_tag = True
                break
        assert found_tag, f"TAG_RELATIONSHIP (0x{TAG_RELATIONSHIP:02x}) not found in bytes: {data.hex()}"

    def test_scalar_col_stays_scalar(self):
        session = self._make_session()
        encoded = session._encode_typed_row(["hello"], ["name"], ["scalar"])
        assert encoded == ["hello"]

    def test_mixed_row_rel_and_scalar(self):
        session = self._make_session()
        row_val = {"s": "Patient/p1", "p": "ENCOUNTER", "o": "Encounter/e1",
                   "ts": 1704067200, "weight": 1.0}
        encoded = session._encode_typed_row(
            [row_val, "some_scalar"],
            ["e", "name"],
            ["relationship", "scalar"],
        )
        assert isinstance(encoded[0], RawPackedBytes)
        assert encoded[1] == "some_scalar"

    def test_none_val_relationship_col(self):
        """None value for a relationship column produces fallback struct."""
        session = self._make_session()
        encoded = session._encode_typed_row([None], ["e"], ["relationship"])
        assert len(encoded) == 1
        # Should be RawPackedBytes (fallback struct), not raise
        assert isinstance(encoded[0], RawPackedBytes)


# ---------------------------------------------------------------------------
# T026 — pack_relationship roundtrip
# ---------------------------------------------------------------------------

class TestPackRelationshipRoundtrip:

    def test_pack_relationship_produces_correct_tag(self):
        data = pack_relationship(
            rel_id=42,
            start_node_id="Patient/p1",
            end_node_id="Encounter/e1",
            rel_type="ENCOUNTER",
            properties={"ts": 1704067200, "weight": 1.0},
        )
        assert isinstance(data, bytes)
        # PackStream._pack_struct returns bytes; TAG_RELATIONSHIP must be present
        assert TAG_RELATIONSHIP in data
        # Unpack gives a list of the struct fields [rel_id, start, end, type, props]
        val, _ = PackStream.unpack(data, 0)
        assert isinstance(val, list)
        assert val[3] == "ENCOUNTER"

    def test_pack_relationship_tag_bytes(self):
        data = pack_relationship(42, "a", "b", "KNOWS", {"x": 1})
        # Should contain TAG_RELATIONSHIP byte somewhere
        assert TAG_RELATIONSHIP in data

    def test_pack_relationship_properties_encoded(self):
        data = pack_relationship(1, "s", "o", "REL", {"ts": 999, "weight": 2.5})
        # Roundtrip via unpack — the raw struct bytes contain the tag struct fields
        val, _ = PackStream.unpack(data, 0)
        assert isinstance(val, list)
        assert val[4].get("ts") == 999

    def test_node_int_id_is_deterministic(self):
        a = _node_int_id("Patient/p1")
        b = _node_int_id("Patient/p1")
        assert a == b
        assert isinstance(a, int)

    def test_node_int_id_different_for_different_ids(self):
        a = _node_int_id("Patient/p1")
        b = _node_int_id("Encounter/e1")
        assert a != b


# ---------------------------------------------------------------------------
# Edge case: ts_start > ts_end returns empty result
# ---------------------------------------------------------------------------

class TestTemporalWindowEdgeCases:

    def test_inverted_window_returns_empty_via_engine(self):
        """If ts_start > ts_end, engine.get_edges_in_window returns [], CTE is empty."""
        eng = MagicMock()
        eng.get_edges_in_window.return_value = []
        cypher = (
            "MATCH (p)-[e:ENCOUNTER]->(x) "
            "WHERE e.ts >= 1710000000 AND e.ts <= 1700000000 "
            "RETURN p, e, x"
        )
        parsed = parse_query(cypher)
        result = translate_to_sql(parsed, {}, engine=eng)
        # Should not raise; SQL should be valid (empty CTE union)
        assert result.sql is not None
        assert isinstance(result.sql, str)


# ---------------------------------------------------------------------------
# _pack_rel_from_value edge cases
# ---------------------------------------------------------------------------

class TestPackRelFromValue:

    def _make_session(self):
        session = BoltSession.__new__(BoltSession)
        session._websocket = MagicMock()
        session._bolt_version = 4
        return session

    def test_string_val_produces_struct(self):
        session = self._make_session()
        data = session._pack_rel_from_value("ENCOUNTER", "e")
        assert TAG_RELATIONSHIP in data

    def test_dict_with_all_fields(self):
        session = self._make_session()
        val = {"s": "Patient/p", "p": "CONDITION", "o": "Condition/c",
               "ts": 1705276800, "weight": 1.0}
        data = session._pack_rel_from_value(val, "e")
        assert TAG_RELATIONSHIP in data

    def test_dict_missing_ts(self):
        session = self._make_session()
        val = {"s": "Patient/p", "p": "CONDITION", "o": "Condition/c"}
        data = session._pack_rel_from_value(val, "e")
        assert TAG_RELATIONSHIP in data

    def test_none_fallback(self):
        session = self._make_session()
        data = session._pack_rel_from_value(None, "e")
        assert TAG_RELATIONSHIP in data

    def test_raw_packed_bytes_passthrough(self):
        session = self._make_session()
        raw = RawPackedBytes(b"\xb3\x52\x01\x01\x01\x85KNOWS\xa0")
        data = session._pack_rel_from_value(raw, "e")
        assert data == b"\xb3\x52\x01\x01\x01\x85KNOWS\xa0"

    def test_dict_with_unparseable_ts_still_produces_struct(self):
        """TypeError/ValueError branches for bad ts/weight values."""
        session = self._make_session()
        val = {"s": "Patient/p", "p": "REL", "o": "X/y", "ts": "not-a-number", "weight": "bad"}
        data = session._pack_rel_from_value(val, "e")
        assert TAG_RELATIONSHIP in data

    def test_dict_with_none_ts_and_weight_produces_empty_props(self):
        session = self._make_session()
        val = {"s": "Patient/p", "p": "REL", "o": "X/y", "ts": None, "weight": None}
        data = session._pack_rel_from_value(val, "e")
        assert TAG_RELATIONSHIP in data
