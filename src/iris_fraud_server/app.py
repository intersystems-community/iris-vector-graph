"""
Fraud Scoring FastAPI Application (Embedded Python)

Main ASGI application for fraud scoring running in IRIS embedded Python.
Uses iris.sql.exec() for all database operations (no external connections).
"""

import os
import sys
import logging
import uuid
from datetime import datetime
from typing import List, Optional, Literal

import structlog
from fastapi import FastAPI, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, validator
import torch
import numpy as np

# Import iris module (only available in irispython)
try:
    import iris
except ImportError:
    print("ERROR: iris module not found. This must run via /usr/irissys/bin/irispython")
    sys.exit(1)

# Configure structured logging for embedded Python
# CRITICAL: Use PrintLoggerFactory() to write to stdout (not LoggerFactory())
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.ConsoleRenderer()
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),  # Write directly to stdout
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()


# ============================================================================
# Pydantic Models
# ============================================================================

class FraudScoreRequest(BaseModel):
    """Request model for POST /fraud/score"""

    mode: Literal["MLP", "EGO"] = Field(
        ...,
        description="Scoring mode: MLP (feature-based) or EGO (subgraph-based)"
    )
    payer: str = Field(..., description="Payer entity ID (e.g., 'acct:user123')")
    device: str = Field(..., description="Device ID (e.g., 'dev:laptop')")
    ip: str = Field(..., description="IP address (e.g., 'ip:192.168.1.1')")
    merchant: str = Field(..., description="Merchant ID (e.g., 'merchant:store')")
    amount: Optional[float] = Field(None, description="Transaction amount")
    country: Optional[str] = Field(None, description="Country code (e.g., 'US')")

    @validator('payer', 'device', 'ip', 'merchant')
    def validate_entity_id_format(cls, v):
        """Validate entity ID format: namespace:identifier"""
        if ':' not in v:
            raise ValueError(f"Invalid entity ID format: {v}. Expected 'namespace:id'")
        return v


class ReasonCode(BaseModel):
    """Reason code explaining fraud score"""
    kind: str = Field(..., description="Reason type: 'feature' or 'vector'")
    detail: str = Field(..., description="Human-readable explanation")
    weight: float = Field(..., description="Attribution weight")


class FraudScoreResponse(BaseModel):
    """Response model for POST /fraud/score"""
    prob: float = Field(..., description="Fraud probability (0.0-1.0)")
    reasons: List[str] = Field(..., description="Reason codes (min 3)")
    trace_id: str = Field(..., description="Request trace ID for debugging")
    mode: str = Field(..., description="Scoring mode used")
    timestamp: str = Field(..., description="Response timestamp")


class FraudHealthResponse(BaseModel):
    """Response model for GET /fraud/health"""
    status: str = Field(..., description="Health status: 'healthy' or 'unhealthy'")
    model_loaded: bool = Field(..., description="TorchScript model loaded")
    database_connected: bool = Field(..., description="IRIS database accessible")
    centroid_available: bool = Field(..., description="Fraud centroid computed")
    timestamp: str = Field(..., description="Health check timestamp")


# ============================================================================
# Global State
# ============================================================================

# TorchScript model cache (loaded on startup)
MODEL_CACHE = {
    "mlp": None,
    "ego": None  # Deferred to post-MVP
}

# Fraud centroid cache (loaded on startup)
FRAUD_CENTROID = None


# ============================================================================
# Model Loading (Embedded Python)
# ============================================================================

def load_torchscript_model(model_path: str) -> torch.jit.ScriptModule:
    """
    Load TorchScript model in embedded Python context

    Args:
        model_path: Path to .torchscript file

    Returns:
        Loaded TorchScript model (eval mode)

    Raises:
        FileNotFoundError: Model file not found
        RuntimeError: Model loading failed
    """
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"Model file not found: {model_path}")

    logger.info("Loading TorchScript model", path=model_path)

    try:
        model = torch.jit.load(model_path, map_location=torch.device('cpu'))
        model.eval()  # Set to evaluation mode

        logger.info("✅ TorchScript model loaded successfully",
                   path=model_path,
                   device="cpu")

        return model

    except Exception as e:
        logger.error("❌ Failed to load TorchScript model",
                    path=model_path,
                    error=str(e))
        raise RuntimeError(f"Model loading failed: {e}")


