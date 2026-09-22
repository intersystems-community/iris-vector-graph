import os
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
from ..dependencies import get_db_connection, get_engine_optional
from iris_vector_graph.cypher.parser import parse_query, CypherParseError
from iris_vector_graph.cypher.translator import translate_to_sql
from iris_vector_graph.cypher import ast
from iris_vector_graph.api_auth import is_mutation_cypher

# Use iris-devtester dbapi_compat for IRIS connectivity
try:
    import iris
except ImportError:
    import intersystems_irispython as iris

router = APIRouter(prefix="/api/cypher", tags=["cypher"])
logger = logging.getLogger(__name__)

DEFAULT_MAX_HOPS = 10


def _max_hops_cap() -> int:
    """Largest variable-length path depth this endpoint will translate.

    The contract (`contracts/cypher_api.yaml`, the 413 response) caps a
    variable-length path at 10 hops. The translator's own ceiling is deliberately
    higher — spec 203 raised the unbounded `*` default from 10 to 100 so the
    openCypher TCK could pass — and both are right for their own caller: the library
    should translate what it is asked to, while an HTTP endpoint should refuse an
    expansion whose cost it cannot bound. Hence a cap here rather than there, and one
    an operator can raise (`IVG_CYPHER_MAX_HOPS`) for a trusted deployment.
    """
    raw = os.environ.get("IVG_CYPHER_MAX_HOPS", "")
    try:
        cap = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_MAX_HOPS
    return cap if cap > 0 else DEFAULT_MAX_HOPS


def _iter_patterns(query_ast):
    """Yield every graph pattern in the query, including UNION and multi-part bodies."""
    queries = [query_ast]
    queries.extend(getattr(query_ast, "union_queries", None) or [])
    queries.extend(getattr(query_ast, "subsequent_queries", None) or [])
    for query in queries:
        for part in getattr(query, "query_parts", None) or []:
            for clause in getattr(part, "clauses", None) or []:
                for pattern in getattr(clause, "patterns", None) or []:
                    yield pattern


def _enforce_depth_cap(query_ast) -> None:
    """Raise when a variable-length path asks for more hops than the cap allows.

    The message carries "complexity" so the translation-error handler below maps it to
    413 / COMPLEXITY_LIMIT_EXCEEDED, and carries both numbers so the caller can see
    what to reduce and to what.
    """
    cap = _max_hops_cap()
    for pattern in _iter_patterns(query_ast):
        for rel in getattr(pattern, "relationships", None) or []:
            var_len = getattr(rel, "variable_length", None)
            max_hops = getattr(var_len, "max_hops", None) if var_len else None
            if max_hops is not None and max_hops > cap:
                raise ValueError(
                    f"Variable-length path depth {max_hops} exceeds the maximum "
                    f"complexity limit of {cap} hops"
                )

# `response_model_exclude_none` and the `exclude_none=True` on every error
# `model_dump` below both say the same thing: an optional field in
# `contracts/cypher_api.yaml` is optional by being *absent*. Serialized as an
# explicit `null` it defeats the presence check the contract invites
# (`if "suggestion" in data: ...`) and hands the caller a None where the schema
# promised a string.
@router.post(
    "",
    response_model=Union[CypherQueryResponse, CypherErrorResponse],
    response_model_exclude_none=True,
)
async def execute_cypher_query(
    request: CypherQueryRequest,
    db_connection = Depends(get_db_connection),
    engine = Depends(get_engine_optional),
):
    """Execute an openCypher query against InterSystems IRIS"""
    trace_id = f"cypher-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    translation_start = time.time()

    # Read-only mode: reject mutation Cypher queries.
    if os.environ.get("IVG_READ_ONLY", "").lower() == "true":
        if is_mutation_cypher(request.query):
            return JSONResponse(
                status_code=403,
                content=CypherErrorResponse(
                    errorType="access",
                    message="mutation queries not allowed in read-only mode",
                    errorCode=ErrorCode.INTERNAL_ERROR,
                    traceId=trace_id,
                ).model_dump(by_alias=True, exclude_none=True),
            )

    try:
        # 1. Parse Cypher query
        query_ast = parse_query(request.query)
        _enforce_depth_cap(query_ast)

        # 2. Translate to SQL.
        #
        # The engine is not decoration here: `_apply_pagination` reads
        # `engine._fetch_first_unsafe` to decide between `TOP n` and `FETCH FIRST n
        # ROWS ONLY`, and on the IRIS AI builds the latter SIGSEGVs in %qaqpre when
        # the query also has a multi-table JOIN over VARCHAR keys — which every ivg
        # `MATCH ... RETURN <prop>` does. Translating with `engine=None` therefore
        # crashed the worker on any LIMIT query instead of answering it.
        sql_query = translate_to_sql(query_ast, params=request.parameters, engine=engine)
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
                        columns.append(f"{item.expression.variable}.{item.expression.property_name}")
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
            # Log the full error server-side for debugging; return generic message to client.
            logger.error("SQL execution error [%s]: %s", trace_id, str(e), exc_info=True)
            return JSONResponse(
                status_code=500,
                content=CypherErrorResponse(
                    errorType="execution",
                    message="query execution failed",
                    errorCode=ErrorCode.SQL_EXECUTION_ERROR,
                    traceId=trace_id,
                ).model_dump(by_alias=True, exclude_none=True)
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
            ).model_dump(by_alias=True, exclude_none=True)
        )

    # `SyntaxError` belongs here alongside `ValueError`: the translator raises it for
    # an undefined variable (`translator.py:9737`), which is precisely the case this
    # leg's default `UNDEFINED_VARIABLE` code was written for. Catching only
    # `ValueError` left that default unreachable and sent `RETURN m.name` with no `m`
    # bound to the generic 500 handler — a translation error reported as a server
    # fault, with the message replaced by "query execution failed".
    except (ValueError, SyntaxError) as e:
        error_msg = getattr(e, "msg", None) or str(e)
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
            ).model_dump(by_alias=True, exclude_none=True)
        )

    except Exception as e:
        logger.error("Unexpected error [%s]: %s", trace_id, str(e), exc_info=True)
        return JSONResponse(
            status_code=500,
            content=CypherErrorResponse(
                errorType="execution",
                message="query execution failed",
                errorCode=ErrorCode.INTERNAL_ERROR,
                traceId=trace_id
            ).model_dump(by_alias=True, exclude_none=True)
        )
