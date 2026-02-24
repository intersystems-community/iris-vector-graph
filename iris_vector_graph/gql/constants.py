from typing import Dict, Type

RESERVED_KEYWORDS = {
    "id", "labels", "properties", "outgoing", "incoming", "neighbors", 
    "__typename", "schema", "query", "mutation"
}

EXCLUDED_PROPERTIES = {"emb", "vector", "embedding"}

# Registry of dynamic types to avoid circular imports and enable lookup
DYNAMIC_TYPES: Dict[str, Type] = {}
