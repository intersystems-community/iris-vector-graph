"""Fraud detection models for Financial Services demo"""
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional
from datetime import datetime
from decimal import Decimal
from enum import Enum


class RiskClassification(str, Enum):
    """Human-readable risk levels (FR-007)"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FraudTransactionQuery(BaseModel):
    """User-submitted transaction for fraud scoring (FR-006)"""
    payer: str = Field(..., pattern=r'^acct:.+', max_length=100,
                      description="Payer account identifier")
    payee: Optional[str] = Field(None, pattern=r'^acct:.+',
                                description="Payee account identifier")
    amount: float = Field(..., gt=0, le=1_000_000.00,
                         description="Transaction amount")
    device: str = Field(..., pattern=r'^dev:.+', max_length=100,
                       description="Device identifier")
    merchant: Optional[str] = Field(None, pattern=r'^merch:.+',
                                   description="Merchant identifier")
    ip_address: Optional[str] = Field(None, description="IP address")
    timestamp: Optional[datetime] = Field(default_factory=datetime.utcnow)

    class Config:
        json_schema_extra = {
            "example": {
                "payer": "acct:user_12345",
                "amount": 1500.00,
                "device": "dev:laptop_001",
                "merchant": "merch:store_789",
                "ip_address": "192.168.1.100"
            }
        }


class FraudScoringResult(BaseModel):
    """Fraud probability and risk assessment (FR-007)"""
    fraud_probability: float = Field(..., ge=0.0, le=1.0,
                                    description="Probability score 0.0-1.0")
    risk_classification: RiskClassification
    contributing_factors: List[str] = Field(default_factory=list)
    scoring_timestamp: datetime = Field(default_factory=datetime.utcnow)
    scoring_model: str = Field(..., description="Model version used")
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)

    @classmethod
    def from_probability(cls, prob: float, model: str = "demo",
                        factors: Optional[List[str]] = None) -> "FraudScoringResult":
        """Create result with auto-classification from probability"""
        # Map probability to risk classification
        if prob < 0.30:
            risk = RiskClassification.LOW
        elif prob < 0.60:
            risk = RiskClassification.MEDIUM
        elif prob < 0.85:
            risk = RiskClassification.HIGH
        else:
            risk = RiskClassification.CRITICAL

        return cls(
            fraud_probability=prob,
            risk_classification=risk,
            contributing_factors=factors or [],
            scoring_model=model,
            scoring_timestamp=datetime.utcnow(),
            confidence=1.0
        )

    class Config:
        json_schema_extra = {
            "example": {
                "fraud_probability": 0.92,
                "risk_classification": "critical",
                "contributing_factors": [
                    "High transaction amount",
                    "New device fingerprint"
                ],
                "scoring_timestamp": "2025-01-06T14:30:05Z",
                "scoring_model": "MLP",
                "confidence": 0.87
            }
        }


class BitemporalQuery(BaseModel):
    """Time-travel query parameters (FR-008)"""
    event_id: str = Field(..., description="Transaction identifier")
    system_time: datetime = Field(..., description="When did we know?")
    valid_time: Optional[datetime] = Field(None, description="When did it happen?")
    query_mode: str = Field(default="as_of", pattern=r'^(as_of|diff|audit_trail)$')

    class Config:
        json_schema_extra = {
            "example": {
                "event_id": "txn_2025_001234",
                "system_time": "2024-12-31T23:59:59Z",
                "query_mode": "as_of"
            }
        }


class BitemporalResult(BaseModel):
    """Historical version of transaction data (FR-009)"""
    event_id: str
    version_id: int = Field(..., ge=1)
    valid_from: datetime
    valid_to: Optional[datetime] = None
    system_from: datetime
    system_to: Optional[datetime] = None  # NULL = current version
    fraud_score: float = Field(..., ge=0.0, le=1.0)
    fraud_status: str = Field(..., pattern=r'^(clean|suspicious|confirmed_fraud|reversed)$')
    changed_by: Optional[str] = None
    change_reason: Optional[str] = None
    data_snapshot: dict = Field(default_factory=dict)

    class Config:
        json_schema_extra = {
            "example": {
                "event_id": "txn_2025_001",
                "version_id": 2,
                "valid_from": "2025-01-15T10:30:00Z",
                "valid_to": None,
                "system_from": "2025-01-15T14:30:00Z",
                "system_to": None,
                "fraud_score": 0.65,
                "fraud_status": "suspicious",
                "changed_by": "fraud_detection_system",
                "change_reason": "Late arrival detected"
            }
        }


class LateArrivalTransaction(BaseModel):
    """Transaction reported >24h after occurrence (FR-010)"""
    event_id: str
    valid_from: datetime  # When it actually happened
    system_from: datetime  # When system learned about it
    delay_hours: float = Field(..., gt=24)
    amount: float
    payer: str
    device: str
    suspicion_flags: List[str] = Field(default_factory=list)

    class Config:
        json_schema_extra = {
            "example": {
                "event_id": "txn_late_001",
                "valid_from": "2025-01-10T10:00:00Z",
                "system_from": "2025-01-12T16:00:00Z",
                "delay_hours": 54.0,
                "amount": 5000.00,
                "payer": "acct:suspicious_001",
                "device": "dev:unknown",
                "suspicion_flags": ["Settlement delay", "High amount", "New device"]
            }
        }
