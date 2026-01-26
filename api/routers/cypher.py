from fastapi import APIRouter, Depends, HTTPException, Body
from fastapi.responses import JSONResponse
import time
import logging
import uuid
from typing import List, Any, Dict, Optional, Union

from ..models.cypher import (
    CypherQueryRequest, 
    CypherQueryResponse, 
    CypherErrorResponse,
    QueryMetadata,
    ErrorCode
)
from ..dependencies import get_db_connection
from iris_vector_graph.cypher.parser import parse_query, CypherParseError
from iris_vector_graph.cypher.translator import translate_to_sql
from iris_vector_graph.cypher import ast

# Use iris-devtester dbapi_compat for IRIS connectivity
try:
    import iris
except ImportError:
    import intersystems_irispython as iris

router = APIRouter(prefix="/api/cypher", tags=["cypher"])
logger = logging.getLogger(__name__)

@router.post("", response_model=Union[CypherQueryResponse, CypherErrorResponse])
async def execute_cypher_query(
    request: CypherQueryRequest,
    db_connection = Depends(get_db_connection)
):
    """Execute an openCypher query against InterSystems IRIS"""
    trace_id = f"cypher-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    translation_start = time.time()
    
    try:
        # 1. Parse Cypher query
        query_ast = parse_query(request.query)

        # 2. Translate to SQL
        sql_query = translate_to_sql(query_ast, params=request.parameters)
        translation_time_ms = (time.time() - translation_start) * 1000

        # 3. Execute SQL query
        execution_start = time.time()
        cursor = db_connection.cursor()
        rows = []
        sql_text = ""

        try:
            if sql_query.is_transactional:
                cursor.execute("START TRANSACTION")
                try:
                    stmts = sql_query.sql if isinstance(sql_query.sql, list) else [sql_query.sql]
                    all_params = sql_query.parameters
                    
                    for i, stmt in enumerate(stmts[:-1]):
                        params = all_params[i] if i < len(all_params) else []
                        cursor.execute(stmt, params)
                    
                    if len(stmts) > 0:
                        last_stmt = stmts[-1]
                        last_params = all_params[-1] if len(all_params) >= len(stmts) else []
                        cursor.execute(last_stmt, last_params)
                        if cursor.description:
                            rows = cursor.fetchall()
                    
                    cursor.execute("COMMIT")
                    sql_text = "\n".join(stmts) if isinstance(sql_query.sql, list) else str(sql_query.sql)
                except Exception as e:
                    cursor.execute("ROLLBACK")
                    raise e
            else:
                sql_str = sql_query.sql if isinstance(sql_query.sql, str) else "\n".join(sql_query.sql)
                sql_text = sql_str
                params = sql_query.parameters[0] if sql_query.parameters else []
                cursor.execute(sql_str, params)
                rows = cursor.fetchall()
            
            execution_time_ms = (time.time() - execution_start) * 1000

            # 4. Build success response
            columns = []
            if query_ast.return_clause:
                for item in query_ast.return_clause.items:
                    if item.alias:
                        columns.append(item.alias)
                    elif isinstance(item.expression, ast.PropertyReference):
                        columns.append(f"{item.expression.variable}_{item.expression.property_name}")
                    elif isinstance(item.expression, ast.Variable):
                        columns.append(item.expression.name)
                    elif isinstance(item.expression, (ast.AggregationFunction, ast.FunctionCall)):
                        columns.append(f"{item.expression.function_name}_res")
                    else:
                        columns.append("result")

            return CypherQueryResponse(
                columns=columns,
                rows=[list(row) for row in rows],
                rowCount=len(rows),
                executionTimeMs=execution_time_ms,
                translationTimeMs=translation_time_ms,
                queryMetadata=QueryMetadata(
                    sqlQuery=sql_text if request.enable_optimization else None,
                    optimizationsApplied=sql_query.query_metadata.optimization_applied
                ),
                traceId=trace_id
            )

        except Exception as e:
            # SQL execution error
            error_msg = str(e)
            error_code = ErrorCode.SQL_EXECUTION_ERROR
            if "FOREIGN KEY" in error_msg.upper():
                error_code = ErrorCode.FK_CONSTRAINT_VIOLATION
                
            return JSONResponse(
                status_code=500,
                content=CypherErrorResponse(
                    errorType="execution",
                    message=f"SQL execution failed: {error_msg}",
                    errorCode=error_code,
                    traceId=trace_id,
                    sqlQuery=sql_text
                ).model_dump(by_alias=True)
            )

    except CypherParseError as e:
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
        error_msg = str(e)
        status = 400
        error_code = ErrorCode.UNDEFINED_VARIABLE
        if "complexity" in error_msg.lower():
            status = 413
            error_code = ErrorCode.COMPLEXITY_LIMIT_EXCEEDED
            
        return JSONResponse(
            status_code=status,
            content=CypherErrorResponse(
                errorType="translation",
                message=error_msg,
                errorCode=error_code,
                traceId=trace_id
            ).model_dump(by_alias=True)
        )

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content=CypherErrorResponse(
                errorType="execution",
                message=f"Internal error: {str(e)}",
                errorCode=ErrorCode.INTERNAL_ERROR,
                traceId=trace_id
            ).model_dump(by_alias=True)
        )
