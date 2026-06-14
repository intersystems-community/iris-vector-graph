"""
GraphQL DataLoaders for IRIS Vector Graph API

DataLoaders implement batch loading and caching to prevent N+1 queries.
All loaders accept a raw IRIS db_connection.
"""

from strawberry.dataloader import DataLoader
from typing import List, Optional, Dict, Any


class ProteinLoader(DataLoader):
    """Batch load proteins by ID"""

    def __init__(self, db_connection: Any) -> None:
        self.db = db_connection
        super().__init__(load_fn=self.batch_load_fn)

    async def batch_load_fn(self, keys: List[str]) -> List[Optional[Dict[str, Any]]]:
        if not keys or self.db is None:
            return [None] * len(keys)

        cursor = self.db.cursor()
        placeholders = ",".join(["?" for _ in keys])
        props_by_node: Dict[str, Dict[str, str]] = {k: {} for k in keys}
        labels_by_node: Dict[str, List[str]] = {k: [] for k in keys}

        try:
            cursor.execute(
                f"SELECT s, key, val FROM Graph_KG.rdf_props WHERE s IN ({placeholders})",
                list(keys),
            )
            for row in cursor.fetchall():
                if row[0] in props_by_node:
                    props_by_node[row[0]][row[1]] = row[2]

            cursor.execute(
                f"SELECT s, label FROM Graph_KG.rdf_labels WHERE s IN ({placeholders})",
                list(keys),
            )
            for row in cursor.fetchall():
                if row[0] in labels_by_node:
                    labels_by_node[row[0]].append(row[1])
        except Exception:
            pass

        result = []
        for key in keys:
            props = props_by_node.get(key, {})
            labels = labels_by_node.get(key, [])
            if not props and not labels:
                result.append(None)
            else:
                result.append({
                    "id": key,
                    "labels": labels,
                    "properties": props,
                    "created_at": None,
                    "name": props.get("name", ""),
                    "function": props.get("function"),
                    "organism": props.get("organism"),
                    "confidence": float(props["confidence"]) if "confidence" in props and props["confidence"] else None,
                })
        return result


class GeneLoader(DataLoader):
    """Batch load genes by ID"""

    def __init__(self, db_connection: Any) -> None:
        self.db = db_connection
        super().__init__(load_fn=self.batch_load_fn)

    async def batch_load_fn(self, keys: List[str]) -> List[Optional[Dict[str, Any]]]:
        if not keys or self.db is None:
            return [None] * len(keys)

        cursor = self.db.cursor()
        placeholders = ",".join(["?" for _ in keys])
        props_by_node: Dict[str, Dict[str, str]] = {k: {} for k in keys}
        labels_by_node: Dict[str, List[str]] = {k: [] for k in keys}

        try:
            cursor.execute(
                f"SELECT s, key, val FROM Graph_KG.rdf_props WHERE s IN ({placeholders})",
                list(keys),
            )
            for row in cursor.fetchall():
                if row[0] in props_by_node:
                    props_by_node[row[0]][row[1]] = row[2]

            cursor.execute(
                f"SELECT s, label FROM Graph_KG.rdf_labels WHERE s IN ({placeholders})",
                list(keys),
            )
            for row in cursor.fetchall():
                if row[0] in labels_by_node:
                    labels_by_node[row[0]].append(row[1])
        except Exception:
            pass

        result = []
        for key in keys:
            props = props_by_node.get(key, {})
            labels = labels_by_node.get(key, [])
            if not props and not labels:
                result.append(None)
            else:
                result.append({
                    "id": key,
                    "labels": labels,
                    "properties": props,
                    "created_at": None,
                    "name": props.get("name", ""),
                    "chromosome": props.get("chromosome"),
                    "position": int(props["position"]) if "position" in props and props["position"] else None,
                })
        return result


