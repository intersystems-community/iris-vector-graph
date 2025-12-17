"""
IRIS Graph Core - Domain-Agnostic Graph Engine

A high-performance, domain-agnostic graph engine for InterSystems IRIS with:
- HNSW-optimized vector search (50ms performance)
- Native IRIS iFind text search integration
- Reciprocal Rank Fusion (RRF) for hybrid ranking
- JSON_TABLE confidence filtering
- Multi-modal graph-vector-text fusion

This core module can be integrated into any RAG system requiring advanced
graph and hybrid search capabilities.
"""

from .engine import IRISGraphEngine
from .schema import GraphSchema
from .vector_utils import VectorOptimizer
from .text_search import TextSearchEngine
from .fusion import RRFFusion

__version__ = "1.0.0"
__all__ = [
    "IRISGraphEngine",
    "GraphSchema",
    "VectorOptimizer",
    "TextSearchEngine",
    "RRFFusion"
]