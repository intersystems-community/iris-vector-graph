"""
Generic Core DataLoaders for IRIS Vector Graph API

DataLoaders implement batch loading and caching to prevent N+1 queries.
These loaders work with the generic NodePK schema and can be used
across all domains.
"""

from strawberry.dataloader import DataLoader
from typing import List, Dict, Any, Optional


class GenericNodeLoader(DataLoader):
    """Batch load nodes by ID including labels and properties"""

    def __init__(self, engine: Any) -> None:
        self.engine = engine
        super().__init__(load_fn=self.batch_load_fn)

    async def batch_load_fn(self, keys: List[str]) -> List[Optional[Dict[str, Any]]]:
        """Batch load nodes using engine.get_nodes for efficient batching"""
        if not keys:
            return []

        nodes_data = self.engine.get_nodes(keys)
        
        result_map = {n["id"]: n for n in nodes_data if n}
        return [result_map.get(key) for key in keys]
