from typing import List, Optional, Any, Dict, Union
import strawberry
import json
from decimal import Decimal
from datetime import datetime, date
from .pooling import connection_context
from .constants import DYNAMIC_TYPES, RESERVED_KEYWORDS

async def resolve_node(info: strawberry.Info, id: strawberry.ID) -> Optional[Any]:
    """Resolves a single node by ID."""
    engine = info.context["engine"]
    node_data = engine.get_node(str(id))
    if not node_data:
        return None
    return map_node_data(node_data, info)

async def resolve_nodes(info: strawberry.Info, label: str, limit: int = 10, offset: int = 0) -> List[Any]:
    """Resolves nodes by label."""
    engine = info.context["engine"]
    conn = engine.conn
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT s FROM Graph_KG.rdf_labels WHERE label = ?", [label])
        all_ids = [row[0] for row in cursor.fetchall()]
        target_ids = all_ids[offset : offset + limit]
        if not target_ids:
            return []
        nodes_data = engine.get_nodes(target_ids)
        return [map_node_data(n, info) for n in nodes_data]
    finally:
        cursor.close()

async def resolve_semantic_search(
    info: strawberry.Info, 
    query: str, 
    label: Optional[str] = None, 
    limit: int = 5
) -> List[Any]:
    """Resolves semantic search results."""
    engine = info.context["engine"]
    try:
        results = engine.kg_KNN_VEC(query, k=limit, label_filter=label)
        from .schema import SemanticSearchResult
        node_ids = [r[0] for r in results]
        nodes_data = engine.get_nodes(node_ids)
        node_map = {n["id"]: n for n in nodes_data}
        output = []
        for node_id, score in results:
            if node_id in node_map:
                node_obj = map_node_data(node_map[node_id], info)
                output.append(SemanticSearchResult(score=score, node=node_obj))
        return output
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Semantic search failed: {e}")
        return []

async def resolve_outgoing(
    info: strawberry.Info, 
    root: Any, 
    predicate: Optional[str] = None, 
    limit: int = 10
) -> List[Any]:
    """Resolves outgoing relationships."""
    engine = info.context["engine"]
    conn = engine.conn
    cursor = conn.cursor()
    try:
        sql = "SELECT p, o_id FROM Graph_KG.rdf_edges WHERE s = ?"
        params = [str(root.id)]
        if predicate:
            sql += " AND p = ?"
            params.append(predicate)
        cursor.execute(sql, params)
        rows = cursor.fetchmany(limit)
        from .schema import Relationship
        return [Relationship(predicate=row[0], target_id=row[1]) for row in rows]
    finally:
        cursor.close()

async def resolve_incoming(
    info: strawberry.Info, 
    root: Any, 
    predicate: Optional[str] = None, 
    limit: int = 10
) -> List[Any]:
    """Resolves incoming relationships."""
    engine = info.context["engine"]
    conn = engine.conn
    cursor = conn.cursor()
    try:
        sql = "SELECT p, s FROM Graph_KG.rdf_edges WHERE o_id = ?"
        params = [str(root.id)]
        if predicate:
            sql += " AND p = ?"
            params.append(predicate)
        cursor.execute(sql, params)
        rows = cursor.fetchmany(limit)
        from .schema import Relationship
        return [Relationship(predicate=row[0], target_id=row[1]) for row in rows]
    finally:
        cursor.close()

async def resolve_cypher(
    info: strawberry.Info, 
    query: str, 
    parameters: Optional[strawberry.scalars.JSON] = None
) -> Any:
    """Resolves raw Cypher queries."""
    engine = info.context["engine"]
    # engine.execute_cypher returns {"columns": [...], "rows": [...], ...}
    result = engine.execute_cypher(query, parameters=parameters)
    
    # Apply recursive serialization to rows
    serialized_rows = []
    for row in result.get("rows", []):
        serialized_rows.append(serialize_value(row))
        
    from .schema import CypherResult
    return CypherResult(
        columns=result.get("columns", []),
        rows=serialized_rows
    )

def serialize_value(val: Any) -> Any:
    """Recursively serializes IRIS/Python values to JSON-compatible format."""
    if isinstance(val, (list, tuple)):
        return [serialize_value(item) for item in val]
    if isinstance(val, dict):
        return {str(k): serialize_value(v) for k, v in val.items()}
    if isinstance(val, (datetime, date)):
        return val.isoformat()
    if isinstance(val, Decimal):
        return float(val)
    if isinstance(val, str):
        # Check if it's a JSON string (IRIS often aggregates results as JSON)
        if (val.startswith('{') and val.endswith('}')) or (val.startswith('[') and val.endswith(']')):
            try:
                return serialize_value(json.loads(val))
            except:
                return val
    return val

def map_node_data(node_data: Dict[str, Any], info) -> Any:
    """Maps raw IRIS node data to a Strawberry dynamic type instance."""
    labels = node_data.get("labels", [])
    properties = node_data.get("properties", {})
    node_id = node_data.get("id")
    primary_label = labels[0] if labels else "Node"
    dynamic_class = DYNAMIC_TYPES.get(primary_label)
    mapped_props = {}
    for k, v in properties.items():
        field_name = k
        if k.lower() in RESERVED_KEYWORDS:
            field_name = f"p_{k}"
        mapped_props[field_name] = str(v) if v is not None else None
    if dynamic_class:
        kwargs = {"id": node_id, "labels": labels, **mapped_props}
        obj = dynamic_class(**kwargs)
        obj._primary_label = primary_label
        return obj
    class GenericNode:
        def __init__(self, **kwargs):
            for k, v in kwargs.items(): setattr(self, k, v)
        def properties(self) -> List[Any]:
            from .schema import Property
            return [Property(key=k, value=str(v)) for k, v in properties.items()]
    return GenericNode(id=node_id, labels=labels, _primary_label=primary_label, **mapped_props)
