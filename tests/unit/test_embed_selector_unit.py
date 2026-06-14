"""
Tests for embed_selector.py — targeting miss lines:
- Lines 13-14: _PYDANTIC = False path (EmbedSelector without pydantic)
- Lines 35-56: non-pydantic EmbedSelector class
"""
import pytest
from unittest.mock import patch


class TestEmbedSelectorNoPydantic:
    """Test the fallback EmbedSelector when pydantic is not available."""

    def _get_selector_class(self):
        """Import EmbedSelector from a module where _PYDANTIC is False."""
        import importlib
        import sys
        # Save original module
        mod_name = "iris_vector_graph.embed_selector"
        if mod_name in sys.modules:
            del sys.modules[mod_name]
        with patch.dict("sys.modules", {"pydantic": None}):
            import iris_vector_graph.embed_selector as mod
            cls = mod.EmbedSelector
        # Restore
        if mod_name in sys.modules:
            del sys.modules[mod_name]
        return cls

    def test_basic_init_all_defaults(self):
        """Lines 36-56: EmbedSelector() default init."""
        cls = self._get_selector_class()
        sel = cls()
        assert sel.label is None
        assert sel.node_ids is None
        assert sel.predicate is None
        assert sel.source_label is None
        assert sel.target_label is None
        assert sel.exclude_pattern is None
        assert sel.missing_only is False

    def test_init_with_label(self):
        cls = self._get_selector_class()
        sel = cls(label="Gene")
        assert sel.label == "Gene"

    def test_init_with_node_ids(self):
        cls = self._get_selector_class()
        sel = cls(node_ids=["n1", "n2"])
        assert sel.node_ids == ["n1", "n2"]

    def test_init_with_exclude_pattern_valid(self):
        """Lines 46-49: valid exclude_pattern accepted."""
        cls = self._get_selector_class()
        sel = cls(exclude_pattern="GO:*")
        assert sel.exclude_pattern == "GO:*"

    def test_init_with_unsafe_exclude_pattern_raises(self):
        """Lines 46-49: unsafe exclude_pattern → ValueError."""
        cls = self._get_selector_class()
        with pytest.raises(ValueError, match="Unsafe"):
            cls(exclude_pattern="GO:*; DROP TABLE")

    def test_init_with_dash_dash_raises(self):
        cls = self._get_selector_class()
        with pytest.raises(ValueError):
            cls(exclude_pattern="foo--bar")

    def test_init_with_exec_raises(self):
        cls = self._get_selector_class()
        with pytest.raises(ValueError):
            cls(exclude_pattern="fooEXECbar")

    def test_missing_only_true(self):
        cls = self._get_selector_class()
        sel = cls(missing_only=True)
        assert sel.missing_only is True


class TestEmbedSelectorWithPydantic:
    """Test EmbedSelector when pydantic IS available — covers validate path."""

    def test_valid_selector(self):
        try:
            from iris_vector_graph.embed_selector import EmbedSelector
            sel = EmbedSelector(label="Gene", missing_only=True)
            assert sel.label == "Gene"
        except ImportError:
            pytest.skip("pydantic not available")

    def test_unsafe_pattern_raises(self):
        try:
            from iris_vector_graph.embed_selector import EmbedSelector
            with pytest.raises(Exception):
                EmbedSelector(exclude_pattern="GO:*; DROP TABLE")
        except ImportError:
            pytest.skip("pydantic not available")


class TestBuildNodeWhere:

    def test_no_conditions(self):
        from iris_vector_graph.embed_selector import EmbedSelector, build_node_where
        sel = EmbedSelector()
        assert build_node_where(sel) == ""

    def test_with_label(self):
        from iris_vector_graph.embed_selector import EmbedSelector, build_node_where
        sel = EmbedSelector(label="Gene")
        result = build_node_where(sel)
        assert "Gene" in result

    def test_with_empty_node_ids(self):
        from iris_vector_graph.embed_selector import EmbedSelector, build_node_where
        sel = EmbedSelector(node_ids=[])
        result = build_node_where(sel)
        assert "1=0" in result

    def test_with_node_ids(self):
        from iris_vector_graph.embed_selector import EmbedSelector, build_node_where
        sel = EmbedSelector(node_ids=["n1", "n2"])
        result = build_node_where(sel)
        assert "n1" in result

    def test_with_exclude_pattern(self):
        from iris_vector_graph.embed_selector import EmbedSelector, build_node_where
        sel = EmbedSelector(exclude_pattern="GO:*")
        result = build_node_where(sel)
        assert "NOT LIKE" in result

    def test_with_missing_only(self):
        from iris_vector_graph.embed_selector import EmbedSelector, build_node_where
        sel = EmbedSelector(missing_only=True)
        result = build_node_where(sel)
        assert "NOT IN" in result


class TestBuildEdgeWhere:

    def test_no_conditions(self):
        from iris_vector_graph.embed_selector import EmbedSelector, build_edge_where
        sel = EmbedSelector()
        assert build_edge_where(sel) == ""

    def test_with_predicate(self):
        from iris_vector_graph.embed_selector import EmbedSelector, build_edge_where
        sel = EmbedSelector(predicate="TREATS")
        result = build_edge_where(sel)
        assert "TREATS" in result

    def test_with_source_label(self):
        from iris_vector_graph.embed_selector import EmbedSelector, build_edge_where
        sel = EmbedSelector(source_label="Gene")
        result = build_edge_where(sel)
        assert "Gene" in result

    def test_with_target_label(self):
        from iris_vector_graph.embed_selector import EmbedSelector, build_edge_where
        sel = EmbedSelector(target_label="Disease")
        result = build_edge_where(sel)
        assert "Disease" in result

    def test_with_exclude_pattern(self):
        from iris_vector_graph.embed_selector import EmbedSelector, build_edge_where
        sel = EmbedSelector(exclude_pattern="GO:*")
        result = build_edge_where(sel)
        assert "NOT LIKE" in result
