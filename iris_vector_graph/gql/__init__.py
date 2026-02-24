import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from strawberry.fastapi import GraphQLRouter
from typing import Optional, Any
import logging

from ..engine import IRISGraphEngine
from .engine import GQLGraphEngine
from .schema import build_schema
from .pooling import get_pool

logger = logging.getLogger(__name__)

def create_app(engine: IRISGraphEngine, prefix: str = "/graphql") -> FastAPI:
    """
    Creates a FastAPI app with auto-generated GraphQL schema.
    """
    gql_engine = GQLGraphEngine(engine)
    schema = build_schema(gql_engine)
    app = FastAPI(title="IRIS Vector Graph Auto-Generated API")
    
    # Context getter for resolvers
    async def get_context():
        return {
            "engine": engine,
            "gql_engine": gql_engine,
            "pool": await get_pool(engine)
        }

    # Exception handlers
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        err_msg = str(exc)
        if "Access Denied" in err_msg:
            return JSONResponse(status_code=403, content={"error": "IRIS Access Denied", "details": err_msg})
        if "License Limit" in err_msg or "Too many connections" in err_msg:
            return JSONResponse(status_code=503, content={"error": "IRIS License Limit Reached", "details": err_msg})
        
        logger.error(f"Unhandled exception: {exc}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": "Internal Server Error", "details": err_msg})

    graphql_app = GraphQLRouter(schema, context_getter=get_context)
    app.include_router(graphql_app, prefix=prefix)
    
    @app.get("/")
    async def root():
        return {"message": "IRIS Vector Graph GraphQL API is running", "graphql_endpoint": prefix}
    
    return app

def serve(
    engine: IRISGraphEngine,
    host: str = "0.0.0.0",
    port: int = 8000,
    prefix: str = "/graphql",
    **kwargs
):
    """
    Auto-generates and starts a GraphQL server over an IRIS graph store.
    """
    app = create_app(engine, prefix=prefix)
    print(f"Starting auto-generated GraphQL server at http://{host}:{port}{prefix}")
    uvicorn.run(app, host=host, port=port, **kwargs)
