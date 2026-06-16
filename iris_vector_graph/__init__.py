"""
IRIS Graph Core - Domain-Agnostic Graph Engine
"""

from .engine import IRISGraphEngine
from .status import EngineStatus
from .schema import GraphSchema
from .index_protocol import IVGIndex, IndexHandle, Index
from .index_config import (
    IndexConfig, VectorIndexConfig, FulltextIndexConfig,
    MultiVectorIndexConfig, NeighborhoodVectorConfig, HNSWIndexConfig,
)
from .result import IVGResult
from .capabilities import IRISCapabilities
from .store_protocol import GraphStore
from .stores import IRISGraphStore
from .cypher.aql import translate_aql, AQLParseError, AQLTranslationError
from .sdk import IVGClient, AsyncIVGClient, IVGRecord, IVGError, IVGClientError, IVGServerError
from .embed_selector import EmbedSelector
from .errors import (
    PrerequisiteError, IndexNotFoundError, IndexNotBuiltError,
    EmbeddingsMissingError, IndexNotSyncedError, NodeNotFoundError,
)
from ._validate import (
    NodeIdInput, EdgeInput, CypherInput,
    IVFBuildInput, VectorSearchInput,
    BM25BuildInput, BM25SearchInput,
    KHop2Input, TemporalEdgeInput, VecSearchInput,
    DegreeCentralityInput, BetweennessInput, ClosenessInput, EigenvectorInput,
    LeidenInput, TriangleCountInput, SCCInput, KCoreInput,
)
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
from .fhir_bridge import (
    get_kg_anchors,
    extract_icd_codes,
    fhir_search_conditions,
    unified_clinical_pipeline,
    FHIRSearchTool,
    GetPatientKGNeighborhoodTool,
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
    "Index",
    "IndexConfig",
    "VectorIndexConfig",
    "FulltextIndexConfig",
    "MultiVectorIndexConfig",
    "NeighborhoodVectorConfig",
    "HNSWIndexConfig",
    "IVGResult",
    "NodeIdInput",
    "EdgeInput",
    "CypherInput",
    "IVFBuildInput",
    "VectorSearchInput",
    "BM25BuildInput",
    "BM25SearchInput",
    "KHop2Input",
    "TemporalEdgeInput",
    "VecSearchInput",
    "get_kg_anchors",
    "extract_icd_codes",
    "fhir_search_conditions",
    "unified_clinical_pipeline",
    "FHIRSearchTool",
    "GetPatientKGNeighborhoodTool",
    "GraphStore",
    "IRISGraphStore",
    "translate_aql",
    "AQLParseError",
    "AQLTranslationError",
    "IVGClient",
    "AsyncIVGClient",
    "IVGRecord",
    "IVGError",
    "IVGClientError",
    "IVGServerError",
    "EmbedSelector",
    "PrerequisiteError",
    "IndexNotFoundError",
    "IndexNotBuiltError",
    "EmbeddingsMissingError",
    "IndexNotSyncedError",
    "NodeNotFoundError",
    "ApiKeyMiddleware",
    "ReadOnlyMiddleware",
    "is_mutation_cypher",
]