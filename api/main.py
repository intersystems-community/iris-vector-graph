import os
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from iris_vector_graph import IRISGraphEngine, gql
from iris_devtester.utils.dbapi_compat import get_connection as iris_connect
from api.routers.cypher import router as cypher_router


def get_engine():
    port = os.getenv("IRIS_PORT", "1972")
    conn = iris_connect(
        os.getenv("IRIS_HOST", "localhost"),
        int(port),
        os.getenv("IRIS_NAMESPACE", "USER"),
        os.getenv("IRIS_USER", "_SYSTEM"),
        os.getenv("IRIS_PASSWORD", "SYS")
    )
    return IRISGraphEngine(conn)


def create_app(engine: "IRISGraphEngine | None" = None) -> FastAPI:
    """Create the FastAPI application. If no engine provided, attempts to connect via env vars."""
    if engine is None:
        try:
            engine = get_engine()
        except Exception:
            # Tests may create the app without a live connection — use a stub engine
            engine = None

    if engine is not None:
        app = gql.create_app(engine)
        app.state.engine = engine
        app.state.conn = engine.conn
    else:
        app = FastAPI(title="IRIS Vector Graph API")

    @app.get("/health")
    def health():
        return {"status": "ok", "engine": engine is not None}

    app.include_router(cypher_router)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    return app


# Module-level app for test imports and ASGI servers
app = create_app()

if __name__ == "__main__":
    print("✓ IRIS Vector Graph API - Auto-Generated Platform")
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000)
