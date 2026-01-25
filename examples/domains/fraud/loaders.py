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

    cursor = connection.cursor()

    # Build placeholders for IN clause
    placeholders = ",".join(["?" for _ in keys])

    # Query accounts
    cursor.execute(
        f"""
        SELECT l.s as id, l.label
        FROM rdf_labels l
        WHERE l.s IN ({placeholders})
        AND l.label = 'Account'
    """,
        keys,
    )

    account_ids = {row[0] for row in cursor.fetchall()}

    # Get properties for found accounts
    if account_ids:
        cursor.execute(
            f"""
            SELECT s, key, val FROM rdf_props
            WHERE s IN ({placeholders})
        """,
            list(account_ids),
        )

        props_by_id: Dict[str, Dict[str, str]] = {}
        for s, key, val in cursor.fetchall():
            if s not in props_by_id:
                props_by_id[s] = {}
            props_by_id[s][key] = val
    else:
        props_by_id = {}

    # Build result list in key order
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

    cursor = connection.cursor()
    placeholders = ",".join(["?" for _ in keys])

    # Query transactions
    cursor.execute(
        f"""
        SELECT l.s as id
        FROM rdf_labels l
        WHERE l.s IN ({placeholders})
        AND l.label = 'Transaction'
    """,
        keys,
    )

    txn_ids = {row[0] for row in cursor.fetchall()}

    # Get properties
    if txn_ids:
        cursor.execute(
            f"""
            SELECT s, key, val FROM rdf_props
            WHERE s IN ({placeholders})
        """,
            list(txn_ids),
        )

        props_by_id: Dict[str, Dict[str, str]] = {}
        for s, key, val in cursor.fetchall():
            if s not in props_by_id:
                props_by_id[s] = {}
            props_by_id[s][key] = val
    else:
        props_by_id = {}

    # Build results
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

    cursor = connection.cursor()
    placeholders = ",".join(["?" for _ in keys])

    # Query alerts
    cursor.execute(
        f"""
        SELECT l.s as id
        FROM rdf_labels l
        WHERE l.s IN ({placeholders})
        AND l.label = 'Alert'
    """,
        keys,
    )

    alert_ids = {row[0] for row in cursor.fetchall()}

    # Get properties
    if alert_ids:
        cursor.execute(
            f"""
            SELECT s, key, val FROM rdf_props
            WHERE s IN ({placeholders})
        """,
            list(alert_ids),
        )

        props_by_id: Dict[str, Dict[str, str]] = {}
        for s, key, val in cursor.fetchall():
            if s not in props_by_id:
                props_by_id[s] = {}
            props_by_id[s][key] = val
    else:
        props_by_id = {}

    # Build results
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

    cursor = connection.cursor()
    placeholders = ",".join(["?" for _ in keys])

    # Find transactions where these accounts are source or destination
    cursor.execute(
        f"""
        SELECT e.s as txn_id, e.p as predicate, e.o_id as account_id
        FROM rdf_edges e
        WHERE e.o_id IN ({placeholders})
        AND e.p IN ('FROM_ACCOUNT', 'TO_ACCOUNT')
    """,
        keys,
    )

    edges_by_account: Dict[str, List[Dict[str, Any]]] = {k: [] for k in keys}

    for txn_id, predicate, account_id in cursor.fetchall():
        edge = {
            "transaction_id": txn_id,
            "type": predicate,
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
