"""Unit tests for spec 219 — get_edges_in_window docstring documents attrs limitation."""


class TestAttrsDocumentation:

    def test_get_edges_in_window_docstring_documents_attrs(self):
        """T001: get_edges_in_window docstring mentions attrs and get_edge_attrs."""
        from iris_vector_graph._engine.temporal import TemporalMixin
        doc = TemporalMixin.get_edges_in_window.__doc__ or ""
        assert "attrs" in doc.lower() or "get_edge_attrs" in doc, (
            "get_edges_in_window docstring must document the attrs limitation"
        )

    def test_get_edge_attrs_docstring_present(self):
        """T003: get_edge_attrs docstring is meaningful."""
        from iris_vector_graph._engine.temporal import TemporalMixin
        doc = TemporalMixin.get_edge_attrs.__doc__ or ""
        assert len(doc.strip()) > 20, "get_edge_attrs should have a substantive docstring"
