"""
Fraud Detection Domain Types for IRIS Vector Graph API

Domain-specific GraphQL types for fraud detection knowledge graphs.
These types extend the generic Node interface with typed fields.

This is an EXAMPLE domain implementation demonstrating how to create
domain-specific types on top of the generic graph core.
"""

from datetime import datetime
from typing import List, Optional

import strawberry

# Simple scalar types (avoiding complex imports for standalone demo)
DateTime = datetime
JSON = strawberry.scalar(dict, name="JSON", description="JSON scalar type")


@strawberry.type
class Account:
    """
    Financial account entity in fraud detection network.

    Represents a checking, savings, credit, or crypto account
    with risk assessment and transaction relationships.
    """

    id: strawberry.ID
    labels: List[str]
    properties: JSON

    # Account-specific typed fields
    account_type: Optional[str] = strawberry.field(name="accountType", default=None)
    status: Optional[str] = None
    risk_score: Optional[float] = strawberry.field(name="riskScore", default=None)
    holder_name: Optional[str] = strawberry.field(name="holderName", default=None)
    created_date: Optional[str] = strawberry.field(name="createdDate", default=None)

    @strawberry.field
    async def transactions(
        self, info: strawberry.Info, first: int = 10, offset: int = 0
    ) -> List["Transaction"]:
        """Transactions involving this account (sent or received)"""
        # Implementation would use DataLoader - placeholder for now
        return []

    @strawberry.field
    async def sent_transactions(
        self, info: strawberry.Info, first: int = 10
    ) -> List["Transaction"]:
        """Transactions sent from this account"""
        return []

    @strawberry.field
    async def received_transactions(
        self, info: strawberry.Info, first: int = 10
    ) -> List["Transaction"]:
        """Transactions received by this account"""
        return []

    @strawberry.field
    async def alerts(self, info: strawberry.Info, first: int = 10) -> List["Alert"]:
        """Fraud alerts involving this account"""
        return []

    @strawberry.field
    async def connected_accounts(
        self, info: strawberry.Info, depth: int = 2, first: int = 20
    ) -> List["Account"]:
        """Accounts connected via transaction network"""
        return []

    @strawberry.field
    async def similar(
        self, info: strawberry.Info, limit: int = 10, threshold: float = 0.7
    ) -> List["SimilarAccount"]:
        """Find similar accounts using vector embeddings"""
        return []


@strawberry.type
class Transaction:
    """
    Financial transaction between accounts.

    Represents a money transfer, payment, withdrawal, or deposit
    with amount, timing, and relationship to source/destination accounts.
    """

    id: strawberry.ID
    labels: List[str]
    properties: JSON

    # Transaction-specific typed fields
    amount: Optional[float] = None
    currency: Optional[str] = None
    transaction_type: Optional[str] = strawberry.field(name="transactionType", default=None)
    timestamp: Optional[str] = None
    status: Optional[str] = None
    description: Optional[str] = None

    @strawberry.field
    async def from_account(self, info: strawberry.Info) -> Optional[Account]:
        """Source account for this transaction"""
        return None

    @strawberry.field
    async def to_account(self, info: strawberry.Info) -> Optional[Account]:
        """Destination account for this transaction"""
        return None

    @strawberry.field
    async def triggered_alerts(self, info: strawberry.Info) -> List["Alert"]:
        """Alerts triggered by this transaction"""
        return []


@strawberry.type
class Alert:
    """
    Fraud detection alert.

    Represents a system-generated alert for suspicious activity
    with type, severity, confidence, and related entities.
    """

    id: strawberry.ID
    labels: List[str]
    properties: JSON

    # Alert-specific typed fields
    alert_type: Optional[str] = strawberry.field(name="alertType", default=None)
    severity: Optional[str] = None
    confidence: Optional[float] = None
    triggered_at: Optional[str] = strawberry.field(name="triggeredAt", default=None)
    status: Optional[str] = None
    description: Optional[str] = None

    @strawberry.field
    async def related_transactions(self, info: strawberry.Info) -> List[Transaction]:
        """Transactions that triggered this alert"""
        return []

    @strawberry.field
    async def involved_accounts(self, info: strawberry.Info) -> List[Account]:
        """Accounts involved in this alert"""
        return []


@strawberry.type
class SuspiciousPattern:
    """
    Detected fraud pattern in the transaction network.

    Represents ring patterns, mule accounts, velocity violations, etc.
    """

    pattern_type: str = strawberry.field(name="patternType")
    confidence: float
    accounts: List[Account]
    transactions: List[Transaction]
    description: Optional[str] = None


@strawberry.type
class SimilarAccount:
    """Vector similarity result for accounts"""

    account: Account
    similarity: float
    distance: Optional[float] = None


# Input types for mutations
@strawberry.input
class CreateAccountInput:
    """Input for creating a new account"""

    id: strawberry.ID
    account_type: str = strawberry.field(name="accountType")
    status: str = "active"
    holder_name: Optional[str] = strawberry.field(name="holderName", default=None)


@strawberry.input
class CreateTransactionInput:
    """Input for creating a new transaction"""

    id: strawberry.ID
    from_account_id: strawberry.ID = strawberry.field(name="fromAccountId")
    to_account_id: strawberry.ID = strawberry.field(name="toAccountId")
    amount: float
    currency: str = "USD"
    transaction_type: str = strawberry.field(name="transactionType", default="transfer")


@strawberry.input
class CreateAlertInput:
    """Input for creating a fraud alert"""

    alert_type: str = strawberry.field(name="alertType")
    severity: str
    confidence: float
    account_ids: List[strawberry.ID] = strawberry.field(name="accountIds")
    transaction_ids: Optional[List[strawberry.ID]] = strawberry.field(
        name="transactionIds", default=None
    )
    description: Optional[str] = None
