"""
Generic Core DataLoaders for IRIS Vector Graph API

DataLoaders implement batch loading and caching to prevent N+1 queries.
These loaders work with the generic NodePK schema and can be used
across all domains.

Architecture:
- EdgeLoader: Batch load edges by source node ID (from rdf_edges)
- PropertyLoader: Batch load properties by node ID (from rdf_props)
- LabelLoader: Batch load labels by node ID (from rdf_labels)

Performance: Reduces N+1 queries to ≤2 queries per nested GraphQL query.
"""

from strawberry.dataloader import DataLoader
from typing import List, Dict, Any


class EdgeLoader(DataLoader):
    """Batch load edges by source node ID"""

    def __init__(self, db_connection: Any) -> None:
        self.db = db_connection
        super().__init__(load_fn=self.batch_load_fn)

    async def batch_load_fn(self, keys: List[str]) -> List[List[Dict[str, Any]]]:
        """
        Batch load edges for given source node IDs using single SQL query.

        Args:
            keys: List of source node IDs

        Returns:
            List of lists, where each inner list contains edges for that source node
        """
        if not keys:
            return []

        cursor = self.db.cursor()

        # Batch query edges for all source nodes
        placeholders = ",".join(["?" for _ in keys])
        query = f"""
            SELECT s as source_id, p as type, o_id as target_id, qualifiers
            FROM rdf_edges
            WHERE s IN ({placeholders})
            ORDER BY s
        """

        cursor.execute(query, keys)
        rows = cursor.fetchall()

        # Group edges by source_id
        edges_by_source: Dict[str, List[Dict[str, Any]]] = {key: [] for key in keys}

        for row in rows:
            source_id = row[0]
            edge = {
                "source_id": source_id,
                "type": row[1],
                "target_id": row[2],
                "qualifiers": row[3] if len(row) > 3 else None
            }
            if source_id in edges_by_source:
                edges_by_source[source_id].append(edge)

        # Return in same order as keys
        return [edges_by_source[key] for key in keys]


class PropertyLoader(DataLoader):
    """Batch load properties by node ID"""

    def __init__(self, db_connection: Any) -> None:
        self.db = db_connection
        super().__init__(load_fn=self.batch_load_fn)

    async def batch_load_fn(self, keys: List[str]) -> List[Dict[str, str]]:
        """
        Batch load properties for given node IDs using single SQL query.

        Args:
            keys: List of node IDs

        Returns:
            List of dictionaries, where each dict contains key-value properties
        """
        if not keys:
            return []

        cursor = self.db.cursor()

        # Batch query properties for all nodes
        placeholders = ",".join(["?" for _ in keys])
        query = f"""
            SELECT s as node_id, key, val as value
            FROM rdf_props
            WHERE s IN ({placeholders})
            ORDER BY s
        """

        cursor.execute(query, keys)
        rows = cursor.fetchall()

        # Group properties by node_id
        props_by_node: Dict[str, Dict[str, str]] = {key: {} for key in keys}

        for row in rows:
            node_id = row[0]
            key = row[1]
            value = row[2]
            if node_id in props_by_node:
                props_by_node[node_id][key] = value

        # Return in same order as keys
        return [props_by_node[key] for key in keys]


class LabelLoader(DataLoader):
    """Batch load labels by node ID"""

    def __init__(self, db_connection: Any) -> None:
        self.db = db_connection
        super().__init__(load_fn=self.batch_load_fn)

    async def batch_load_fn(self, keys: List[str]) -> List[List[str]]:
        """
        Batch load labels for given node IDs using single SQL query.

        Args:
            keys: List of node IDs

        Returns:
            List of lists, where each inner list contains labels for that node
        """
        if not keys:
            return []

        cursor = self.db.cursor()

        # Batch query labels for all nodes
        placeholders = ",".join(["?" for _ in keys])
        query = f"""
            SELECT s as node_id, label
            FROM rdf_labels
            WHERE s IN ({placeholders})
            ORDER BY s
        """

        cursor.execute(query, keys)
        rows = cursor.fetchall()

        # Group labels by node_id
        labels_by_node: Dict[str, List[str]] = {key: [] for key in keys}

        for row in rows:
            node_id = row[0]
            label = row[1]
            if node_id in labels_by_node:
                labels_by_node[node_id].append(label)

        # Return in same order as keys
        return [labels_by_node[key] for key in keys]
