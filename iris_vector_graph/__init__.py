"""
IRIS Vector Graph - Knowledge graph and vector search platform on InterSystems IRIS.

This package provides a convenient top-level namespace that re-exports the core functionality
from iris_vector_graph_core for easier imports.

Usage:
    from iris_vector_graph import IRISGraphEngine

    # Or use the core module directly:
    from iris_vector_graph_core import IRISGraphEngine
"""

# Re-export all public APIs from iris_vector_graph_core
from iris_vector_graph_core import *
from iris_vector_graph_core.engine import IRISGraphEngine
from iris_vector_graph_core.fusion import HybridSearchFusion
from iris_vector_graph_core.text_search import TextSearchEngine
from iris_vector_graph_core.vector_utils import VectorOptimizer

# Version info
__version__ = "1.1.0"
__all__ = [
    "IRISGraphEngine",
    "HybridSearchFusion",
    "TextSearchEngine",
    "VectorOptimizer",
]
