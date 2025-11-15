"""
IRIS Vector Graph - Knowledge graph and vector search platform on InterSystems IRIS.

A high-performance graph engine combining:
- HNSW-optimized vector search (<10ms with ACORN-1)
- Native IRIS iFind text search integration
- Reciprocal Rank Fusion (RRF) for hybrid ranking
- Multi-modal graph-vector-text fusion
- Personalized PageRank (PPR) with ObjectScript optimization (8.9x faster at scale)

Proven at scale:
- Financial Services: 130M+ transactions, <10ms fraud scoring
- Biomedical Research: 100K+ proteins, <50ms multi-hop queries
"""

from .engine import IRISGraphEngine
from .schema import GraphSchema
from .vector_utils import VectorOptimizer
from .text_search import TextSearchEngine
from .fusion import RRFFusion, HybridSearchFusion

__version__ = "1.1.6"
__all__ = [
    "IRISGraphEngine",
    "GraphSchema",
    "VectorOptimizer",
    "TextSearchEngine",
    "RRFFusion",
    "HybridSearchFusion"
]