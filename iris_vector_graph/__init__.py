"""
IRIS Graph Core - Domain-Agnostic Graph Engine
"""

from .engine import IRISGraphEngine
from .status import EngineStatus
from .schema import GraphSchema
from .index_protocol import IVGIndex, IndexHandle
from .result import IVGResult
from .capabilities import IRISCapabilities
from .vector_utils import VectorOptimizer
from .text_search import TextSearchEngine
from .fusion import RRFFusion
from .dbapi_utils import (
    normalize_vector,
    insert_vector,
    create_hnsw_index,
    create_ivfflat_index,
    vector_similarity_search,
)

try:
    from .embedded import EmbeddedConnection, EmbeddedCursor
except ImportError:
    pass

try:
    from importlib.metadata import version
    __version__ = version("iris-vector-graph")
except Exception:
    __version__ = "unknown"

__all__ = [
    "IRISGraphEngine",
    "GraphSchema",
    "IRISCapabilities",
    "VectorOptimizer",
    "TextSearchEngine",
    "RRFFusion",
    "EmbeddedConnection",
    "EmbeddedCursor",
    "IVGIndex",
    "IndexHandle",
    "IVGResult",
]