class PathwayLoader(DataLoader):
    """Batch load pathways by ID"""

    def __init__(self, db_connection: Any) -> None:
        self.db = db_connection
        super().__init__(load_fn=self.batch_load_fn)

    async def batch_load_fn(self, keys: List[str]) -> List[Optional[Dict[str, Any]]]:
        if not keys or self.db is None:
            return [None] * len(keys)

        cursor = self.db.cursor()
        placeholders = ",".join(["?" for _ in keys])
        props_by_node: Dict[str, Dict[str, str]] = {k: {} for k in keys}
        labels_by_node: Dict[str, List[str]] = {k: [] for k in keys}

        try:
            cursor.execute(
                f"SELECT s, key, val FROM Graph_KG.rdf_props WHERE s IN ({placeholders})",
                list(keys),
            )
            for row in cursor.fetchall():
                if row[0] in props_by_node:
                    props_by_node[row[0]][row[1]] = row[2]

            cursor.execute(
                f"SELECT s, label FROM Graph_KG.rdf_labels WHERE s IN ({placeholders})",
                list(keys),
            )
            for row in cursor.fetchall():
                if row[0] in labels_by_node:
                    labels_by_node[row[0]].append(row[1])
        except Exception:
            pass

        result = []
        for key in keys:
            props = props_by_node.get(key, {})
            labels = labels_by_node.get(key, [])
            if not props and not labels:
                result.append(None)
            else:
                result.append({
                    "id": key,
                    "labels": labels,
                    "properties": props,
                    "created_at": None,
                    "name": props.get("name", ""),
                    "source": props.get("source"),
                    "external_id": props.get("external_id"),
                    "description": props.get("description"),
                })
        return result


class EdgeLoader(DataLoader):
    """Batch load edges by source node ID"""

    def __init__(self, db_connection: Any) -> None:
        self.db = db_connection
        super().__init__(load_fn=self.batch_load_fn)

    async def batch_load_fn(self, keys: List[str]) -> List[List[Dict[str, Any]]]:
        if not keys or self.db is None:
            return [[] for _ in keys]

        cursor = self.db.cursor()
        placeholders = ",".join(["?" for _ in keys])
        edges_by_source: Dict[str, List[Dict[str, Any]]] = {key: [] for key in keys}

        try:
            cursor.execute(
                f"SELECT s, p, o_id FROM Graph_KG.rdf_edges WHERE s IN ({placeholders})",
                list(keys),
            )
            seen: Dict[str, set] = {key: set() for key in keys}
            for row in cursor.fetchall():
                source_id = row[0]
                if source_id in edges_by_source:
                    dedup_key = (row[1], row[2])
                    if dedup_key not in seen[source_id]:
                        seen[source_id].add(dedup_key)
                        edges_by_source[source_id].append({
                            "source_id": source_id,
                            "type": row[1],
                            "target_id": row[2],
                            "qualifiers": None,
                        })
        except Exception:
            pass

        return [edges_by_source[key] for key in keys]


class PropertyLoader(DataLoader):
    """Batch load properties by node ID"""

    def __init__(self, db_connection: Any) -> None:
        self.db = db_connection
        super().__init__(load_fn=self.batch_load_fn)

    async def batch_load_fn(self, keys: List[str]) -> List[Dict[str, str]]:
        if not keys or self.db is None:
            return [{} for _ in keys]

        cursor = self.db.cursor()
        placeholders = ",".join(["?" for _ in keys])
        props_by_node: Dict[str, Dict[str, str]] = {key: {} for key in keys}

        try:
            cursor.execute(
                f"SELECT s, key, val FROM Graph_KG.rdf_props WHERE s IN ({placeholders})",
                list(keys),
            )
            for row in cursor.fetchall():
                node_id = row[0]
                if node_id in props_by_node:
                    props_by_node[node_id][row[1]] = row[2]
        except Exception:
            pass

        return [props_by_node[key] for key in keys]


class LabelLoader(DataLoader):
    """Batch load labels by node ID"""

    def __init__(self, db_connection: Any) -> None:
        self.db = db_connection
        super().__init__(load_fn=self.batch_load_fn)

    async def batch_load_fn(self, keys: List[str]) -> List[List[str]]:
        if not keys or self.db is None:
            return [[] for _ in keys]

        cursor = self.db.cursor()
        placeholders = ",".join(["?" for _ in keys])
        labels_by_node: Dict[str, List[str]] = {key: [] for key in keys}

        try:
            cursor.execute(
                f"SELECT s, label FROM Graph_KG.rdf_labels WHERE s IN ({placeholders})",
                list(keys),
            )
            for row in cursor.fetchall():
                node_id = row[0]
                if node_id in labels_by_node:
                    labels_by_node[node_id].append(row[1])
        except Exception:
            pass

        return [labels_by_node[key] for key in keys]