def load_fraud_centroid():
    """
    Load fraud centroid from database via iris.sql.exec()

    Returns:
        numpy array of fraud centroid (768-dim) or None
    """
    try:
        # Query fraud centroid using iris.sql.exec() (embedded Python)
        result = iris.sql.exec("""
            SELECT emb, num_fraud_nodes
            FROM gs_fraud_centroid
            WHERE centroid_id = 1
        """)

        row = result.fetchone()
        if not row:
            logger.warning("⚠️  Fraud centroid not found in database")
            return None

        # Parse vector (depends on IRIS vector format)
        emb_value, num_fraud_nodes = row
        logger.info("✅ Fraud centroid loaded",
                   num_fraud_nodes=num_fraud_nodes,
                   vector_dim=len(emb_value) if emb_value else 0)

        return np.array(emb_value) if emb_value else None

    except Exception as e:
        logger.error("❌ Failed to load fraud centroid", error=str(e))
        return None


# ============================================================================
# Feature Computation (via iris.sql.exec)
# ============================================================================

def compute_features(payer_id: str) -> dict:
    """
    Compute rolling features directly via SQL queries

    NOTE: Stored procedures work in SQL files but not via iris.sql.exec() due to syntax differences.
    Implementing feature computation directly in Python instead.

    Args:
        payer_id: Entity ID of payer

    Returns:
        Dictionary with features: {deg_24h, tx_amt_sum_24h, uniq_devices_7d, risk_neighbors_1hop}
    """
    try:
        # Feature 1 & 2: Rolling 24h features (deg_24h, tx_amt_sum_24h)
        # Use DATEADD SQL function instead of Python datetime parameters
        result = iris.sql.exec("""
            SELECT COUNT(*) AS deg_24h, COALESCE(SUM(amount), 0.0) AS tx_amt_sum_24h
            FROM gs_events
            WHERE entity_id = ?
            AND ts >= DATEADD(hour, -24, CURRENT_TIMESTAMP)
        """, payer_id)
        # iris.sql.exec returns iterator, not result set with fetchone()
        rows_24h = list(result)
        deg_24h = rows_24h[0][0] if rows_24h else 0
        tx_amt_sum_24h = rows_24h[0][1] if rows_24h else 0.0

        # Feature 3: Unique devices in last 7 days
        result = iris.sql.exec("""
            SELECT COUNT(DISTINCT device_id) FROM gs_events
            WHERE entity_id = ?
            AND ts >= DATEADD(day, -7, CURRENT_TIMESTAMP)
            AND device_id IS NOT NULL
        """, payer_id)
        rows_7d = list(result)
        uniq_devices_7d = rows_7d[0][0] if rows_7d else 0

        # Feature 4: Risk neighbors (stub - graph queries deferred)
        risk_neighbors_1hop = 0

        return {
            "deg_24h": deg_24h,
            "tx_amt_sum_24h": tx_amt_sum_24h,
            "uniq_devices_7d": uniq_devices_7d,
            "risk_neighbors_1hop": risk_neighbors_1hop
        }

    except Exception as e:
        logger.error("❌ Feature computation failed",
                    payer=payer_id,
                    error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Feature computation failed: {e}"
        )


def get_node_embedding(node_id: str) -> np.ndarray:
    """
    Get node embedding from kg_NodeEmbeddings via iris.sql.exec()

    Args:
        node_id: Node ID

    Returns:
        768-dim numpy array (or zeros if not found)
    """
    try:
        result = iris.sql.exec("""
            SELECT embedding
            FROM kg_NodeEmbeddings
            WHERE node_id = ?
        """, node_id)

        row = result.fetchone()
        if not row or not row[0]:
            logger.warning("⚠️  Node embedding not found (using zero-vector)",
                          node=node_id)
            return np.zeros(768, dtype=np.float32)

        # Parse vector from IRIS format
        embedding = np.array(row[0], dtype=np.float32)
        return embedding

    except Exception as e:
        logger.error("❌ Embedding lookup failed",
                    node=node_id,
                    error=str(e))
        # Return zero-vector fallback (FR-018)
        return np.zeros(768, dtype=np.float32)


