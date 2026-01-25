"""
FastAPI application with GraphQL and openCypher API endpoints.

Provides:
- /graphql endpoint with GraphQL Playground UI and vector graph operations
- /api/cypher endpoint for openCypher query execution
"""

import os
from typing import Dict, Any, Generator
from contextlib import asynccontextmanager

from iris_devtester.utils.dbapi_compat import get_connection as iris_connect
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


def get_db_config():
    """Get database configuration from environment variables"""
    port = os.getenv("IRIS_PORT", "1972")
    return {
        "host": os.getenv("IRIS_HOST", "localhost"),
        "port": int(port),
        "namespace": os.getenv("IRIS_NAMESPACE", "USER"),
        "user": os.getenv("IRIS_USER", "_SYSTEM"),
        "password": os.getenv("IRIS_PASSWORD", "SYS"),
    }


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

        config = get_db_config()
        conn = iris_connect(
            config["host"],
            config["port"],
            config["namespace"],
            config["user"],
            config["password"]
        )
        # Ensure schema is set
        schema = os.getenv("IRIS_SCHEMA", "SQLUser")
        try:
            cursor = conn.cursor()
            cursor.execute(f"SET OPTION %SCHEMA = '{schema}'")
            cursor.close()
        except:
            pass
        return conn

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
# NOTE: IRIS Community Edition has 5 concurrent connection limit
connection_pool = ConnectionPool(max_connections=3)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan context manager for startup/shutdown"""
    # Startup: Initialize connection pool
    config = get_db_config()
    print(f"✓ IRIS Vector Graph API - Multi-Query-Engine Platform")
    print(f"  GraphQL endpoint: /graphql")
    print(f"  openCypher endpoint: /api/cypher")
    print(f"  Connecting to IRIS at {config['host']}:{config['port']}/{config['namespace']}")
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


def get_db_connection():
    """Get database connection from pool with schema initialized"""
    return connection_pool.get_connection()


async def get_context() -> Dict[str, Any]:
    """
    Build GraphQL context with database connection and DataLoaders.

    Creates request-scoped DataLoaders for batching and caching.
    Each request gets fresh loaders to prevent data staleness.
    
    NOTE: Creates a fresh connection per request to avoid connection
    exhaustion issues with IRIS CE's 5-connection limit. The connection
    is stored in context but should be used synchronously within request.
    """
    # Create fresh connection for this request (not from pool)
    # This ensures connection is not held across requests
    config = get_db_config()
    db_connection = iris_connect(
        config["host"],
        config["port"],
        config["namespace"],
        config["user"],
        config["password"]
    )
    
    # Set schema
    schema = os.getenv("IRIS_SCHEMA", "SQLUser")
    try:
        cursor = db_connection.cursor()
        cursor.execute(f"SET OPTION %SCHEMA = '{schema}'")
        cursor.close()
    except:
        pass

    # Create request-scoped DataLoaders
    # owns_connection=True tells the extension to close this connection
    # when the request ends (prevents license exhaustion on IRIS CE)
    context = {
        "db_connection": db_connection,
        "owns_connection": True,  # Extension will close after request
        "protein_loader": ProteinLoader(db_connection),
        "gene_loader": GeneLoader(db_connection),
        "pathway_loader": PathwayLoader(db_connection),
        "edge_loader": EdgeLoader(db_connection),
        "property_loader": PropertyLoader(db_connection),
        "label_loader": LabelLoader(db_connection),
    }

    return context


# Create GraphQL router with Strawberry
# NOTE: DatabaseConnectionExtension is added to schema in api/gql/schema.py
graphql_app = GraphQLRouter(
    schema,
    context_getter=get_context,
    graphiql=True,  # Enable GraphQL Playground UI
)


# Mount GraphQL endpoint
app.include_router(graphql_app, prefix="/graphql")

# Mount openCypher endpoint
app.include_router(cypher_router)


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
            "cypher": "available"
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "database": f"error: {str(e)}",
            "graphql": "unavailable",
            "cypher": "unavailable"
        }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )
