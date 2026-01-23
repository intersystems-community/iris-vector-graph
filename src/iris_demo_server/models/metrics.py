"""Performance metrics models for observability (FR-019)"""
from pydantic import BaseModel, Field
from typing import List
from datetime import datetime


class QueryPerformanceMetrics(BaseModel):
    """Execution metrics displayed to user (FR-019)"""
    query_type: str = Field(..., description="Type of query executed")
    execution_time_ms: int = Field(..., ge=0, description="Total query time in ms")
    backend_used: str = Field(..., description="Which backend serviced the query")
    result_count: int = Field(..., ge=0, description="Number of results returned")
    search_methods: List[str] = Field(default_factory=list,
                                     description="Methods used (e.g., HNSW, BM25, RRF)")
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        json_schema_extra = {
            "example": {
                "query_type": "fraud_score",
                "execution_time_ms": 127,
                "backend_used": "fraud_api",
                "result_count": 1,
                "search_methods": ["MLP"],
                "timestamp": "2025-01-06T14:35:22Z"
            }
        }
