"""
Cypher Query Router

FastAPI router for POST /api/cypher endpoint.
Implements openCypher query execution against IRIS Vector Graph.
"""

import time
import uuid
import os
from typing import Any
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import JSONResponse
import iris

from api.models.cypher import (
    CypherQueryRequest,
    CypherQueryResponse,
    CypherErrorResponse,
    ErrorCode,
    QueryMetadata
)
from iris_vector_graph.cypher.parser import parse_query, CypherParseError
from iris_vector_graph.cypher.translator import translate_to_sql
from iris_vector_graph.cypher import ast


router = APIRouter(prefix="/api", tags=["Cypher Queries"])


def get_iris_connection():
    """
    Dependency to get IRIS database connection.

    Reads connection params from environment variables.
    """
    try:
        conn = iris.connect(
            os.getenv("IRIS_HOST", "localhost"),
            int(os.getenv("IRIS_PORT", "1972")),
            os.getenv("IRIS_NAMESPACE", "USER"),
            os.getenv("IRIS_USER", "_SYSTEM"),
            os.getenv("IRIS_PASSWORD", "SYS")
        )
        return conn
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to connect to IRIS database: {str(e)}"
        )


@router.post("/cypher")
async def execute_cypher_query(
    request: CypherQueryRequest,
    db_connection: Any = Depends(get_iris_connection)
) -> CypherQueryResponse | CypherErrorResponse:
    """
    Execute openCypher query against IRIS Vector Graph.

    **Request Body**:
    - `query` (required): openCypher query string
    - `parameters` (optional): Named parameters for query
    - `timeout` (optional): Query timeout in seconds (default: 30, max: 300)
    - `enableOptimization` (optional): Enable query optimization (default: true)
    - `enableCache` (optional): Enable query caching (default: true)

    **Response**:
    - Success (200): CypherQueryResponse with columns, rows, timing, traceId
    - Syntax Error (400): CypherErrorResponse with error details
    - Timeout (408): CypherErrorResponse with timeout message
    - Complexity Limit (413): CypherErrorResponse with limit exceeded
    - Execution Error (500): CypherErrorResponse with SQL error

    **Example**:
    ```
    POST /api/cypher
    {
      "query": "MATCH (p:Protein {id: $proteinId}) RETURN p.name",
      "parameters": {"proteinId": "PROTEIN:TP53"}
    }
    ```
    """
    trace_id = f"cypher-{uuid.uuid4().hex[:12]}"
    translation_start = time.time()

    try:
        # Parse Cypher query
        query_ast = parse_query(request.query, request.parameters)

        # Translate to SQL
        sql_query = translate_to_sql(query_ast)
        translation_time_ms = (time.time() - translation_start) * 1000

        # Execute SQL query
        execution_start = time.time()
        cursor = db_connection.cursor()

        try:
            cursor.execute(sql_query.sql, sql_query.parameters)
            rows = cursor.fetchall()
            execution_time_ms = (time.time() - execution_start) * 1000

            # Get column names from return clause
            columns = []
            if query_ast.return_clause:
                for item in query_ast.return_clause.items:
                    if item.alias:
                        columns.append(item.alias)
                    elif isinstance(item.expression, ast.PropertyReference):
                        columns.append(f"{item.expression.variable}.{item.expression.property_name}")
                    elif isinstance(item.expression, ast.Variable):
                        columns.append(item.expression.name)
                    elif isinstance(item.expression, ast.AggregationFunction):
                        columns.append(f"{item.expression.function_name}_res")
                    elif isinstance(item.expression, ast.FunctionCall):
                        columns.append(f"{item.expression.function_name}_res")
                    else:
                        columns.append("result")

            # Build response
            return CypherQueryResponse(
                columns=columns,
                rows=[list(row) for row in rows],
                rowCount=len(rows),
                executionTimeMs=execution_time_ms,
                translationTimeMs=translation_time_ms,
                queryMetadata=QueryMetadata(
                    sqlQuery=sql_query.sql if request.enable_optimization else None,
                    optimizationsApplied=sql_query.query_metadata.optimization_applied
                ),
                traceId=trace_id
            )

        except iris.Error as e:
            # SQL execution error
            error_message = str(e)

            # Check for FK constraint violation
            if "FOREIGN KEY" in error_message.upper():
                return JSONResponse(
                    status_code=500,
                    content=CypherErrorResponse(
                        errorType="execution",
                        message=f"Foreign key constraint violation: {error_message}",
                        errorCode=ErrorCode.FK_CONSTRAINT_VIOLATION,
                        traceId=trace_id,
                        sqlQuery=sql_query.sql
                    ).model_dump(by_alias=True)
                )
            else:
                return JSONResponse(
                    status_code=500,
                    content=CypherErrorResponse(
                        errorType="execution",
                        message=f"SQL execution failed: {error_message}",
                        errorCode=ErrorCode.SQL_EXECUTION_ERROR,
                        traceId=trace_id,
                        sqlQuery=sql_query.sql
                    ).model_dump(by_alias=True)
                )

    except CypherParseError as e:
        # Syntax error
        return JSONResponse(
            status_code=400,
            content=CypherErrorResponse(
                errorType="syntax",
                message=e.message,
                line=e.line,
                column=e.column,
                errorCode=ErrorCode.SYNTAX_ERROR,
                suggestion=e.suggestion,
                traceId=trace_id
            ).model_dump(by_alias=True)
        )

    except ValueError as e:
        # Translation error (undefined variable, complexity limit, etc.)
        error_message = str(e)

        # Check for complexity limit
        if "complexity" in error_message.lower() or "max_hops" in error_message.lower():
            return JSONResponse(
                status_code=413,
                content=CypherErrorResponse(
                    errorType="translation",
                    message=error_message,
                    errorCode=ErrorCode.COMPLEXITY_LIMIT_EXCEEDED,
                    suggestion="Reduce max_hops in variable-length path pattern",
                    traceId=trace_id
                ).model_dump(by_alias=True)
            )
        else:
            # Generic translation error
            return JSONResponse(
                status_code=400,
                content=CypherErrorResponse(
                    errorType="translation",
                    message=error_message,
                    errorCode=ErrorCode.UNDEFINED_VARIABLE,
                    traceId=trace_id
                ).model_dump(by_alias=True)
            )

    except Exception as e:
        # Unexpected error
        return JSONResponse(
            status_code=500,
            content=CypherErrorResponse(
                errorType="execution",
                message=f"Internal server error: {str(e)}",
                errorCode=ErrorCode.INTERNAL_ERROR,
                traceId=trace_id
            ).model_dump(by_alias=True)
        )
    finally:
        # Close connection
        try:
            db_connection.close()
        except:
            pass
