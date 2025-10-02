"""
GraphQL DataLoaders for IRIS Vector Graph API

DataLoaders implement batch loading and caching to prevent N+1 queries.
All loaders use SQL IN batching to fetch data for multiple keys in a single query.

Architecture:
- ProteinLoader: Batch load proteins by ID (from rdf_labels + rdf_props)
- EdgeLoader: Batch load edges by source node ID (from rdf_edges)
- PropertyLoader: Batch load properties by node ID (from rdf_props)
- LabelLoader: Batch load labels by node ID (from rdf_labels)

Performance: Reduces N+1 queries to ≤2 queries per nested GraphQL query.
"""

from strawberry.dataloader import DataLoader
from typing import List, Optional, Dict, Any
from datetime import datetime


class ProteinLoader(DataLoader):
    """Batch load proteins by ID"""

    def __init__(self, db_connection: Any) -> None:
        self.db = db_connection
        super().__init__(load_fn=self.batch_load_fn)

    async def batch_load_fn(self, keys: List[str]) -> List[Optional[Dict[str, Any]]]:
        """
        Batch load proteins for given IDs using single SQL query.

        Args:
            keys: List of protein IDs (e.g., ["PROTEIN:TP53", "PROTEIN:MDM2"])

        Returns:
            List of protein data dicts in same order as keys (None for missing IDs)
        """
        if not keys:
            return []

        cursor = self.db.cursor()

        # Query nodes with Protein label
        placeholders = ",".join(["?" for _ in keys])
        query = f"""
            SELECT DISTINCT l.s as id
            FROM rdf_labels l
            WHERE l.s IN ({placeholders})
              AND l.label = 'Protein'
        """

        cursor.execute(query, keys)
        rows = cursor.fetchall()

        # Create dict of existing protein IDs
        existing_ids = {row[0] for row in rows}

        # Load properties for all proteins in batch
        if existing_ids:
            property_loader = PropertyLoader(self.db)
            label_loader = LabelLoader(self.db)

            # Batch load properties and labels
            props_list = await property_loader.load_many(list(existing_ids))
            labels_list = await label_loader.load_many(list(existing_ids))

            # Build protein data dicts
            protein_dict: Dict[str, Dict[str, Any]] = {}
            for i, protein_id in enumerate(list(existing_ids)):
                props = props_list[i]
                labels = labels_list[i]

                protein_dict[protein_id] = {
                    "id": protein_id,
                    "labels": labels,
                    "properties": props,
                    "created_at": datetime.now(),  # TODO: Get from nodes.created_at
                    "name": props.get("name", ""),
                    "function": props.get("function"),
                    "organism": props.get("organism"),
                    "confidence": float(props["confidence"]) if "confidence" in props else None
                }
        else:
            protein_dict = {}

        # Return in same order as keys
        return [protein_dict.get(key) for key in keys]


class GeneLoader(DataLoader):
    """Batch load genes by ID"""

    def __init__(self, db_connection: Any) -> None:
        self.db = db_connection
        super().__init__(load_fn=self.batch_load_fn)

    async def batch_load_fn(self, keys: List[str]) -> List[Optional[Dict[str, Any]]]:
        """Batch load genes for given IDs using single SQL query"""
        if not keys:
            return []

        cursor = self.db.cursor()

        # Query nodes with Gene label
        placeholders = ",".join(["?" for _ in keys])
        query = f"""
            SELECT DISTINCT l.s as id
            FROM rdf_labels l
            WHERE l.s IN ({placeholders})
              AND l.label = 'Gene'
        """

        cursor.execute(query, keys)
        rows = cursor.fetchall()
        existing_ids = {row[0] for row in rows}

        # Load properties and labels for all genes in batch
        if existing_ids:
            property_loader = PropertyLoader(self.db)
            label_loader = LabelLoader(self.db)

            props_list = await property_loader.load_many(list(existing_ids))
            labels_list = await label_loader.load_many(list(existing_ids))

            gene_dict: Dict[str, Dict[str, Any]] = {}
            for i, gene_id in enumerate(list(existing_ids)):
                props = props_list[i]
                labels = labels_list[i]

                gene_dict[gene_id] = {
                    "id": gene_id,
                    "labels": labels,
                    "properties": props,
                    "created_at": datetime.now(),
                    "name": props.get("name", ""),
                    "chromosome": props.get("chromosome"),
                    "position": int(props["position"]) if "position" in props else None
                }
        else:
            gene_dict = {}

        return [gene_dict.get(key) for key in keys]


class PathwayLoader(DataLoader):
    """Batch load pathways by ID"""

    def __init__(self, db_connection: Any) -> None:
        self.db = db_connection
        super().__init__(load_fn=self.batch_load_fn)

    async def batch_load_fn(self, keys: List[str]) -> List[Optional[Dict[str, Any]]]:
        """Batch load pathways for given IDs using single SQL query"""
        if not keys:
            return []

        cursor = self.db.cursor()

        # Query nodes with Pathway label
        placeholders = ",".join(["?" for _ in keys])
        query = f"""
            SELECT DISTINCT l.s as id
            FROM rdf_labels l
            WHERE l.s IN ({placeholders})
              AND l.label = 'Pathway'
        """

        cursor.execute(query, keys)
        rows = cursor.fetchall()
        existing_ids = {row[0] for row in rows}

        # Load properties and labels for all pathways in batch
        if existing_ids:
            property_loader = PropertyLoader(self.db)
            label_loader = LabelLoader(self.db)

            props_list = await property_loader.load_many(list(existing_ids))
            labels_list = await label_loader.load_many(list(existing_ids))

            pathway_dict: Dict[str, Dict[str, Any]] = {}
            for i, pathway_id in enumerate(list(existing_ids)):
                props = props_list[i]
                labels = labels_list[i]

                pathway_dict[pathway_id] = {
                    "id": pathway_id,
                    "labels": labels,
                    "properties": props,
                    "created_at": datetime.now(),
                    "name": props.get("name", ""),
                    "description": props.get("description")
                }
        else:
            pathway_dict = {}

        return [pathway_dict.get(key) for key in keys]


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
