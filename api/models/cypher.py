"""
Pydantic Models for Cypher API Endpoint

Request and response models for POST /api/cypher endpoint.
Based on contracts/cypher_api.yaml.
"""

from pydantic import BaseModel, Field
from typing import List, Any, Dict, Optional


# ==============================================================================
# Request Models (cypher_api.yaml lines 153-183)
# ==============================================================================

class CypherQueryRequest(BaseModel):
    """
    Request model for POST /api/cypher endpoint.

    Example:
        {
          "query": "MATCH (p:Protein) WHERE p.id = $proteinId RETURN p.name",
          "parameters": {"proteinId": "PROTEIN:TP53"},
          "timeout": 60,
          "enableOptimization": true,
          "enableCache": true
        }
    """
    query: str = Field(
        ...,
        description="openCypher query string",
        examples=["MATCH (p:Protein) WHERE p.id = $proteinId RETURN p.name"]
    )

    parameters: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Named parameters for query (optional)",
        examples=[{"proteinId": "PROTEIN:TP53", "minScore": 0.8}]
    )

    timeout: int = Field(
        default=30,
        ge=1,
        le=300,
        description="Query execution timeout in seconds (default 30, max 300)"
    )

    enable_optimization: bool = Field(
        default=True,
        alias="enableOptimization",
        description="Enable query optimization (label pushdown, property pushdown)"
    )

    enable_cache: bool = Field(
        default=True,
        alias="enableCache",
        description="Enable query translation caching for repeated patterns"
    )

    class Config:
        populate_by_name = True  # Allow both snake_case and camelCase


# ==============================================================================
# Response Models - Success (cypher_api.yaml lines 185-248)
# ==============================================================================

class QueryMetadata(BaseModel):
    """Optional query execution metadata"""
    sql_query: Optional[str] = Field(
        default=None,
        alias="sqlQuery",
        description="Generated SQL query (for debugging, optional)"
    )

    indexes_used: Optional[List[str]] = Field(
        default=None,
        alias="indexesUsed",
        description="Database indexes used during execution"
    )

    optimizations_applied: Optional[List[str]] = Field(
        default=None,
        alias="optimizationsApplied",
        description="Query optimizations applied",
        examples=[["label_pushdown", "property_pushdown"]]
    )

    class Config:
        populate_by_name = True


class CypherQueryResponse(BaseModel):
    """
    Response model for successful Cypher query execution.

    Example:
        {
          "columns": ["name", "function"],
          "rows": [
            ["Tumor protein p53", "Tumor suppressor protein"],
            ["EGFR", "Receptor tyrosine kinase"]
          ],
          "rowCount": 2,
          "executionTimeMs": 12.3,
          "translationTimeMs": 2.1,
          "traceId": "cypher-20251002-abc123"
        }
    """
    columns: List[str] = Field(
        ...,
        description="Column names from RETURN clause",
        examples=[["protein_name", "interaction_count"]]
    )

    rows: List[List[Any]] = Field(
        ...,
        description="Result rows (array of arrays)",
        examples=[[["TP53", 127], ["EGFR", 93]]]
    )

    row_count: int = Field(
        ...,
        alias="rowCount",
        ge=0,
        description="Total number of rows returned"
    )

    execution_time_ms: float = Field(
        ...,
        alias="executionTimeMs",
        ge=0.0,
        description="Total query execution time in milliseconds"
    )

    translation_time_ms: float = Field(
        ...,
        alias="translationTimeMs",
        ge=0.0,
        description="Cypher-to-SQL translation time in milliseconds"
    )

    query_metadata: Optional[QueryMetadata] = Field(
        default=None,
        alias="queryMetadata",
        description="Optional query execution metadata"
    )

    trace_id: str = Field(
        ...,
        alias="traceId",
        description="Unique trace ID for debugging/logging",
        examples=["cypher-20251002-abc123"]
    )

    class Config:
        populate_by_name = True


# ==============================================================================
# Response Models - Error (cypher_api.yaml lines 250-303)
# ==============================================================================

class CypherErrorResponse(BaseModel):
    """
    Response model for Cypher query errors.

    Example:
        {
          "errorType": "syntax",
          "message": "Unexpected token 'RETRUN' at line 2, column 1",
          "line": 2,
          "column": 1,
          "errorCode": "SYNTAX_ERROR",
          "suggestion": "Did you mean 'RETURN'?",
          "traceId": "cypher-20251002-def456"
        }
    """
    error_type: str = Field(
        ...,
        alias="errorType",
        description="Category of error",
        examples=["syntax", "translation", "execution", "timeout"]
    )

    message: str = Field(
        ...,
        description="Human-readable error message",
        examples=["Unexpected token 'RETRUN' at line 2, column 1"]
    )

    line: Optional[int] = Field(
        default=None,
        ge=1,
        description="Line number in Cypher query where error occurred (if applicable)"
    )

    column: Optional[int] = Field(
        default=None,
        ge=1,
        description="Column number in Cypher query where error occurred (if applicable)"
    )

    error_code: str = Field(
        ...,
        alias="errorCode",
        description="Machine-readable error code",
        examples=["SYNTAX_ERROR", "UNDEFINED_VARIABLE", "QUERY_TIMEOUT"]
    )

    suggestion: Optional[str] = Field(
        default=None,
        description="Actionable suggestion to fix the error (optional)",
        examples=["Did you mean 'RETURN'?"]
    )

    trace_id: str = Field(
        ...,
        alias="traceId",
        description="Unique trace ID for debugging/logging",
        examples=["cypher-20251002-def456"]
    )

    sql_query: Optional[str] = Field(
        default=None,
        alias="sqlQuery",
        description="Generated SQL query that failed (for debugging, optional)"
    )

    class Config:
        populate_by_name = True


# ==============================================================================
# Error Code Constants
# ==============================================================================

class ErrorCode:
    """Machine-readable error codes (cypher_api.yaml lines 280-290)"""
    SYNTAX_ERROR = "SYNTAX_ERROR"
    UNDEFINED_VARIABLE = "UNDEFINED_VARIABLE"
    UNDEFINED_LABEL = "UNDEFINED_LABEL"
    UNDEFINED_PROPERTY = "UNDEFINED_PROPERTY"
    TYPE_MISMATCH = "TYPE_MISMATCH"
    COMPLEXITY_LIMIT_EXCEEDED = "COMPLEXITY_LIMIT_EXCEEDED"
    QUERY_TIMEOUT = "QUERY_TIMEOUT"
    FK_CONSTRAINT_VIOLATION = "FK_CONSTRAINT_VIOLATION"
    SQL_EXECUTION_ERROR = "SQL_EXECUTION_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"
