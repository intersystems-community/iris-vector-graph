"""
Unit Tests for GraphQL DataLoaders

TDD gate tests — assert that DataLoader classes do not exist yet.
These will be replaced with real tests when DataLoaders are implemented (T016/T018).
"""

import pytest


class TestProteinLoader:
    def test_protein_loader_not_implemented_yet(self) -> None:
        """TDD gate: ProteinLoader must not exist until the feature is built."""
        with pytest.raises(ImportError):
            from api.graphql.loaders import ProteinLoader  # noqa: F401


class TestEdgeLoader:
    def test_edge_loader_not_implemented_yet(self) -> None:
        """TDD gate: EdgeLoader must not exist until the feature is built."""
        with pytest.raises(ImportError):
            from api.graphql.loaders import EdgeLoader  # noqa: F401


class TestPropertyLoader:
    def test_property_loader_not_implemented_yet(self) -> None:
        """TDD gate: PropertyLoader must not exist until the feature is built."""
        with pytest.raises(ImportError):
            from api.graphql.loaders import PropertyLoader  # noqa: F401


class TestLabelLoader:
    def test_label_loader_not_implemented_yet(self) -> None:
        """TDD gate: LabelLoader must not exist until the feature is built."""
        with pytest.raises(ImportError):
            from api.graphql.loaders import LabelLoader  # noqa: F401
