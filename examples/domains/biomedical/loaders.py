"""
Biomedical Domain DataLoaders

Domain-specific DataLoaders for biomedical entities (Protein, Gene, Pathway).
These loaders extend the core DataLoader pattern with biomedical-specific
label filtering and property mapping.
"""

from strawberry.dataloader import DataLoader
from typing import List, Optional, Dict, Any
from datetime import datetime

from api.gql.core.loaders import PropertyLoader, LabelLoader


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

        from iris_vector_graph.engine import IRISGraphEngine
        engine = IRISGraphEngine(self.db)

        placeholders = ",".join(["?" for _ in keys])
        result = engine.execute_cypher(f"""
            MATCH (n:Protein)
            WHERE n.node_id IN ({placeholders})
            RETURN n.node_id
        """, keys)

        existing_ids = {row[0] for row in result.rows} if result.rows else set()

        protein_dict: Dict[str, Dict[str, Any]] = {}
        for protein_id in existing_ids:
            node_result = engine.get_node(protein_id)
            if node_result:
                props = node_result.get("properties", {})
                protein_dict[protein_id] = {
                    "id": protein_id,
                    "labels": ["Protein"],
                    "properties": props,
                    "created_at": node_result.get("created_at"),
                    "name": props.get("name", ""),
                    "function": props.get("function"),
                    "organism": props.get("organism"),
                    "confidence": float(props["confidence"]) if "confidence" in props else None
                }

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

        from iris_vector_graph.engine import IRISGraphEngine
        engine = IRISGraphEngine(self.db)

        placeholders = ",".join(["?" for _ in keys])
        result = engine.execute_cypher(f"""
            MATCH (n:Gene)
            WHERE n.node_id IN ({placeholders})
            RETURN n.node_id
        """, keys)

        existing_ids = {row[0] for row in result.rows} if result.rows else set()

        gene_dict: Dict[str, Dict[str, Any]] = {}
        for gene_id in existing_ids:
            node_result = engine.get_node(gene_id)
            if node_result:
                props = node_result.get("properties", {})
                gene_dict[gene_id] = {
                    "id": gene_id,
                    "labels": ["Gene"],
                    "properties": props,
                    "created_at": node_result.get("created_at"),
                    "name": props.get("name", ""),
                    "chromosome": props.get("chromosome"),
                    "position": int(props["position"]) if "position" in props else None
                }

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

        from iris_vector_graph.engine import IRISGraphEngine
        engine = IRISGraphEngine(self.db)

        placeholders = ",".join(["?" for _ in keys])
        result = engine.execute_cypher(f"""
            MATCH (n:Pathway)
            WHERE n.node_id IN ({placeholders})
            RETURN n.node_id
        """, keys)

        existing_ids = {row[0] for row in result.rows} if result.rows else set()

        pathway_dict: Dict[str, Dict[str, Any]] = {}
        for pathway_id in existing_ids:
            node_result = engine.get_node(pathway_id)
            if node_result:
                props = node_result.get("properties", {})
                pathway_dict[pathway_id] = {
                    "id": pathway_id,
                    "labels": ["Pathway"],
                    "properties": props,
                    "created_at": node_result.get("created_at"),
                    "name": props.get("name", ""),
                    "description": props.get("description")
                }

        return [pathway_dict.get(key) for key in keys]
