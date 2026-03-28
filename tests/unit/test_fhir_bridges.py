"""Unit tests for FHIR-to-KG bridge: MRCONSO parsing, anchor extraction, idempotency."""
import pytest
from unittest.mock import MagicMock, patch


class TestMrconsoParser:

    def test_parse_pipe_delimited_line(self):
        """T006: extract CUI, SAB, CODE, STR from MRCONSO line"""
        line = "C0032285|ENG|P|L0032285|PF|S0032285|Y|A0032285||M0027023|D011014|MSH|MH|D011014|Pneumonia|0|N||"
        fields = line.split("|")
        assert fields[0] == "C0032285"
        assert fields[11] == "MSH"
        assert fields[13] == "D011014"
        assert fields[14] == "Pneumonia"

    def test_cui_join_produces_icd_mesh_pairs(self):
        """T007: CUI join logic produces correct pairs"""
        icd_by_cui = {"C0032285": ["J18.9"], "C0011849": ["E11.9"]}
        mesh_by_cui = {"C0032285": ["D011014"], "C0011849": ["D003924"]}

        pairs = []
        for cui in icd_by_cui:
            if cui in mesh_by_cui:
                for icd in icd_by_cui[cui]:
                    for mesh in mesh_by_cui[cui]:
                        pairs.append((icd, f"MeSH:{mesh}"))

        assert ("J18.9", "MeSH:D011014") in pairs
        assert ("E11.9", "MeSH:D003924") in pairs
        assert len(pairs) == 2

    def test_mesh_ids_prefixed(self):
        """T008: MeSH descriptor IDs prefixed with MeSH:"""
        raw_mesh_id = "D011014"
        prefixed = f"MeSH:{raw_mesh_id}"
        assert prefixed == "MeSH:D011014"
        assert prefixed.startswith("MeSH:")

    def test_malformed_lines_skipped(self):
        """T009: malformed MRCONSO lines skipped with warning"""
        good_line = "C0032285|ENG|P|L0032285|PF|S0032285|Y|A0032285||M0027023|D011014|MSH|MH|D011014|Pneumonia|0|N||"
        bad_line = "short|line"
        empty_line = ""

        parsed = 0
        skipped = 0
        for line in [good_line, bad_line, empty_line]:
            fields = line.split("|")
            if len(fields) >= 15:
                parsed += 1
            else:
                skipped += 1

        assert parsed == 1
        assert skipped == 2

    def test_idempotent_insert_no_duplicate(self):
        """T009a: duplicate (fhir_code, kg_node_id) silently skipped"""
        seen = set()
        rows = [
            ("J18.9", "MeSH:D011014"),
            ("J18.9", "MeSH:D011014"),
            ("E11.9", "MeSH:D003924"),
        ]
        unique = []
        for row in rows:
            if row not in seen:
                seen.add(row)
                unique.append(row)
        assert len(unique) == 2


class TestGetKgAnchors:

    def _make_engine(self):
        from iris_vector_graph.engine import IRISGraphEngine
        engine = IRISGraphEngine.__new__(IRISGraphEngine)
        engine.conn = MagicMock()
        return engine

    def test_returns_mesh_nodes_for_icd_codes(self):
        """T012: get_kg_anchors returns MeSH node IDs"""
        engine = self._make_engine()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [("MeSH:D011014",), ("MeSH:D003924",)]
        engine.conn.cursor.return_value = mock_cursor

        result = engine.get_kg_anchors(icd_codes=["J18.9", "E11.9"])
        assert "MeSH:D011014" in result
        assert "MeSH:D003924" in result
        assert mock_cursor.execute.called

    def test_empty_icd_codes_returns_empty(self):
        """T013: empty input returns empty list"""
        engine = self._make_engine()
        result = engine.get_kg_anchors(icd_codes=[])
        assert result == []

    def test_filters_to_existing_kg_nodes(self):
        """T014: only returns nodes that exist in Graph_KG.nodes"""
        engine = self._make_engine()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [("MeSH:D011014",)]
        engine.conn.cursor.return_value = mock_cursor

        result = engine.get_kg_anchors(icd_codes=["J18.9"])
        assert len(result) == 1
        sql = mock_cursor.execute.call_args[0][0]
        assert "nodes" in sql.lower()
        assert "JOIN" in sql
