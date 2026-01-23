"""Demo session models for state management"""
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from datetime import datetime
from enum import Enum
import uuid


class DemoMode(str, Enum):
    """Demo mode selection"""
    FRAUD = "fraud"
    BIOMEDICAL = "biomedical"


class QueryHistoryEntry(BaseModel):
    """Single query record within a demo session"""
    query_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    query_type: str = Field(..., description="Type of query executed")
    query_params: Dict[str, Any] = Field(default_factory=dict)
    result_summary: Dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        json_schema_extra = {
            "example": {
                "query_id": "550e8400-e29b-41d4-a716-446655440000",
                "query_type": "fraud_score",
                "query_params": {"payer": "acct:user", "amount": 1500.00},
                "result_summary": {"fraud_probability": 0.15, "risk": "low"},
                "timestamp": "2025-01-06T14:30:00Z"
            }
        }


class DemoSession(BaseModel):
    """User's interaction session with demo system"""
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    mode: DemoMode = Field(default=DemoMode.FRAUD)
    query_history: List[QueryHistoryEntry] = Field(default_factory=list, max_length=100)
    visualization_state: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_activity: datetime = Field(default_factory=datetime.utcnow)

    def add_query(self, entry: QueryHistoryEntry) -> None:
        """Add query to history (max 100 queries)"""
        if len(self.query_history) >= 100:
            self.query_history.pop(0)  # Remove oldest
        self.query_history.append(entry)
        self.last_activity = datetime.utcnow()

    def switch_mode(self, new_mode: DemoMode) -> None:
        """Switch demo mode while preserving query history (FR-021)"""
        self.mode = new_mode
        self.last_activity = datetime.utcnow()

    def is_expired(self, timeout_minutes: int = 30) -> bool:
        """Check if session has expired (30min default)"""
        elapsed = (datetime.utcnow() - self.last_activity).total_seconds() / 60
        return elapsed > timeout_minutes

    class Config:
        json_schema_extra = {
            "example": {
                "session_id": "abc123",
                "mode": "fraud",
                "query_history": [],
                "visualization_state": {},
                "created_at": "2025-01-06T14:00:00Z",
                "last_activity": "2025-01-06T14:30:00Z"
            }
        }
