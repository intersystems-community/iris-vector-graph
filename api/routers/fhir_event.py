"""POST /fhir-event — FHIR resource event sidecar endpoint.

Called by arno-fhir (fire-and-forget, 500ms timeout) after a successful
resource write to materialize a pointer node and temporal edge in ivg.
Also callable directly from the demo seed script.

Lazy vectorization: if the payload includes `content` (the full FHIR resource)
and the resource type is in EMBED_ELIGIBLE_TYPES, the node is queued for
background embedding immediately after the HTTP response is sent.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Request, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# Lazy vectorization config
# ---------------------------------------------------------------------------

# Resource types whose FHIR content is worth embedding for semantic search.
# Condition: ICD code + display text → comorbidity similarity
# DiagnosticReport: conclusion free-text → clinical note similarity
EMBED_ELIGIBLE_TYPES = {"Condition", "DiagnosticReport"}

# Module-level embedder singleton — loaded once on first eligible request.
_embedder = None
_embedder_lock = asyncio.Lock()


def _extract_embed_text(resource_type: str, content: dict) -> Optional[str]:
    """Extract the text to embed from a FHIR resource body.

    Returns None if no meaningful content is found.
    """
    if resource_type == "Condition":
        # ICD-10 code + display text: "I50.20 Heart failure with reduced ejection fraction"
        coding = (
            content.get("code", {})
            .get("coding", [{}])[0]
        )
        code = coding.get("code", "")
        display = coding.get("display", "") or content.get("code", {}).get("text", "")
        if code or display:
            return f"{code} {display}".strip()

    elif resource_type == "DiagnosticReport":
        # Free-text clinical impression
        conclusion = content.get("conclusion", "")
        if conclusion:
            return conclusion
        # Fallback: code display
        coding = content.get("code", {}).get("coding", [{}])[0]
        return coding.get("display", "") or None

    return None


def _extract_codes(resource_type: str, content: Optional[dict]) -> dict:
    """Extract clinical code(s) from a FHIR resource into node properties.

    Shaarpec's VGAE trains on coded trajectory nodes (ICD-10 on Condition, ATC/RxNorm
    on MedicationRequest, etc.), not bare FHIR-URL pointers. This pulls the PRIMARY
    coding into flat string properties so the node carries the clinical identity.

    Returns a dict suitable for create_node(properties=...); empty if no codes found.
    Keys: code_system, code, code_display (and medication_* mirror for MedicationRequest).
    None content (sidecar called without the resource body) → {} (caller falls back to
    a pointer-only node, preserving prior behavior).
    """
    if not content:
        return {}

    # MedicationRequest carries its code under medicationCodeableConcept.
    if resource_type == "MedicationRequest":
        cc = content.get("medicationCodeableConcept", {})
    elif resource_type == "Encounter":
        # Encounter uses type[].coding[]; fall back to class for the act code.
        types = content.get("type", [])
        cc = types[0] if isinstance(types, list) and types else {}
        if not cc.get("coding") and isinstance(content.get("class"), dict):
            # class is a single Coding, not a CodeableConcept
            cls = content["class"]
            return _coding_to_props(cls) if cls.get("code") else {}
    else:
        # Condition, Observation, DiagnosticReport, Procedure, ... → code.coding[]
        cc = content.get("code", {})

    if not isinstance(cc, dict):
        return {}
    coding_list = cc.get("coding", [])
    coding = coding_list[0] if isinstance(coding_list, list) and coding_list else {}
    props = _coding_to_props(coding)
    # Fall back to CodeableConcept.text for display if coding lacked one.
    if "code_display" not in props and cc.get("text"):
        props["code_display"] = cc["text"]
    return props


def _coding_to_props(coding: dict) -> dict:
    """Map a single FHIR Coding {system, code, display} → flat node properties."""
    if not isinstance(coding, dict):
        return {}
    props: dict[str, Any] = {}
    if coding.get("system"):
        props["code_system"] = coding["system"]
    if coding.get("code"):
        props["code"] = coding["code"]
    if coding.get("display"):
        props["code_display"] = coding["display"]
    return props


async def _ensure_embedder():
    """Lazily initialize the sentence-transformer model (thread-safe, loads once)."""
    global _embedder
    if _embedder is not None:
        return _embedder
    async with _embedder_lock:
        if _embedder is None:
            loop = asyncio.get_event_loop()
            def _load():
                from sentence_transformers import SentenceTransformer
                return SentenceTransformer("all-MiniLM-L6-v2")
            _embedder = await loop.run_in_executor(None, _load)
            logger.info("Lazy vectorizer: loaded all-MiniLM-L6-v2 (384-dim)")
    return _embedder


async def _embed_and_store(engine, node_id: str, text: str) -> None:
    """Background task: embed text and store in kg_NodeEmbeddings."""
    try:
        model = await _ensure_embedder()
        loop = asyncio.get_event_loop()
        vec = await loop.run_in_executor(None, lambda: model.encode(text).tolist())
        # store_embedding is synchronous IRIS SQL — run in executor
        await loop.run_in_executor(None, lambda: engine.store_embedding(node_id, vec))
        logger.info("lazy-vec: stored embedding for %s (%.0f-dim)", node_id, len(vec))
    except Exception as exc:
        logger.warning("lazy-vec: embedding failed for %s: %s", node_id, exc)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class FhirEventPayload(BaseModel):
    resourceType: str
    id: str
    fhirUrl: str
    patientRef: Optional[str] = None
    date: Optional[str] = None
    # Optional: full FHIR resource body. When present and resource type is
    # embed-eligible, triggers lazy background vectorization.
    content: Optional[dict[str, Any]] = None


class FhirEventResponse(BaseModel):
    status: str
    node_id: str
    temporal_edge: bool
    queued_for_embedding: bool = False
    warnings: list[str] = []


# ---------------------------------------------------------------------------
# Date helper
# ---------------------------------------------------------------------------

def unix_from_date(date_str: Optional[str]) -> tuple[int, list[str]]:
    """Convert an ISO 8601 date/datetime string to a Unix timestamp.

    Returns (timestamp_int, warnings). Falls back to current UTC time if
    date_str is None, empty, or unparseable, and adds a warning.
    """
    warnings: list[str] = []
    if not date_str:
        warnings.append("date not provided; using current UTC time as timestamp")
        return int(time.time()), warnings

    for fmt in (
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp()), warnings
        except ValueError:
            continue

    try:
        dt = datetime.fromisoformat(date_str.strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp()), warnings
    except ValueError:
        pass

    warnings.append(f"date '{date_str}' could not be parsed; using current UTC time")
    return int(time.time()), warnings


# ---------------------------------------------------------------------------
# Eligibility check
# ---------------------------------------------------------------------------

def is_embed_eligible(resource_type: str, content: Optional[dict]) -> bool:
    """Return True if this resource should be queued for lazy embedding."""
    if resource_type not in EMBED_ELIGIBLE_TYPES:
        return False
    if content is None:
        return False
    return _extract_embed_text(resource_type, content) is not None


def already_embedded(engine, node_id: str) -> bool:
    """Return True if an embedding already exists for this node (skip re-embed)."""
    try:
        existing = engine.get_embedding(node_id)
        return existing is not None
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Engine resolver
# ---------------------------------------------------------------------------

def _get_engine(request: Request):
    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        conn = (
            getattr(request.app.state, "db_connection", None)
            or getattr(request.app.state, "iris_connection", None)
        )
        if conn is not None:
            from iris_vector_graph import IRISGraphEngine
            engine = IRISGraphEngine(conn)
    return engine


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

@router.post("/", response_model=FhirEventResponse)
async def fhir_event(
    payload: FhirEventPayload,
    request: Request,
    background_tasks: BackgroundTasks,
):
    """Materialize a pointer node and optional temporal edge from a FHIR write event.

    If the payload includes `content` and the resource type is embed-eligible
    (Condition, DiagnosticReport), the node is queued for lazy background
    embedding using all-MiniLM-L6-v2.
    """
    engine = _get_engine(request)
    if engine is None:
        raise HTTPException(
            status_code=503,
            detail="IRISGraphEngine not available; check server configuration",
        )

    warnings: list[str] = []
    fhir_url = payload.fhirUrl
    resource_type = payload.resourceType
    patient_ref = payload.patientRef or ""

    # 1. Upsert node — carry clinical codes as properties when the resource body
    #    is present (Shaarpec VGAE trains on coded nodes, not bare pointers).
    #    NOTE: deliberately supersedes spec-195 data-model's "no FHIR attributes copied"
    #    line — that pointer-only design predates the Shaarpec code-label requirement.
    code_props = _extract_codes(resource_type, payload.content)
    try:
        engine.create_node(
            node_id=fhir_url,
            labels=[resource_type],
            properties=code_props or None,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"IRIS write failed: {exc}") from exc

    # 2. Temporal edge
    temporal_edge_written = False
    if patient_ref:
        ts, date_warnings = unix_from_date(payload.date)
        warnings.extend(date_warnings)
        try:
            engine.create_node(node_id=patient_ref, labels=["Patient"])
            engine.create_edge_temporal(
                source=patient_ref,
                predicate=resource_type.upper(),
                target=fhir_url,
                timestamp=ts,
                weight=1.0,
                upsert=True,
            )
            temporal_edge_written = True
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"IRIS write failed: {exc}") from exc
    else:
        warnings.append("patientRef not provided; pointer node written but no temporal edge created")

    # 3. Lazy vectorization — queue in background if eligible and not yet embedded
    queued_for_embedding = False
    if is_embed_eligible(resource_type, payload.content):
        embed_text = _extract_embed_text(resource_type, payload.content)
        if embed_text and not already_embedded(engine, fhir_url):
            background_tasks.add_task(_embed_and_store, engine, fhir_url, embed_text)
            queued_for_embedding = True
            logger.debug("lazy-vec: queued %s '%s'", fhir_url, embed_text[:60])

    return FhirEventResponse(
        status="ok",
        node_id=fhir_url,
        temporal_edge=temporal_edge_written,
        queued_for_embedding=queued_for_embedding,
        warnings=warnings,
    )
