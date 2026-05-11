"""FHIR-to-KG Clinical Bridge.

Bridges clinical patient data (FHIR Conditions with ICD-10 codes) to the
biomedical knowledge graph (MeSH/MONDO identifiers) via the fhir_bridges table.

Core functions:
    get_kg_anchors(engine, icd_codes) -> list[str]
    unified_clinical_pipeline(engine, ...) -> dict
    extract_icd_codes(bundle) -> list[str]

MCP tools:
    FHIRSearchTool
    GetPatientKGNeighborhoodTool
"""

import json
import logging
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

__all__ = [
    "get_kg_anchors",
    "extract_icd_codes",
    "fhir_search_conditions",
    "unified_clinical_pipeline",
    "FHIRSearchTool",
    "GetPatientKGNeighborhoodTool",
]


# ---------------------------------------------------------------------------
# US2: Query Patient KG Anchors
# ---------------------------------------------------------------------------


def get_kg_anchors(engine: Any, icd_codes: list[str], bridge_type: str = "icd10_to_mesh") -> list[str]:
    """Resolve ICD-10 codes to KG node IDs via the fhir_bridges table.

    Delegates to engine.get_kg_anchors() which performs the SQL JOIN
    against Graph_KG.nodes to filter to only existing KG nodes.

    Args:
        engine: IRISGraphEngine instance
        icd_codes: List of ICD-10-CM codes (e.g. ["E11.9", "I10"])
        bridge_type: Bridge type filter (default "icd10_to_mesh")

    Returns:
        List of KG node IDs that exist in Graph_KG.nodes.
        Empty list if no codes provided, no bridges loaded, or no matches.
    """
    if not icd_codes:
        return []
    result = engine.get_kg_anchors(icd_codes=icd_codes, bridge_type=bridge_type)
    if not result:
        logger.warning(
            "get_kg_anchors returned empty for codes=%s — check fhir_bridges table is populated",
            icd_codes,
        )
    return result


# ---------------------------------------------------------------------------
# US3: FHIR Client Helpers
# ---------------------------------------------------------------------------


def fhir_search_conditions(
    fhir_base_url: str,
    patient_id: str,
    auth: Optional[tuple[str, str]] = None,
    timeout: float = 10.0,
) -> dict:
    """Search FHIR server for Condition resources for a patient.

    Args:
        fhir_base_url: Base URL of FHIR server (e.g. "http://localhost:8080/fhir")
        patient_id: FHIR patient ID
        auth: Optional (username, password) tuple for BasicAuth
        timeout: Request timeout in seconds (default 10s, independent per spec)

    Returns:
        dict with keys:
            "conditions": list of condition dicts with "code", "system", "display"
            "error": None on success, error message string on failure
    """
    url = f"{fhir_base_url}/Condition?patient={patient_id}&_format=json"
    try:
        resp = requests.get(url, auth=auth, timeout=timeout)
        resp.raise_for_status()
        bundle = resp.json()
        conditions = extract_icd_codes_from_bundle(bundle)
        return {"conditions": conditions, "error": None}
    except requests.exceptions.Timeout:
        msg = f"FHIR request timed out after {timeout}s"
        logger.warning(msg)
        return {"conditions": [], "error": msg}
    except requests.exceptions.ConnectionError as e:
        msg = f"FHIR server unreachable: {e}"
        logger.warning(msg)
        return {"conditions": [], "error": msg}
    except requests.exceptions.HTTPError as e:
        msg = f"FHIR HTTP error: {e.response.status_code}"
        logger.warning(msg)
        return {"conditions": [], "error": msg}
    except Exception as e:
        msg = f"FHIR request failed: {e}"
        logger.warning(msg)
        return {"conditions": [], "error": msg}


