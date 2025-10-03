"""
FastAPI application with GraphQL and openCypher API endpoints.

Provides:
- /graphql endpoint with GraphQL Playground UI and vector graph operations
- /api/cypher endpoint for openCypher query execution
"""

import os
from typing import Dict, Any
from contextlib import asynccontextmanager

import iris
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from strawberry.fastapi import GraphQLRouter

from api.gql.schema import schema
from api.gql.core.loaders import (
    EdgeLoader,
    PropertyLoader,
    LabelLoader,
)

# Import biomedical domain loaders (example domain)
from examples.domains.biomedical.loaders import (
    ProteinLoader,
    GeneLoader,
    PathwayLoader,
)

# Import openCypher router
from api.routers.cypher import router as cypher_router

# Import fraud scoring router
from api.routers.fraud import router as fraud_router


# Database connection configuration
IRIS_HOST = os.getenv("IRIS_HOST", "localhost")
IRIS_PORT = int(os.getenv("IRIS_PORT", "1972"))
IRIS_NAMESPACE = os.getenv("IRIS_NAMESPACE", "USER")
IRIS_USER = os.getenv("IRIS_USER", "_SYSTEM")
IRIS_PASSWORD = os.getenv("IRIS_PASSWORD", "SYS")


# Database connection pool
class ConnectionPool:
    """Simple connection pool for IRIS database"""

    def __init__(self, max_connections: int = 10):
        self.max_connections = max_connections
        self._connections = []

    def get_connection(self):
        """Get database connection from pool"""
        if self._connections:
            return self._connections.pop()

        # Create new connection
        return iris.connect(
            IRIS_HOST,
            IRIS_PORT,
            IRIS_NAMESPACE,
            IRIS_USER,
            IRIS_PASSWORD
        )

    def release_connection(self, conn):
        """Return connection to pool"""
        if len(self._connections) < self.max_connections:
            self._connections.append(conn)
        else:
            conn.close()

    def close_all(self):
        """Close all connections in pool"""
        for conn in self._connections:
            conn.close()
        self._connections.clear()


# Global connection pool
connection_pool = ConnectionPool(max_connections=10)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan context manager for startup/shutdown"""
    # Startup: Initialize connection pool
    print(f"✓ IRIS Vector Graph API - Multi-Query-Engine Platform")
    print(f"  GraphQL endpoint: /graphql")
    print(f"  openCypher endpoint: /api/cypher")
    print(f"  Fraud scoring endpoint: /fraud/score")
    print(f"  Connecting to IRIS at {IRIS_HOST}:{IRIS_PORT}/{IRIS_NAMESPACE}")
    yield
    # Shutdown: Close all connections
    connection_pool.close_all()
    print("✓ Closed all IRIS connections")


# Create FastAPI application
app = FastAPI(
    title="IRIS Vector Graph - Multi-Query-Engine API",
    description="GraphQL and openCypher query engines for IRIS Vector Graph database",
    version="1.0.0",
    lifespan=lifespan
)


# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def get_context() -> Dict[str, Any]:
    """
    Build GraphQL context with database connection and DataLoaders.

    Creates request-scoped DataLoaders for batching and caching.
    Each request gets fresh loaders to prevent data staleness.
    """
    # Get database connection from pool
    db_connection = connection_pool.get_connection()

    # Create request-scoped DataLoaders
    context = {
        "db_connection": db_connection,
        "protein_loader": ProteinLoader(db_connection),
        "gene_loader": GeneLoader(db_connection),
        "pathway_loader": PathwayLoader(db_connection),
        "edge_loader": EdgeLoader(db_connection),
        "property_loader": PropertyLoader(db_connection),
        "label_loader": LabelLoader(db_connection),
    }

    return context


# Create GraphQL router with Strawberry
graphql_app = GraphQLRouter(
    schema,
    context_getter=get_context,
    graphiql=True,  # Enable GraphQL Playground UI
)


# Mount GraphQL endpoint
app.include_router(graphql_app, prefix="/graphql")

# Mount openCypher endpoint
app.include_router(cypher_router)

# Mount fraud scoring endpoint
app.include_router(fraud_router)


@app.get("/")
async def root():
    """Root endpoint with API information"""
    return {
        "name": "IRIS Vector Graph - Multi-Query-Engine API",
        "version": "1.0.0",
        "query_engines": {
            "graphql": {
                "endpoint": "/graphql",
                "playground": "/graphql (open in browser)",
                "description": "Type-safe queries with DataLoader batching"
            },
            "opencypher": {
                "endpoint": "/api/cypher",
                "docs": "/docs#/Cypher%20Queries",
                "description": "Pattern matching with Cypher-to-SQL translation"
            },
            "sql": {
                "method": "iris.connect()",
                "description": "Native IRIS SQL for direct access"
            },
            "fraud": {
                "endpoint": "/fraud/score",
                "health": "/fraud/health",
                "description": "Real-time fraud scoring with TorchScript ML"
            }
        },
        "status": "running"
    }


@app.get("/health")
async def health_check():
    """Health check endpoint for monitoring"""
    try:
        # Test database connection
        conn = connection_pool.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.fetchone()
        connection_pool.release_connection(conn)

        return {
            "status": "healthy",
            "database": "connected",
            "graphql": "available",
            "cypher": "available",
            "fraud": "available"
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "database": f"error: {str(e)}",
            "graphql": "unavailable",
            "cypher": "unavailable",
            "fraud": "unavailable"
        }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )
