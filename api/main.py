import logging
import os
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from strawberry.fastapi import GraphQLRouter

from iris_vector_graph import IRISGraphEngine, gql
from iris_vector_graph.api_auth import ApiKeyMiddleware, ReadOnlyMiddleware
from iris_devtester.utils.dbapi_compat import get_connection as iris_connect
from api.routers.cypher import router as cypher_router
from api.routers.fhir_event import router as fhir_event_router
from api.gql.schema import schema as biomedical_schema
from api.gql.context import build_graphql_context

logger = logging.getLogger(__name__)


def get_engine():
    port = os.getenv("IRIS_PORT", "1972")
    conn = iris_connect(
        os.getenv("IRIS_HOST", "localhost"),
        int(port),
        os.getenv("IRIS_NAMESPACE", "USER"),
        os.getenv("IRIS_USER", "_SYSTEM"),
        os.getenv("IRIS_PASSWORD", "SYS")
    )
    # Warn when default superuser credentials are in use — change in production.
    if (os.getenv("IRIS_USER", "_SYSTEM") == "_SYSTEM"
            and os.getenv("IRIS_PASSWORD", "SYS") == "SYS"):
        logger.warning(
            "IRIS connection using default credentials (_SYSTEM/SYS) — "
            "set IRIS_USER and IRIS_PASSWORD in production"
        )
    return IRISGraphEngine(conn)


def create_app(engine: "IRISGraphEngine | None" = None) -> FastAPI:
    """Create the FastAPI application with the biomedical GraphQL schema."""
    if engine is None:
        # `engine.conn` runs the first-access check, so a namespace recording another IVG Schema raises there, not in
        # get_engine(); both stay inside the try, or the module-level fallback below builds the engine again and fails
        # the import.
        try:
            engine = get_engine()
            conn = engine.conn
        except Exception as exc:
            logger.warning("IRIS Vector Graph API starting without the database: %s", exc)
            engine = conn = None
    else:
        conn = engine.conn

    app = FastAPI(title="IRIS Vector Graph API")

    async def get_context():
        # Shared with api.gql.create_app: the generic resolvers read `engine` from the
        # context, and a second hand-rolled dict here is how `/graphql` came to answer
        # `nodes`/`node`/`stats` empty with no error.
        return build_graphql_context(engine)

    graphql_router = GraphQLRouter(biomedical_schema, context_getter=get_context)
    app.include_router(graphql_router, prefix="/graphql")

    @app.get("/")
    def root():
        return {"name": "IRIS Vector Graph API", "graphql_endpoint": "/graphql"}

    @app.get("/health")
    def health():
        db_status = "connected" if engine is not None else "unavailable"
        gql_status = "available"
        return {"status": "healthy", "database": db_status, "graphql": gql_status}

    app.include_router(cypher_router)
    app.include_router(fhir_event_router, prefix="/fhir-event", tags=["fhir-event"])

    # Read-only middleware (fhir-event write blocking). Added before auth, so auth runs
    # first: an unauthenticated write gets 401, not a 403 that reveals the mode.
    app.add_middleware(ReadOnlyMiddleware)

    # API key authentication — opt-in via IVG_API_KEY; default-deny (every route but
    # /health and the docs pages needs the key, the /graphql WebSocket included).
    app.add_middleware(ApiKeyMiddleware)

    # CORS outermost (added last): it answers preflights, which never carry the key,
    # and decorates the 401. Never combine wildcard origins with allow_credentials=True
    # (CORS spec §7.1.5).
    origins_raw = os.getenv("CORS_ORIGINS", "")
    origins = [o.strip() for o in origins_raw.split(",") if o.strip()]
    wildcard = not origins or origins == ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if wildcard else origins,
        allow_credentials=not wildcard,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if engine is not None:
        app.state.engine = engine
        app.state.conn = conn

    return app


# Module-level app for test imports and ASGI servers
try:
    app = create_app()
except Exception:
    app = create_app(engine=None)

if __name__ == "__main__":
    print("✓ IRIS Vector Graph API - Auto-Generated Platform")
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000)