def extract_icd_codes(bundle: dict) -> list[str]:
    """Extract ICD-10-CM codes from a FHIR Condition bundle.

    Looks for coding entries with system containing 'icd-10' or 'icd10'.

    Args:
        bundle: FHIR Bundle resource (JSON dict)

    Returns:
        List of ICD-10 code strings (deduplicated).
    """
    codes: list[str] = []
    entries = bundle.get("entry", [])
    for entry in entries:
        resource = entry.get("resource", {})
        if resource.get("resourceType") != "Condition":
            continue
        code_obj = resource.get("code", {})
        for coding in code_obj.get("coding", []):
            system = (coding.get("system") or "").lower()
            if "icd-10" in system or "icd10" in system:
                code_val = coding.get("code")
                if code_val and code_val not in codes:
                    codes.append(code_val)
    return codes


def extract_icd_codes_from_bundle(bundle: dict) -> list[dict]:
    """Extract structured condition info from a FHIR Condition bundle.

    Returns richer structure than extract_icd_codes (which returns only code strings).

    Args:
        bundle: FHIR Bundle resource

    Returns:
        List of dicts with "code", "system", "display" keys.
    """
    conditions: list[dict] = []
    entries = bundle.get("entry", [])
    for entry in entries:
        resource = entry.get("resource", {})
        if resource.get("resourceType") != "Condition":
            continue
        code_obj = resource.get("code", {})
        for coding in code_obj.get("coding", []):
            system = (coding.get("system") or "").lower()
            if "icd-10" in system or "icd10" in system:
                conditions.append({
                    "code": coding.get("code", ""),
                    "system": coding.get("system", ""),
                    "display": coding.get("display", ""),
                })
    return conditions


# ---------------------------------------------------------------------------
# US3: Unified Clinical Pipeline
# ---------------------------------------------------------------------------


def unified_clinical_pipeline(
    engine: Any,
    query: str,
    fhir_base_url: str,
    patient_id: str,
    fhir_auth: Optional[tuple[str, str]] = None,
    top_k: int = 10,
    ppr_top_k: int = 20,
    fhir_timeout: float = 10.0,
) -> dict:
    """Run the full clinical-to-literature pipeline.

    Pipeline steps:
        1. Search FHIR for patient Conditions
        2. Extract ICD-10 codes
        3. Resolve codes to KG anchors via fhir_bridges
        4. Run Personalized PageRank from anchor nodes
        5. Return ranked results with provenance

    Args:
        engine: IRISGraphEngine instance
        query: Clinical query string (for provenance/context)
        fhir_base_url: FHIR server base URL
        patient_id: FHIR patient ID
        fhir_auth: Optional (username, password) for BasicAuth
        top_k: Number of top vector search results (unused in current impl)
        ppr_top_k: Number of PPR results to return
        fhir_timeout: FHIR request timeout in seconds

    Returns:
        dict with keys:
            status: "ok" | "no_fhir_conditions" | "no_bridges_loaded" |
                    "anchors_resolved_but_no_graph_connectivity"
            anchors: list of KG node IDs
            ppr_results: list of PPR-ranked nodes (empty if no connectivity)
            fhir_conditions: list of condition dicts from FHIR
            provenance: dict with pipeline step details
    """
    result = {
        "status": "ok",
        "anchors": [],
        "ppr_results": [],
        "fhir_conditions": [],
        "provenance": {"query": query, "patient_id": patient_id, "steps": []},
    }

    # Step 1: Fetch FHIR conditions
    fhir_result = fhir_search_conditions(
        fhir_base_url=fhir_base_url,
        patient_id=patient_id,
        auth=fhir_auth,
        timeout=fhir_timeout,
    )
    result["provenance"]["steps"].append({"step": "fhir_search", "error": fhir_result["error"]})

    if fhir_result["error"]:
        result["status"] = "fhir_error"
        result["provenance"]["fhir_error"] = fhir_result["error"]
        return result

    result["fhir_conditions"] = fhir_result["conditions"]

    # Step 2: Extract ICD-10 codes
    icd_codes = [c["code"] for c in fhir_result["conditions"] if c.get("code")]
    result["provenance"]["steps"].append({"step": "extract_icd_codes", "codes": icd_codes})

    if not icd_codes:
        result["status"] = "no_fhir_conditions"
        return result

    # Step 3: Resolve to KG anchors
    anchors = get_kg_anchors(engine, icd_codes)
    result["anchors"] = anchors
    result["provenance"]["steps"].append({"step": "get_kg_anchors", "anchors": anchors})

    if not anchors:
        result["status"] = "no_bridges_loaded"
        return result

    # Step 4: Run PPR from anchors
    try:
        ppr_results = engine.kg_PERSONALIZED_PAGERANK(
            seed_entities=anchors,
            top_k=ppr_top_k,
        )
        result["ppr_results"] = ppr_results
        result["provenance"]["steps"].append({"step": "ppr", "result_count": len(ppr_results)})

        if not ppr_results:
            result["status"] = "anchors_resolved_but_no_graph_connectivity"
    except Exception as e:
        logger.warning(f"PPR failed: {e}")
        result["status"] = "anchors_resolved_but_no_graph_connectivity"
        result["provenance"]["steps"].append({"step": "ppr", "error": str(e)})

    return result


