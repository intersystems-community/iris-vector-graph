"""
Fraud Detection Domain Resolvers

Query resolvers for fraud detection domain.
"""

from typing import Any, Dict, List, Optional

import strawberry


def get_account_by_id(account_id: str, connection) -> Optional[Dict[str, Any]]:
    """
    Get a single account by ID.
    """
    from iris_vector_graph.engine import IRISGraphEngine
    engine = IRISGraphEngine(connection)
    
    result = engine.execute_cypher(
        "MATCH (n:Account {node_id:$id}) RETURN n.node_id AS id",
        {"id": account_id}
    )
    
    if not result.rows:
        return None

    node_result = engine.get_node(account_id)
    if not node_result:
        return None
    
    return {
        "id": account_id,
        "labels": ["Account"],
        "properties": node_result.get("properties", {}),
        "account_type": node_result.get("properties", {}).get("account_type"),
        "status": node_result.get("properties", {}).get("status"),
        "risk_score": float(node_result.get("properties", {}).get("risk_score", 0)) if "risk_score" in node_result.get("properties", {}) else None,
        "holder_name": node_result.get("properties", {}).get("holder_name"),
    }


def get_transaction_by_id(txn_id: str, connection) -> Optional[Dict[str, Any]]:
    """
    Get a single transaction by ID.
    """
    from iris_vector_graph.engine import IRISGraphEngine
    engine = IRISGraphEngine(connection)

    result = engine.execute_cypher(
        "MATCH (n:Transaction {node_id:$id}) RETURN n.node_id AS id",
        {"id": txn_id}
    )

    if not result.rows:
        return None

    node_result = engine.get_node(txn_id)
    if not node_result:
        return None

    return {
        "id": txn_id,
        "labels": ["Transaction"],
        "properties": node_result.get("properties", {}),
        "amount": float(node_result.get("properties", {}).get("amount", 0)) if "amount" in node_result.get("properties", {}) else None,
        "currency": node_result.get("properties", {}).get("currency"),
        "transaction_type": node_result.get("properties", {}).get("transaction_type"),
        "status": node_result.get("properties", {}).get("status"),
    }


def get_alert_by_id(alert_id: str, connection) -> Optional[Dict[str, Any]]:
    """
    Get a single alert by ID.
    """
    from iris_vector_graph.engine import IRISGraphEngine
    engine = IRISGraphEngine(connection)

    result = engine.execute_cypher(
        "MATCH (n:Alert {node_id:$id}) RETURN n.node_id AS id",
        {"id": alert_id}
    )

    if not result.rows:
        return None

    node_result = engine.get_node(alert_id)
    if not node_result:
        return None

    return {
        "id": alert_id,
        "labels": ["Alert"],
        "properties": node_result.get("properties", {}),
        "alert_type": node_result.get("properties", {}).get("alert_type"),
        "severity": node_result.get("properties", {}).get("severity"),
        "confidence": float(node_result.get("properties", {}).get("confidence", 0)) if "confidence" in node_result.get("properties", {}) else None,
        "status": node_result.get("properties", {}).get("status"),
    }


def find_high_risk_accounts(
    connection, min_risk_score: float = 0.7, limit: int = 10
) -> List[Dict[str, Any]]:
    """
    Find accounts with risk score above threshold.
    """
    from iris_vector_graph.engine import IRISGraphEngine
    engine = IRISGraphEngine(connection)

    result = engine.execute_cypher(f"""
        MATCH (n:Account)
        WHERE n.risk_score >= {min_risk_score}
        RETURN n.node_id
        LIMIT {limit}
    """)

    account_ids = [row[0] for row in result.rows] if result.rows else []

    results = []
    for account_id in account_ids:
        account = get_account_by_id(account_id, connection)
        if account:
            results.append(account)

    return results


def detect_ring_patterns(connection, max_ring_size: int = 5) -> List[Dict[str, Any]]:
    """
    Detect ring (cyclic) patterns in transaction network.

    A ring pattern indicates potential money laundering where funds
    cycle through multiple accounts and return to origin.
    """
    from iris_vector_graph.engine import IRISGraphEngine
    engine = IRISGraphEngine(connection)

    result = engine.execute_cypher("""
        MATCH (a)-[:FROM_ACCOUNT]->(t1)-[:TO_ACCOUNT]->(b)
        WHERE a.node_id != b.node_id
        RETURN DISTINCT b.node_id
    """)

    ring_accounts = [row[0] for row in result.rows] if result.rows else []

    patterns = []
    seen_accounts = set()

    for account_id in ring_accounts:
        if account_id in seen_accounts:
            continue

        connected_result = engine.execute_cypher(f"""
            MATCH (t:Transaction)-[:FROM_ACCOUNT|:TO_ACCOUNT]-(n:Account {{node_id: '{account_id}'}})
            RETURN DISTINCT t.node_id
            LIMIT 10
        """)

        connected = connected_result.rows if connected_result.rows else []

        if len(connected) >= 2:
            pattern = {
                "pattern_type": "ring",
                "confidence": 0.8 + (len(connected) * 0.02),
                "accounts": [account_id],
                "transactions": [c[0] for c in connected],
            }
            patterns.append(pattern)
            seen_accounts.add(account_id)

    return patterns[:10]


def detect_mule_accounts(connection, min_unique_counterparties: int = 5) -> List[Dict[str, Any]]:
    """
    Detect potential mule accounts (high-degree nodes).

    Mule accounts receive from many sources and distribute to many destinations.
    """
    from iris_vector_graph.engine import IRISGraphEngine
    engine = IRISGraphEngine(connection)

    result = engine.execute_cypher(f"""
        MATCH (a:Account)-[:FROM_ACCOUNT|:TO_ACCOUNT]-(t:Transaction)-[:FROM_ACCOUNT|:TO_ACCOUNT]-(b:Account)
        WHERE a.node_id != b.node_id
        RETURN a.node_id, COUNT(DISTINCT b.node_id) AS degree
        GROUP BY a.node_id
        HAVING COUNT(DISTINCT b.node_id) >= {min_unique_counterparties}
        ORDER BY degree DESC
        LIMIT 10
    """)

    results = []
    for account_id, degree in result.rows if result.rows else []:
        account = get_account_by_id(account_id, connection)
        if account:
            account["unique_counterparties"] = degree
            results.append(account)

    return results


def get_open_alerts(connection, limit: int = 20) -> List[Dict[str, Any]]:
    """
    Get open alerts ordered by severity.
    """
    from iris_vector_graph.engine import IRISGraphEngine
    engine = IRISGraphEngine(connection)

    result = engine.execute_cypher(f"""
        MATCH (n:Alert)
        WHERE n.status = 'open'
        RETURN n.node_id
        LIMIT {limit}
    """)

    alert_ids = [row[0] for row in result.rows] if result.rows else []

    results = []
    for alert_id in alert_ids:
        alert = get_alert_by_id(alert_id, connection)
        if alert:
            results.append(alert)

    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    results.sort(key=lambda a: severity_order.get(a.get("severity", "low"), 4))

    return results
