"""
Fraud Detection Domain for IRIS Vector Graph API

Domain-specific GraphQL types for fraud detection knowledge graphs.
Includes Account, Transaction, and Alert entities with pattern detection.

This is an EXAMPLE domain implementation demonstrating how to create
domain-specific types for fraud detection on top of the generic graph core.
"""

from examples.domains.fraud.types import (
    Account,
    Alert,
    CreateAccountInput,
    CreateAlertInput,
    CreateTransactionInput,
    SuspiciousPattern,
    Transaction,
)

__all__ = [
    "Account",
    "Transaction",
    "Alert",
    "SuspiciousPattern",
    "CreateAccountInput",
    "CreateTransactionInput",
    "CreateAlertInput",
]