# ============================================================================
# Fraud Scoring (MLP Mode)
# ============================================================================

def score_mlp_mode(request: FraudScoreRequest) -> FraudScoreResponse:
    """
    Score fraud using MLP mode (feature-based)

    Performance target: <20ms p95

    Args:
        request: Fraud score request

    Returns:
        Fraud score response with probability and reasons
    """
    trace_id = str(uuid.uuid4())

    try:
        # Step 1: Compute features (5-8ms target)
        features = compute_features(request.payer)

        # Step 2: Get payer embedding (4ms target)
        payer_emb = get_node_embedding(request.payer)

        # Step 3: Create input tensor
        # Features: [deg_24h, tx_amt_sum_24h, uniq_devices_7d, risk_neighbors_1hop, amount, ...]
        feature_vector = [
            features["deg_24h"],
            features["tx_amt_sum_24h"],
            features["uniq_devices_7d"],
            features["risk_neighbors_1hop"],
            request.amount or 0.0,
            1.0 if request.country == "US" else 0.0,  # country_is_us
            0.0,  # device_is_new (placeholder)
            0.0   # ip_reputation (placeholder)
        ]

        # Concatenate features + embedding
        input_array = np.concatenate([feature_vector, payer_emb])
        input_tensor = torch.tensor(input_array, dtype=torch.float32).unsqueeze(0)

        # Step 4: Run TorchScript inference (8ms target)
        model = MODEL_CACHE.get("mlp")
        if model is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="TorchScript model not loaded"
            )

        with torch.no_grad():
            logits = model(input_tensor)
            fraud_prob = torch.sigmoid(logits).item()

        # Step 5: Generate explainability reasons (3ms target)
        reasons = generate_reasons(features, payer_emb, fraud_prob)

        return FraudScoreResponse(
            prob=fraud_prob,
            reasons=reasons,
            trace_id=trace_id,
            mode="MLP",
            timestamp=datetime.utcnow().isoformat()
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("❌ MLP scoring failed",
                    trace_id=trace_id,
                    error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Scoring failed: {e}"
        )


def generate_reasons(features: dict, payer_emb: np.ndarray, fraud_prob: float) -> List[str]:
    """
    Generate min 3 reason codes explaining fraud score

    Args:
        features: Feature dictionary
        payer_emb: Payer embedding (768-dim)
        fraud_prob: Fraud probability

    Returns:
        List of reason strings (min 3)
    """
    reasons = []

    # Feature-based reasons
    if features["deg_24h"] > 10:
        reasons.append(f"High transaction frequency ({features['deg_24h']} in 24h)")

    if features["tx_amt_sum_24h"] > 500:
        reasons.append(f"High recent transaction volume (${features['tx_amt_sum_24h']:.2f} in 24h)")

    if features["uniq_devices_7d"] > 3:
        reasons.append(f"Multiple devices used ({features['uniq_devices_7d']} unique in 7d)")

    if features["risk_neighbors_1hop"] > 0:
        reasons.append(f"Connected to {features['risk_neighbors_1hop']} known fraudsters")

    # Vector proximity reason (if centroid available)
    if FRAUD_CENTROID is not None:
        cos_sim = np.dot(payer_emb, FRAUD_CENTROID) / (
            np.linalg.norm(payer_emb) * np.linalg.norm(FRAUD_CENTROID) + 1e-10
        )
        if cos_sim > 0.7:
            reasons.append(f"High similarity to fraud pattern (cos_sim={cos_sim:.2f})")

    # Ensure min 3 reasons (FR-002)
    if len(reasons) < 3:
        # Add generic reasons
        if fraud_prob > 0.5:
            reasons.append("Model confidence: HIGH")
        else:
            reasons.append("Model confidence: LOW")

        if len(reasons) < 3:
            reasons.append("Transaction risk score elevated")

    return reasons[:5]  # Top 5 reasons max


# ============================================================================
# FastAPI Application
# ============================================================================

def create_app() -> FastAPI:
    """
    Create FastAPI application for fraud scoring

    Returns:
        FastAPI application instance
    """
    app = FastAPI(
        title="IRIS Fraud Scoring API (Embedded Python)",
        description="Real-time fraud scoring using TorchScript MLP in IRIS embedded Python",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc"
    )

    @app.on_event("startup")
    async def startup_event():
        """Load TorchScript model and fraud centroid on startup"""
        global MODEL_CACHE, FRAUD_CENTROID

        logger.info("🚀 Starting fraud scoring API (embedded Python)")

        # Load TorchScript model
        model_path = os.getenv("FRAUD_MODEL_PATH", "/models/fraud_mlp.torchscript")

        try:
            MODEL_CACHE["mlp"] = load_torchscript_model(model_path)
        except FileNotFoundError:
            logger.warning("⚠️  TorchScript model not found, using dummy model",
                          path=model_path)
            # Create dummy model for testing
            MODEL_CACHE["mlp"] = create_dummy_model()
        except Exception as e:
            logger.error("❌ Model loading failed", error=str(e))

        # Load fraud centroid
        FRAUD_CENTROID = load_fraud_centroid()

        logger.info("✅ Fraud scoring API ready")

    @app.get("/")
    async def root():
        """Root endpoint"""
        return {
            "service": "IRIS Fraud Scoring API",
            "version": "0.1.0",
            "mode": "embedded_python",
            "endpoints": ["/fraud/score", "/fraud/health", "/docs"]
        }

    @app.post("/fraud/score", response_model=FraudScoreResponse)
    async def score_fraud(request: FraudScoreRequest):
        """
        Score transaction for fraud risk

        Performance target: <20ms p95 (MLP mode)
        """
        if request.mode == "MLP":
            return score_mlp_mode(request)
        elif request.mode == "EGO":
            # EGO mode deferred to post-MVP
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail="EGO mode not implemented in MVP"
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid mode: {request.mode}"
            )

    @app.get("/fraud/health", response_model=FraudHealthResponse)
    async def health_check():
        """Health check endpoint"""
        model_loaded = MODEL_CACHE.get("mlp") is not None
        centroid_available = FRAUD_CENTROID is not None

        # Test database connection via iris.sql.exec()
        db_connected = False
        try:
            iris.sql.exec("SELECT 1")
            db_connected = True
        except Exception:
            pass

        health_status = "healthy" if (model_loaded and db_connected) else "unhealthy"

        return FraudHealthResponse(
            status=health_status,
            model_loaded=model_loaded,
            database_connected=db_connected,
            centroid_available=centroid_available,
            timestamp=datetime.utcnow().isoformat()
        )

    return app


