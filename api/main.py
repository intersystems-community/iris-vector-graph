"""
FastAPI application with Cypher API endpoint.

Provides /api/cypher endpoint for openCypher query execution.
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers.cypher import router as cypher_router


# Database connection configuration
IRIS_HOST = os.getenv("IRIS_HOST", "localhost")
IRIS_PORT = int(os.getenv("IRIS_PORT", "1972"))
IRIS_NAMESPACE = os.getenv("IRIS_NAMESPACE", "USER")
IRIS_USER = os.getenv("IRIS_USER", "_SYSTEM")
IRIS_PASSWORD = os.getenv("IRIS_PASSWORD", "SYS")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan context manager for startup/shutdown"""
    # Startup
    print(f"✓ IRIS Vector Graph API - Cypher Endpoint")
    print(f"  Connecting to IRIS at {IRIS_HOST}:{IRIS_PORT}/{IRIS_NAMESPACE}")
    yield
    # Shutdown
    print("✓ Shutting down")


# Create FastAPI application
app = FastAPI(
    title="IRIS Vector Graph - openCypher API",
    description="openCypher query execution for IRIS Vector Graph database",
    version="1.0.0",
    lifespan=lifespan
)


# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Include routers
app.include_router(cypher_router)


@app.get("/")
async def root():
    """API information"""
    return {
        "name": "IRIS Vector Graph - openCypher API",
        "version": "1.0.0",
        "cypher_endpoint": "/api/cypher",
        "docs": "/docs"
    }


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    import iris

    try:
        # Test database connection
        conn = iris.connect(
            IRIS_HOST,
            IRIS_PORT,
            IRIS_NAMESPACE,
            IRIS_USER,
            IRIS_PASSWORD
        )
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        conn.close()

        return {
            "status": "healthy",
            "database": "connected",
            "cypher": "available"
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "database": "error",
            "error": str(e)
        }
