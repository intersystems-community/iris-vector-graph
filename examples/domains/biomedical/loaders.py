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
