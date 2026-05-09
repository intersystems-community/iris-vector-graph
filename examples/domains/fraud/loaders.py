"""
Fraud Detection Domain DataLoaders

DataLoaders for batched queries in fraud detection domain.
Prevents N+1 query problems when resolving relationships.
"""

from typing import Any, Dict, List, Optional

from strawberry.dataloader import DataLoader


async def load_accounts(keys: List[str], connection) -> List[Optional[Dict[str, Any]]]:
    """
    Batch load accounts by ID.

    Args:
        keys: List of account IDs to load
        connection: IRIS database connection

    Returns:
        List of account data dicts in same order as keys
    """
    if not keys:
        return []

    from iris_vector_graph.engine import IRISGraphEngine
    engine = IRISGraphEngine(connection)

    placeholders = ",".join(["?" for _ in keys])
    result = engine.execute_cypher(f"""
        MATCH (n:Account)
        WHERE n.node_id IN ({placeholders})
        RETURN n.node_id
    """, keys)

    account_ids = {row[0] for row in result.rows} if result.rows else set()

    props_by_id: Dict[str, Dict[str, str]] = {}
    for account_id in account_ids:
        node_result = engine.get_node(account_id)
        if node_result:
            props_by_id[account_id] = node_result.get("properties", {})

    results = []
    for key in keys:
        if key in account_ids:
            props = props_by_id.get(key, {})
            results.append(
                {
                    "id": key,
                    "labels": ["Account"],
                    "properties": props,
                    "account_type": props.get("account_type"),
                    "status": props.get("status"),
                    "risk_score": float(props["risk_score"]) if "risk_score" in props else None,
                    "holder_name": props.get("holder_name"),
                }
            )
        else:
            results.append(None)

    return results


async def load_transactions(keys: List[str], connection) -> List[Optional[Dict[str, Any]]]:
    """
    Batch load transactions by ID.
    """
    if not keys:
        return []

    from iris_vector_graph.engine import IRISGraphEngine
    engine = IRISGraphEngine(connection)

    placeholders = ",".join(["?" for _ in keys])
    result = engine.execute_cypher(f"""
        MATCH (n:Transaction)
        WHERE n.node_id IN ({placeholders})
        RETURN n.node_id
    """, keys)

    txn_ids = {row[0] for row in result.rows} if result.rows else set()

    props_by_id: Dict[str, Dict[str, str]] = {}
    for txn_id in txn_ids:
        node_result = engine.get_node(txn_id)
        if node_result:
            props_by_id[txn_id] = node_result.get("properties", {})

    results = []
    for key in keys:
        if key in txn_ids:
            props = props_by_id.get(key, {})
            results.append(
                {
                    "id": key,
                    "labels": ["Transaction"],
                    "properties": props,
                    "amount": float(props["amount"]) if "amount" in props else None,
                    "currency": props.get("currency"),
                    "transaction_type": props.get("transaction_type"),
                    "status": props.get("status"),
                }
            )
        else:
            results.append(None)

    return results


async def load_alerts(keys: List[str], connection) -> List[Optional[Dict[str, Any]]]:
    """
    Batch load alerts by ID.
    """
    if not keys:
        return []

    from iris_vector_graph.engine import IRISGraphEngine
    engine = IRISGraphEngine(connection)

    placeholders = ",".join(["?" for _ in keys])
    result = engine.execute_cypher(f"""
        MATCH (n:Alert)
        WHERE n.node_id IN ({placeholders})
        RETURN n.node_id
    """, keys)

    alert_ids = {row[0] for row in result.rows} if result.rows else set()

    props_by_id: Dict[str, Dict[str, str]] = {}
    for alert_id in alert_ids:
        node_result = engine.get_node(alert_id)
        if node_result:
            props_by_id[alert_id] = node_result.get("properties", {})

    results = []
    for key in keys:
        if key in alert_ids:
            props = props_by_id.get(key, {})
            results.append(
                {
                    "id": key,
                    "labels": ["Alert"],
                    "properties": props,
                    "alert_type": props.get("alert_type"),
                    "severity": props.get("severity"),
                    "confidence": float(props["confidence"]) if "confidence" in props else None,
                    "status": props.get("status"),
                }
            )
        else:
            results.append(None)

    return results


async def load_account_edges(keys: List[str], connection) -> List[List[Dict[str, Any]]]:
    """
    Load edges (transactions) for accounts.

    Args:
        keys: Account IDs
        connection: Database connection

    Returns:
        List of edge lists, one per key
    """
    if not keys:
        return []

    from iris_vector_graph.engine import IRISGraphEngine
    engine = IRISGraphEngine(connection)

    placeholders = ",".join(["?" for _ in keys])
    result = engine.execute_cypher(f"""
        MATCH (t:Transaction)-[r:FROM_ACCOUNT|:TO_ACCOUNT]->(a:Account)
        WHERE a.node_id IN ({placeholders})
        RETURN t.node_id as txn_id, type(r) as rel_type, a.node_id as account_id
    """, keys)

    edges_by_account: Dict[str, List[Dict[str, Any]]] = {k: [] for k in keys}

    for txn_id, rel_type, account_id in result.rows if result.rows else []:
        edge = {
            "transaction_id": txn_id,
            "type": rel_type,
            "account_id": account_id,
        }
        edges_by_account[account_id].append(edge)

    return [edges_by_account[k] for k in keys]


def create_fraud_loaders(connection) -> Dict[str, DataLoader]:
    """
    Create all DataLoaders for fraud detection domain.

    Args:
        connection: IRIS database connection

    Returns:
        Dict of loader name -> DataLoader instance
    """
    return {
        "account_loader": DataLoader(load_fn=lambda keys: load_accounts(keys, connection)),
        "transaction_loader": DataLoader(load_fn=lambda keys: load_transactions(keys, connection)),
        "alert_loader": DataLoader(load_fn=lambda keys: load_alerts(keys, connection)),
        "account_edge_loader": DataLoader(
            load_fn=lambda keys: load_account_edges(keys, connection)
        ),
    }
