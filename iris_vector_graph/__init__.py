"""
IRIS Graph Core - Domain-Agnostic Graph Engine
"""

from ._validate import (
    BetweennessInput,
    BM25BuildInput,
    BM25SearchInput,
    ClosenessInput,
    CypherInput,
    DegreeCentralityInput,
    EdgeInput,
    EigenvectorInput,
    IVFBuildInput,
    KCoreInput,
    KHop2Input,
    LeidenInput,
    NodeIdInput,
    SCCInput,
    TemporalEdgeInput,
    TriangleCountInput,
    VecSearchInput,
    VectorSearchInput,
)
from .capabilities import IRISCapabilities
from .cypher.aql import AQLParseError, AQLTranslationError, translate_aql
from .dbapi_utils import (
    create_hnsw_index,
    create_ivfflat_index,
    insert_vector,
    normalize_vector,
    vector_similarity_search,
)
from .embed_selector import EmbedSelector
from .engine import IRISGraphEngine

# Spec 227's two route-shaped results. Re-exported from `_engine` — where the mixins
# that produce them live — so a caller can annotate what `resolve_route` and
# `embedding_inventory` return without importing a private module.
from ._engine.embeddings import EmbeddingRoute
from ._engine.schema import EmbeddingInventoryRow, QuarantinedVector
from .errors import (
    EmbeddingsMissingError,
    IndexNotBuiltError,
    IndexNotFoundError,
    IndexNotSyncedError,
    NodeNotFoundError,
    PrerequisiteError,
)
from .embedding_identity import (
    MECHANISMS,
    EmbeddingIdentity,
    identity_from_config,
    normalize_model_key,
)
from .exceptions import (
    EmbeddingIdentityConflict,
    NamespaceConsistencyError,
    NamespaceMismatchWarning,
)
from .fhir_bridge import (
    FHIRSearchTool,
    GetPatientKGNeighborhoodTool,
    extract_icd_codes,
    fhir_search_conditions,
    get_kg_anchors,
    unified_clinical_pipeline,
)
from .fusion import RRFFusion
from .index_config import (
    FulltextIndexConfig,
    HNSWIndexConfig,
    IndexConfig,
    MultiVectorIndexConfig,
    NeighborhoodVectorConfig,
    VectorIndexConfig,
)
from .index_protocol import Index, IndexHandle, IVGIndex
from .result import IVGResult
from .schema import GraphSchema
from .sdk import AsyncIVGClient, IVGClient, IVGClientError, IVGError, IVGRecord, IVGServerError
from .status import EngineStatus
from .store_protocol import GraphStore
from .stores import IRISGraphStore
from .text_search import TextSearchEngine
from .vector_utils import VectorOptimizer

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
    "EmbeddingIdentity",
    "EmbeddingIdentityConflict",
    "EmbeddingInventoryRow",
    "EmbeddingRoute",
    "QuarantinedVector",
    "MECHANISMS",
    "identity_from_config",
    "normalize_model_key",
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