# ---------------------------------------------------------------------------
# US4: FHIR Search Tool (MCP-compatible)
# ---------------------------------------------------------------------------


class FHIRSearchTool:
    """MCP-compatible tool that searches a FHIR server for patient data.

    Wraps FHIR REST API details (pagination, auth, resource parsing) so
    AI agents don't need to know FHIR internals.
    """

    name = "fhir_search_conditions"
    description = (
        "Search a FHIR server for patient Condition resources. "
        "Returns structured list of conditions with ICD-10 codes."
    )

    def __init__(
        self,
        base_url: str,
        auth: Optional[tuple[str, str]] = None,
        timeout: float = 10.0,
    ):
        self.base_url = base_url
        self.auth = auth
        self.timeout = timeout

    def __call__(self, patient_id: str) -> dict:
        """Execute the tool.

        Args:
            patient_id: FHIR patient ID to search conditions for.

        Returns:
            dict with "conditions" list and "error" (None on success).
        """
        return fhir_search_conditions(
            fhir_base_url=self.base_url,
            patient_id=patient_id,
            auth=self.auth,
            timeout=self.timeout,
        )


# ---------------------------------------------------------------------------
# US5: Patient Graph Neighborhood Tool (MCP-compatible)
# ---------------------------------------------------------------------------


class GetPatientKGNeighborhoodTool:
    """MCP-compatible tool: patient_id -> conditions -> anchors -> PPR neighborhood.

    Python equivalent of CareConnect's GetPatientGraphNeighborhood ObjectScript tool.
    """

    name = "get_patient_kg_neighborhood"
    description = (
        "Get the knowledge graph neighborhood for a patient. "
        "Chains: patient → FHIR Conditions → ICD codes → KG anchors → PPR walk → ranked concepts."
    )

    def __init__(
        self,
        engine: Any,
        fhir_base_url: str,
        fhir_auth: Optional[tuple[str, str]] = None,
        fhir_timeout: float = 10.0,
        ppr_top_k: int = 20,
    ):
        self.engine = engine
        self.fhir_base_url = fhir_base_url
        self.fhir_auth = fhir_auth
        self.fhir_timeout = fhir_timeout
        self.ppr_top_k = ppr_top_k

    def __call__(self, patient_id: str) -> dict:
        """Execute the tool.

        Args:
            patient_id: FHIR patient ID.

        Returns:
            dict with "anchors", "ppr_results", "status".
        """
        return unified_clinical_pipeline(
            engine=self.engine,
            query=f"patient_neighborhood:{patient_id}",
            fhir_base_url=self.fhir_base_url,
            patient_id=patient_id,
            fhir_auth=self.fhir_auth,
            ppr_top_k=self.ppr_top_k,
            fhir_timeout=self.fhir_timeout,
        )
