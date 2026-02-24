import os
import uvicorn
from iris_vector_graph import IRISGraphEngine, gql
from iris_devtester.utils.dbapi_compat import get_connection as iris_connect
from api.routers.cypher import router as cypher_router
from fastapi.middleware.cors import CORSMiddleware

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

if __name__ == "__main__":
    engine = get_engine()
    
    # Create the auto-generated app
    app = gql.create_app(engine)
    
    # Add back the Cypher router
    app.include_router(cypher_router)
    
    # Add CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    print("✓ IRIS Vector Graph API - Auto-Generated Platform")
    uvicorn.run(app, host="0.0.0.0", port=8000)
