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
from iris_vector_graph_core.cypher.parser import parse_query, CypherParseError
from iris_vector_graph_core.cypher.translator import translate_to_sql


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
        ast = parse_query(request.query, request.parameters)

        # Translate to SQL
        sql_query = translate_to_sql(ast)
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
            for item in ast.return_clause.items:
                if item.alias:
                    columns.append(item.alias)
                elif hasattr(item.expression, 'property_name'):
                    # PropertyReference
                    columns.append(f"{item.expression.variable}.{item.expression.property_name}")
                else:
                    # Variable
                    columns.append(item.expression.name)

            # Build response
            return CypherQueryResponse(
                columns=columns,
                rows=[list(row) for row in rows],
                row_count=len(rows),
                execution_time_ms=execution_time_ms,
                translation_time_ms=translation_time_ms,
                query_metadata=QueryMetadata(
                    sql_query=sql_query.sql if request.enable_optimization else None,
                    optimizations_applied=sql_query.query_metadata.optimization_applied
                ),
                trace_id=trace_id
            )

        except iris.Error as e:
            # SQL execution error
            error_message = str(e)

            # Check for FK constraint violation
            if "FOREIGN KEY" in error_message.upper():
                return JSONResponse(
                    status_code=500,
                    content=CypherErrorResponse(
                        error_type="execution",
                        message=f"Foreign key constraint violation: {error_message}",
                        error_code=ErrorCode.FK_CONSTRAINT_VIOLATION,
                        trace_id=trace_id,
                        sql_query=sql_query.sql
                    ).model_dump(by_alias=True)
                )
            else:
                return JSONResponse(
                    status_code=500,
                    content=CypherErrorResponse(
                        error_type="execution",
                        message=f"SQL execution failed: {error_message}",
                        error_code=ErrorCode.SQL_EXECUTION_ERROR,
                        trace_id=trace_id,
                        sql_query=sql_query.sql
                    ).model_dump(by_alias=True)
                )

    except CypherParseError as e:
        # Syntax error
        return JSONResponse(
            status_code=400,
            content=CypherErrorResponse(
                error_type="syntax",
                message=e.message,
                line=e.line,
                column=e.column,
                error_code=ErrorCode.SYNTAX_ERROR,
                suggestion=e.suggestion,
                trace_id=trace_id
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
                    error_type="translation",
                    message=error_message,
                    error_code=ErrorCode.COMPLEXITY_LIMIT_EXCEEDED,
                    suggestion="Reduce max_hops in variable-length path pattern",
                    trace_id=trace_id
                ).model_dump(by_alias=True)
            )
        else:
            # Generic translation error
            return JSONResponse(
                status_code=400,
                content=CypherErrorResponse(
                    error_type="translation",
                    message=error_message,
                    error_code=ErrorCode.UNDEFINED_VARIABLE,
                    trace_id=trace_id
                ).model_dump(by_alias=True)
            )

    except Exception as e:
        # Unexpected error
        return JSONResponse(
            status_code=500,
            content=CypherErrorResponse(
                error_type="execution",
                message=f"Internal server error: {str(e)}",
                error_code=ErrorCode.INTERNAL_ERROR,
                trace_id=trace_id
            ).model_dump(by_alias=True)
        )
    finally:
        # Close connection
        try:
            db_connection.close()
        except:
            pass
