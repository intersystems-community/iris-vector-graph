#!/usr/bin/env python3
"""
Bitemporal Fraud Detection Example for Financial Services

This module demonstrates bitemporal data management patterns for fraud detection,
critical for IDFS (InterSystems Data Fabric for Financial Services) customers.

Key Concepts:
- Valid Time: When the transaction actually occurred in the real world
- Transaction Time: When we recorded/learned about the transaction
- Late-arriving data: Transactions reported with delay (settlement, batch processing)
- Corrections: Amendments, chargebacks, fraud reversals
- Audit queries: "What did we know at time X?" vs "What happened at time Y?"

Use Cases:
- Regulatory compliance (SOX, GDPR, MiFID II)
- Fraud investigation and forensics
- Chargeback analysis
- Customer dispute resolution
- Model performance tracking
"""

import iris
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from enum import Enum


class FraudStatus(Enum):
    """Fraud status levels"""
    CLEAN = "clean"
    SUSPICIOUS = "suspicious"
    CONFIRMED_FRAUD = "confirmed_fraud"
    REVERSED = "reversed"


class RiskLevel(Enum):
    """Risk classification"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class BitemporalEvent:
    """Represents a bitemporal fraud event with both valid and transaction time"""
    event_id: str
    version_id: int
    transaction_id: str
    amount: float
    payer: str
    payee: str

    # Valid time (when it actually happened)
    valid_from: datetime
    valid_to: Optional[datetime] = None

    # Transaction time (when we recorded it)
    system_from: Optional[datetime] = None
    system_to: Optional[datetime] = None

    # Fraud attributes
    fraud_score: Optional[float] = None
    fraud_status: FraudStatus = FraudStatus.CLEAN
    risk_level: RiskLevel = RiskLevel.LOW

    # Optional metadata
    merchant: Optional[str] = None
    device: Optional[str] = None
    ip_address: Optional[str] = None
    currency: str = "USD"
    channel: Optional[str] = None
    location_country: Optional[str] = None

    # Audit trail
    reason_for_change: Optional[str] = None
    changed_by: Optional[str] = None


class BitemporalFraudManager:
    """
    Manager for bitemporal fraud event operations.

    This class provides high-level APIs for:
    - Inserting new events
    - Correcting/amending existing events
    - Time-travel queries (as-of queries)
    - Audit trail generation
    """

    def __init__(self):
        """Initialize connection to IRIS"""
        self.conn = iris

    def insert_event(self, event: BitemporalEvent) -> str:
        """
        Insert a new bitemporal fraud event.

        Args:
            event: BitemporalEvent to insert

        Returns:
            event_id of the inserted event
        """
        # System time defaults to now if not provided
        system_from = event.system_from or datetime.now()

        query = """
        INSERT INTO bitemporal_fraud_events (
            event_id, version_id, valid_from, valid_to,
            system_from, system_to, transaction_id,
            amount, currency, payer, payee, merchant,
            device, ip_address, fraud_score, fraud_status,
            risk_level, channel, location_country,
            reason_for_change, changed_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """

        self.conn.sql.exec(query,
            event.event_id, event.version_id,
            event.valid_from, event.valid_to,
            system_from, event.system_to,
            event.transaction_id, event.amount, event.currency,
            event.payer, event.payee, event.merchant,
            event.device, event.ip_address,
            event.fraud_score, event.fraud_status.value,
            event.risk_level.value, event.channel,
            event.location_country, event.reason_for_change,
            event.changed_by
        )

        return event.event_id

    def amend_event(self, event_id: str, new_data: Dict[str, Any],
                   reason: str, changed_by: str) -> int:
        """
        Create an amendment (new version) of an existing event.

        This is used for:
        - Chargebacks
        - Fraud reversals
        - Score updates
        - Status changes

        Args:
            event_id: ID of the event to amend
            new_data: Dictionary of fields to update
            reason: Explanation for the change
            changed_by: Who made the change

        Returns:
            new version_id
        """
        # Get current version
        current = self.conn.sql.exec("""
            SELECT * FROM bitemporal_fraud_events
            WHERE event_id = ? AND system_to IS NULL
        """, event_id)

        if not current:
            raise ValueError(f"Event {event_id} not found")

        current_row = list(current)[0]
        new_version = current_row[1] + 1  # version_id

        # Close current version
        now = datetime.now()
        self.conn.sql.exec("""
            UPDATE bitemporal_fraud_events
            SET system_to = ?
            WHERE event_id = ? AND system_to IS NULL
        """, now, event_id)

        # Create new version with updated data
        new_event = BitemporalEvent(
            event_id=event_id,
            version_id=new_version,
            transaction_id=current_row[6],
            amount=new_data.get('amount', current_row[7]),
            currency=current_row[8],
            payer=current_row[9],
            payee=current_row[10],
            merchant=new_data.get('merchant', current_row[11]),
            device=new_data.get('device', current_row[12]),
            ip_address=new_data.get('ip_address', current_row[13]),
            valid_from=current_row[2],
            valid_to=new_data.get('valid_to', current_row[3]),
            system_from=now,
            fraud_score=new_data.get('fraud_score', current_row[14]),
            fraud_status=FraudStatus(new_data.get('fraud_status', current_row[15])),
            risk_level=RiskLevel(new_data.get('risk_level', current_row[16])),
            channel=new_data.get('channel', current_row[17]),
            location_country=new_data.get('location_country', current_row[18]),
            reason_for_change=reason,
            changed_by=changed_by
        )

        self.insert_event(new_event)
        return new_version

    def get_current_version(self, event_id: str) -> Optional[BitemporalEvent]:
        """
        Get the current (latest) version of an event.

        Args:
            event_id: ID of the event

        Returns:
            Current BitemporalEvent or None if not found
        """
        result = self.conn.sql.exec("""
            SELECT * FROM bitemporal_fraud_events
            WHERE event_id = ? AND system_to IS NULL
        """, event_id)

        row = list(result)
        if not row:
            return None

        return self._row_to_event(row[0])

    def get_as_of(self, event_id: str, as_of_time: datetime) -> Optional[BitemporalEvent]:
        """
        Time-travel query: Get event as it appeared at specific time.

        This answers: "What did we know about this transaction at 2PM yesterday?"

        Args:
            event_id: ID of the event
            as_of_time: Point in time to query

        Returns:
            BitemporalEvent as it was at that time, or None
        """
        result = self.conn.sql.exec("""
            SELECT * FROM bitemporal_fraud_events
            WHERE event_id = ?
              AND system_from <= ?
              AND (system_to IS NULL OR system_to > ?)
            ORDER BY version_id DESC
            LIMIT 1
        """, event_id, as_of_time, as_of_time)

        row = list(result)
        if not row:
            return None

        return self._row_to_event(row[0])

    def get_audit_trail(self, event_id: str) -> List[BitemporalEvent]:
        """
        Get complete audit trail (all versions) for an event.

        Args:
            event_id: ID of the event

        Returns:
            List of all versions in chronological order
        """
        result = self.conn.sql.exec("""
            SELECT * FROM bitemporal_fraud_events
            WHERE event_id = ?
            ORDER BY version_id
        """, event_id)

        return [self._row_to_event(row) for row in result]

    def find_late_arrivals(self, delay_hours: int = 24) -> List[BitemporalEvent]:
        """
        Find transactions reported with significant delay.

        Use case: Detect settlement delays, batch processing issues,
        or potentially backdated fraudulent transactions.

        Args:
            delay_hours: Minimum delay in hours to consider "late"

        Returns:
            List of late-arriving transactions
        """
        result = self.conn.sql.exec("""
            SELECT * FROM bitemporal_fraud_events
            WHERE system_to IS NULL
              AND TIMESTAMPDIFF(HOUR, valid_from, system_from) > ?
            ORDER BY TIMESTAMPDIFF(HOUR, valid_from, system_from) DESC
        """, delay_hours)

        return [self._row_to_event(row) for row in result]

    def find_amendments(self, since: datetime) -> List[Dict[str, Any]]:
        """
        Find all amendments made since a specific time.

        Use case: Change monitoring, audit log, compliance reporting

        Args:
            since: Start time for search

        Returns:
            List of amendments with before/after comparison
        """
        result = self.conn.sql.exec("""
            SELECT
                e1.event_id,
                e1.version_id AS old_version,
                e2.version_id AS new_version,
                e1.fraud_status AS old_status,
                e2.fraud_status AS new_status,
                e1.fraud_score AS old_score,
                e2.fraud_score AS new_score,
                e2.system_from AS change_time,
                e2.reason_for_change,
                e2.changed_by
            FROM bitemporal_fraud_events e1
            JOIN bitemporal_fraud_events e2
                ON e1.event_id = e2.event_id
                AND e2.version_id = e1.version_id + 1
            WHERE e2.system_from >= ?
            ORDER BY e2.system_from DESC
        """, since)

        amendments = []
        for row in result:
            amendments.append({
                'event_id': row[0],
                'old_version': row[1],
                'new_version': row[2],
                'old_status': row[3],
                'new_status': row[4],
                'old_score': row[5],
                'new_score': row[6],
                'change_time': row[7],
                'reason': row[8],
                'changed_by': row[9]
            })

        return amendments

    def reconstruct_state_at(self, as_of_time: datetime) -> List[BitemporalEvent]:
        """
        Reconstruct complete database state as it was at specific time.

        Use case: Regulatory audit, forensic analysis, compliance reporting

        Args:
            as_of_time: Point in time to reconstruct

        Returns:
            List of all events as they appeared at that time
        """
        result = self.conn.sql.exec("""
            SELECT * FROM bitemporal_fraud_events e1
            WHERE e1.system_from <= ?
              AND (e1.system_to IS NULL OR e1.system_to > ?)
              AND e1.version_id = (
                  SELECT MAX(e2.version_id)
                  FROM bitemporal_fraud_events e2
                  WHERE e2.event_id = e1.event_id
                    AND e2.system_from <= ?
                    AND (e2.system_to IS NULL OR e2.system_to > ?)
              )
            ORDER BY e1.valid_from DESC
        """, as_of_time, as_of_time, as_of_time, as_of_time)

        return [self._row_to_event(row) for row in result]

    def _row_to_event(self, row) -> BitemporalEvent:
        """Convert database row to BitemporalEvent object"""
        return BitemporalEvent(
            event_id=row[0],
            version_id=row[1],
            valid_from=row[2],
            valid_to=row[3],
            system_from=row[4],
            system_to=row[5],
            transaction_id=row[6],
            amount=row[7],
            currency=row[8],
            payer=row[9],
            payee=row[10],
            merchant=row[11],
            device=row[12],
            ip_address=row[13],
            fraud_score=row[14],
            fraud_status=FraudStatus(row[15]),
            risk_level=RiskLevel(row[16]),
            channel=row[17],
            location_country=row[18],
            reason_for_change=row[19],
            changed_by=row[20]
        )


def example_bitemporal_workflow():
    """
    Complete example demonstrating bitemporal fraud detection workflow.
    """
    manager = BitemporalFraudManager()

    print("=== Bitemporal Fraud Detection Example ===\n")

    # 1. Initial transaction (clean)
    print("1. Recording initial transaction...")
    event = BitemporalEvent(
        event_id="evt_001",
        version_id=1,
        transaction_id="TXN-2025-001",
        amount=1500.00,
        payer="acct:alice",
        payee="acct:bob",
        merchant="merch:electronics",
        device="dev:laptop_001",
        ip_address="ip:192.168.1.100",
        valid_from=datetime(2025, 1, 15, 10, 30, 0),  # When it happened
        fraud_score=0.15,
        fraud_status=FraudStatus.CLEAN,
        risk_level=RiskLevel.LOW,
        channel="web",
        location_country="US",
        changed_by="system"
    )
    manager.insert_event(event)
    print(f"✓ Transaction {event.transaction_id} recorded at {datetime.now()}")
    print(f"  Actual transaction time: {event.valid_from}")
    print(f"  Initial fraud score: {event.fraud_score}\n")

    # 2. Late-arriving similar transaction (suspicious pattern)
    print("2. Late-arriving transaction detected...")
    event2 = BitemporalEvent(
        event_id="evt_002",
        version_id=1,
        transaction_id="TXN-2025-002",
        amount=1500.00,
        payer="acct:alice",
        payee="acct:charlie",
        merchant="merch:electronics",
        device="dev:laptop_001",
        valid_from=datetime(2025, 1, 15, 10, 31, 0),  # 1 minute after first
        system_from=datetime.now(),  # But reported now (hours later)
        fraud_score=0.65,
        fraud_status=FraudStatus.SUSPICIOUS,
        risk_level=RiskLevel.MEDIUM,
        channel="web",
        location_country="US",
        reason_for_change="Late arrival - batch processing delay",
        changed_by="system"
    )
    manager.insert_event(event2)
    delay = (datetime.now() - event2.valid_from).total_seconds() / 3600
    print(f"✓ Transaction {event2.transaction_id} reported {delay:.1f} hours late")
    print(f"  Flagged as SUSPICIOUS due to pattern\n")

    # 3. Fraud confirmed - amend original transaction
    print("3. Fraud investigation completed - updating status...")
    manager.amend_event(
        event_id="evt_001",
        new_data={
            'fraud_score': 0.95,
            'fraud_status': FraudStatus.CONFIRMED_FRAUD.value,
            'risk_level': RiskLevel.CRITICAL.value
        },
        reason="Confirmed fraud after investigation - part of coordinated attack",
        changed_by="fraud_analyst_jane"
    )
    print("✓ Original transaction updated to CONFIRMED_FRAUD\n")

    # 4. Chargeback processed
    print("4. Processing chargeback...")
    manager.amend_event(
        event_id="evt_001",
        new_data={
            'fraud_status': FraudStatus.REVERSED.value,
            'valid_to': datetime.now()  # Mark transaction as no longer valid
        },
        reason="Chargeback processed - funds returned to customer",
        changed_by="payments_system"
    )
    print("✓ Chargeback completed, transaction reversed\n")

    # 5. Audit queries
    print("5. Running audit queries...\n")

    # Current state
    current = manager.get_current_version("evt_001")
    print(f"Current status: {current.fraud_status.value}")
    print(f"Current score: {current.fraud_score}")
    print(f"Valid until: {current.valid_to}\n")

    # Complete audit trail
    trail = manager.get_audit_trail("evt_001")
    print(f"Audit trail ({len(trail)} versions):")
    for v in trail:
        print(f"  v{v.version_id} @ {v.system_from}: "
              f"{v.fraud_status.value} (score={v.fraud_score}) - {v.reason_for_change}")
    print()

    # Time travel: What did we know 1 hour ago?
    one_hour_ago = datetime.now() - timedelta(hours=1)
    past_state = manager.get_as_of("evt_001", one_hour_ago)
    if past_state:
        print(f"State 1 hour ago (v{past_state.version_id}):")
        print(f"  Status: {past_state.fraud_status.value}")
        print(f"  Score: {past_state.fraud_score}\n")

    # Find late arrivals
    late = manager.find_late_arrivals(delay_hours=1)
    print(f"Late arrivals (>{1}h delay): {len(late)} transactions")
    for txn in late:
        delay_h = (txn.system_from - txn.valid_from).total_seconds() / 3600
        print(f"  {txn.transaction_id}: {delay_h:.1f}h delay\n")

    print("=== Bitemporal workflow complete ===")


if __name__ == "__main__":
    example_bitemporal_workflow()
