"""Spec 167 unit test — build_graph_json_serverside parses server-side JSON."""
from __future__ import annotations
from unittest.mock import MagicMock
import json


class TestBuildGraphJsonServerside:
    def test_parses_correctly(self):
        """T001 / FR-167-003 — serverside JSON parsed into nodes+edges dict."""
        from iris_vector_graph.stores.arno_bridge import build_graph_json_serverside

        sample = {"nodes": ["A", "B", "C", "D", "E"], "edges": [
            {"s": "A", "d": "B"}, {"s": "B", "d": "C"},
            {"s": "C", "d": "D"}, {"s": "D", "d": "E"},
        ]}
        conn = MagicMock()
        cursor = MagicMock()
        # Sequence: build returns status, then 1 chunk call
        cursor.fetchone.side_effect = [
            ("OK:5:4:1",),
            (json.dumps(sample),),
        ]
        conn.cursor.return_value = cursor

        result = build_graph_json_serverside(conn)

        assert result["nodes"] == ["A", "B", "C", "D", "E"]
        assert len(result["edges"]) == 4
        assert result["edges"][0] == {"s": "A", "d": "B"}