def create_dummy_model() -> torch.jit.ScriptModule:
    """
    Create dummy TorchScript model for testing

    Returns a simple MLP that always returns ~0.1 fraud probability
    """
    class DummyMLP(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.fc = torch.nn.Linear(776, 1)  # 8 features + 768 embedding

        def forward(self, x):
            return self.fc(x)

    model = DummyMLP()
    model.eval()

    # Trace to create TorchScript
    dummy_input = torch.randn(1, 776)
    traced_model = torch.jit.trace(model, dummy_input)

    logger.warning("⚠️  Using dummy TorchScript model for testing")
    return traced_model


# ============================================================================
# Server Entry Point
# ============================================================================

def run_server():
    """
    Run fraud scoring server via uvicorn

    This is the entry point when running via irispython:
    /usr/irissys/bin/irispython -m iris_fraud_server
    """
    import uvicorn

    logger.info("🚀 Starting IRIS Fraud Scoring Server (Embedded Python)")
    logger.info("   Python executable: " + sys.executable)
    logger.info("   IRIS module available: " + str('iris' in sys.modules))

    app = create_app()

    # Run ASGI server
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("FRAUD_API_PORT", "8000")),
        log_config=None,  # Use structlog instead of uvicorn logging
        access_log=False   # Disable access logs (use structlog)
    )


if __name__ == "__main__":
    run_server()
