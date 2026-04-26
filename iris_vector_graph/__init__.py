"""
IRIS Graph Core - Domain-Agnostic Graph Engine
"""

from .engine import IRISGraphEngine
from .status import EngineStatus
from .schema import GraphSchema
from .capabilities import IRISCapabilities
from .vector_utils import VectorOptimizer
from .text_search import TextSearchEngine
from .fusion import RRFFusion

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
]