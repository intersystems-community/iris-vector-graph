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
    cursor = connection.cursor()

    # Check if account exists
    cursor.execute(
        """
        SELECT s FROM rdf_labels 
        WHERE s = ? AND label = 'Account'
    """,
        (account_id,),
    )

    if cursor.fetchone() is None:
        return None

    # Get properties
    cursor.execute("SELECT key, val FROM rdf_props WHERE s = ?", (account_id,))
    props = {row[0]: row[1] for row in cursor.fetchall()}

    return {
        "id": account_id,
        "labels": ["Account"],
        "properties": props,
        "account_type": props.get("account_type"),
        "status": props.get("status"),
        "risk_score": float(props["risk_score"]) if "risk_score" in props else None,
        "holder_name": props.get("holder_name"),
    }


def get_transaction_by_id(txn_id: str, connection) -> Optional[Dict[str, Any]]:
    """
    Get a single transaction by ID.
    """
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT s FROM rdf_labels 
        WHERE s = ? AND label = 'Transaction'
    """,
        (txn_id,),
    )

    if cursor.fetchone() is None:
        return None

    cursor.execute("SELECT key, val FROM rdf_props WHERE s = ?", (txn_id,))
    props = {row[0]: row[1] for row in cursor.fetchall()}

    return {
        "id": txn_id,
        "labels": ["Transaction"],
        "properties": props,
        "amount": float(props["amount"]) if "amount" in props else None,
        "currency": props.get("currency"),
        "transaction_type": props.get("transaction_type"),
        "status": props.get("status"),
    }


def get_alert_by_id(alert_id: str, connection) -> Optional[Dict[str, Any]]:
    """
    Get a single alert by ID.
    """
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT s FROM rdf_labels 
        WHERE s = ? AND label = 'Alert'
    """,
        (alert_id,),
    )

    if cursor.fetchone() is None:
        return None

    cursor.execute("SELECT key, val FROM rdf_props WHERE s = ?", (alert_id,))
    props = {row[0]: row[1] for row in cursor.fetchall()}

    return {
        "id": alert_id,
        "labels": ["Alert"],
        "properties": props,
        "alert_type": props.get("alert_type"),
        "severity": props.get("severity"),
        "confidence": float(props["confidence"]) if "confidence" in props else None,
        "status": props.get("status"),
    }


def find_high_risk_accounts(
    connection, min_risk_score: float = 0.7, limit: int = 10
) -> List[Dict[str, Any]]:
    """
    Find accounts with risk score above threshold.
    """
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT DISTINCT l.s 
        FROM rdf_labels l
        JOIN rdf_props p ON l.s = p.s
        WHERE l.label = 'Account'
        AND p.key = 'risk_score'
        AND CAST(p.val AS FLOAT) >= ?
        LIMIT ?
    """,
        (min_risk_score, limit),
    )

    account_ids = [row[0] for row in cursor.fetchall()]

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
    cursor = connection.cursor()

    # Find accounts that are part of cycles by looking for
    # accounts that appear in both FROM_ACCOUNT and TO_ACCOUNT edges
    # with the same other accounts

    # Simplified detection: find accounts with both incoming and outgoing
    cursor.execute(
        """
        SELECT DISTINCT e1.o_id as account_id
        FROM rdf_edges e1
        JOIN rdf_edges e2 ON e1.o_id = e2.o_id
        WHERE e1.p = 'FROM_ACCOUNT'
        AND e2.p = 'TO_ACCOUNT'
        AND e1.s != e2.s
    """
    )

    ring_accounts = [row[0] for row in cursor.fetchall()]

    # Group into potential rings
    patterns = []
    seen_accounts = set()

    for account_id in ring_accounts:
        if account_id in seen_accounts:
            continue

        # Get connected accounts via transactions
        cursor.execute(
            """
            SELECT DISTINCT 
                CASE 
                    WHEN e.p = 'FROM_ACCOUNT' THEN e.s
                    ELSE e.s
                END as txn_id,
                CASE 
                    WHEN e.p = 'FROM_ACCOUNT' THEN 
                        (SELECT o_id FROM rdf_edges WHERE s = e.s AND p = 'TO_ACCOUNT')
                    ELSE 
                        (SELECT o_id FROM rdf_edges WHERE s = e.s AND p = 'FROM_ACCOUNT')
                END as other_account
            FROM rdf_edges e
            WHERE e.o_id = ?
            AND e.p IN ('FROM_ACCOUNT', 'TO_ACCOUNT')
        """,
            (account_id,),
        )

        connected = cursor.fetchall()

        if len(connected) >= 2:  # Potential ring
            pattern = {
                "pattern_type": "ring",
                "confidence": 0.8 + (len(connected) * 0.02),  # Higher for more connections
                "accounts": [account_id] + [c[1] for c in connected if c[1]],
                "transactions": [c[0] for c in connected],
            }
            patterns.append(pattern)
            seen_accounts.add(account_id)

    return patterns[:10]  # Limit results


def detect_mule_accounts(connection, min_unique_counterparties: int = 5) -> List[Dict[str, Any]]:
    """
    Detect potential mule accounts (high-degree nodes).

    Mule accounts receive from many sources and distribute to many destinations.
    """
    cursor = connection.cursor()

    # Find accounts with many unique counterparties
    cursor.execute(
        """
        SELECT account_id, COUNT(DISTINCT counterparty) as degree
        FROM (
            SELECT 
                e1.o_id as account_id,
                e2.o_id as counterparty
            FROM rdf_edges e1
            JOIN rdf_edges e2 ON e1.s = e2.s
            WHERE e1.p = 'TO_ACCOUNT'
            AND e2.p = 'FROM_ACCOUNT'
            
            UNION
            
            SELECT 
                e1.o_id as account_id,
                e2.o_id as counterparty
            FROM rdf_edges e1
            JOIN rdf_edges e2 ON e1.s = e2.s
            WHERE e1.p = 'FROM_ACCOUNT'
            AND e2.p = 'TO_ACCOUNT'
        )
        GROUP BY account_id
        HAVING COUNT(DISTINCT counterparty) >= ?
        ORDER BY degree DESC
        LIMIT 10
    """,
        (min_unique_counterparties,),
    )

    results = []
    for account_id, degree in cursor.fetchall():
        account = get_account_by_id(account_id, connection)
        if account:
            account["unique_counterparties"] = degree
            results.append(account)

    return results


def get_open_alerts(connection, limit: int = 20) -> List[Dict[str, Any]]:
    """
    Get open alerts ordered by severity.
    """
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT l.s
        FROM rdf_labels l
        JOIN rdf_props p ON l.s = p.s
        WHERE l.label = 'Alert'
        AND p.key = 'status'
        AND p.val = 'open'
        LIMIT ?
    """,
        (limit,),
    )

    alert_ids = [row[0] for row in cursor.fetchall()]

    results = []
    for alert_id in alert_ids:
        alert = get_alert_by_id(alert_id, connection)
        if alert:
            results.append(alert)

    # Sort by severity
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    results.sort(key=lambda a: severity_order.get(a.get("severity", "low"), 4))

    return results
