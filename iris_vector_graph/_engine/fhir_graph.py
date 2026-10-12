"""FHIR repository as a named graph (spec 231).

The projection, sync and concept resolution run in ``Graph.KG.FHIRGraph`` in the
FHIR namespace, next to the repository's tables (plan.md, "Where the work runs").
These methods validate their arguments before any round trip, call the class
method, and turn its JSON reply into a value or a ``FHIRGraphError``.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from iris_vector_graph._validate import validate_graph_name
from iris_vector_graph.exceptions import FHIRGraphError
from iris_vector_graph.fhir_links import parse_json_links
from iris_vector_graph.schema import GraphSchema
from iris_vector_graph.utils import _split_sql_statements

_CLS = "Graph.KG.FHIRGraph"

CROSSWALK_RELATIONS = ("exact", "broader", "narrower", "related")

_DENY_ENTRY = re.compile(r"^[A-Za-z]+\.[A-Za-z0-9_-]+$")
_SOURCE_KEY = re.compile(r"^[A-Za-z]+/[^/\s]+$")
_PARAM = re.compile(r"^[A-Za-z0-9_-]+$")
_MAX_HOPS = 10

# The short names Graph_KG.fhir_bridges uses, and the system URIs a FHIR token holds.
_SYSTEM_URIS = {
    "ICD10CM": "http://hl7.org/fhir/sid/icd-10-cm",
    "ICD10": "http://hl7.org/fhir/sid/icd-10",
    "SNOMEDCT": "http://snomed.info/sct",
    "SNOMED": "http://snomed.info/sct",
    "LOINC": "http://loinc.org",
    "RXNORM": "http://www.nlm.nih.gov/research/umls/rxnorm",
}


def fhir_graph_id(namespace: str, package: str) -> str:
    """``fhir:<namespace>:<pkg>`` for a repository; ``package`` is the resource
    classes package (``HSFHIR.X0001.R``) or its second piece (``X0001``)."""
    pkg = package.split(".")[1] if "." in package else package
    return validate_graph_name(f"fhir:{namespace}:{pkg}")


def code_system_uri(system: str) -> str:
    """The FHIR system URI for a ``fhir_bridges`` system name. A URI, or a name
    with no known URI, is returned unchanged rather than guessed."""
    return _SYSTEM_URIS.get(system.upper(), system)


def _fhir_graph(graph: Optional[str]) -> str:
    """A FHIR graph is always a named graph: the default graph is not one."""
    name = validate_graph_name(graph)
    if not name:
        raise ValueError("a FHIR graph id is required, e.g. 'fhir:IVGFHIR:X0001'")
    return name


def _interp_stale(version: Any, current: int) -> bool:
    """Whether a graph's ``interp_version`` needs a re-derivation (spec 235). NULL
    (``None``, or ``""`` from ObjectScript) and older versions are stale. A newer
    version was written by a newer IVG: re-deriving with older rules would silently
    downgrade it, so it raises."""
    if version is None or version == "":
        return True
    if int(version) > int(current):
        raise RuntimeError(
            f"FHIR graph interpretation version {version} is newer than this IVG's {current}; "
            "upgrade iris-vector-graph"
        )
    return int(version) < int(current)


_DIRECTIONS = ("out", "in", "both")

# Edges `fhir_concept_ppr` does not walk by default (spec 235, FR-009/FR-010): the
# derived compartment edges, and Provenance's links to what it records.
FHIR_PPR_EXCLUDE = ("in_patient_compartment", "Provenance.target", "Provenance.entity")
_VIA = re.compile(r"^[a-z-]+$")

# `params="clinical"` (spec 235, FR-014): the token params that carry a clinical
# code, trimmed to those the repository indexes. Medication `code` is the `code`
# param on the Medication type, so it needs no entry of its own.
CLINICAL_PARAMS = ("code", "value-concept", "component-code", "component-value-concept")
_NO_HOP = {"medications": 0, "added": 0}


def _check_params(params):
    """``None``, ``"clinical"``, or a list of search param names."""
    if params is None or params == "clinical":
        return params
    if isinstance(params, str):
        raise ValueError(f"params must be None, 'clinical' or a list, got {params!r}")
    ps = list(params)
    for p in ps:
        if not isinstance(p, str) or not _PARAM.match(p):
            raise ValueError(f"param {p!r} is not a search param name")
    return ps


_TOKEN_PATH = re.compile(
    r"^([A-Z][A-Za-z]+)\.([a-z][A-Za-z]*(?:\.[a-z][A-Za-z]*)*)(\s+as\s+CodeableConcept)?$"
)
_ID_PREFIX = re.compile(r"^[A-Za-z0-9\-.]{1,64}$")


def _token_field(fhirpath: str):
    """``(type, element path)`` for one SearchColumn TOKEN FHIRPath part (research
    R11): ``X.a.b`` is ``("a", "b")``, and ``X.a as CodeableConcept``, with or
    without parentheses, is the R4 choice element ``aCodeableConcept``. Any other
    form raises ``ValueError``."""
    part = fhirpath.strip()
    while part.startswith("(") and part.endswith(")"):
        part = part[1:-1].strip()
    m = _TOKEN_PATH.match(part)
    if not m:
        raise ValueError(f"unsupported token FHIRPath: {fhirpath!r}")
    rtype, path, as_cc = m.groups()
    parts = path.split(".")
    if as_cc:
        parts[-1] += "CodeableConcept"
    return rtype, tuple(parts)


def _token_fields(fhirpath: str, rtype: str):
    """``(fields, skipped)``: the distinct element paths of ``rtype`` in a ``|``
    union, and the parts ``_token_field`` cannot read. Parts naming another type
    are dropped."""
    fields: List[tuple] = []
    skipped: List[str] = []
    for part in fhirpath.split("|"):
        if not part.strip():
            continue
        try:
            t, path = _token_field(part)
        except ValueError:
            skipped.append(part.strip())
            continue
        if t == rtype and path not in fields:
            fields.append(path)
    return fields, skipped


def _check_prefix(id_prefix) -> str:
    if id_prefix is None:
        return ""
    if not isinstance(id_prefix, str) or not _ID_PREFIX.match(id_prefix):
        raise ValueError(f"id_prefix must be 1-64 id characters, got {id_prefix!r}")
    return id_prefix


def _check_direction(direction) -> None:
    if direction not in _DIRECTIONS:
        raise ValueError(f"direction must be one of {_DIRECTIONS}, got {direction!r}")


def _strings(values, what: str) -> List[str]:
    out = list(values)
    for v in out:
        if not isinstance(v, str) or not v:
            raise ValueError(f"{what} must be non-empty strings, got {v!r}")
    return out


def _relations(relations) -> str:
    if relations is None:
        return ""
    rels = list(relations)
    for r in rels:
        if r not in CROSSWALK_RELATIONS:
            raise ValueError(f"relation {r!r} is not one of {CROSSWALK_RELATIONS}")
    return json.dumps(rels)


class FhirGraphMixin:
    """Engine methods for FHIR named graphs (FR-017)."""

    def _fhir_call(self, operation: str, *args) -> Dict[str, Any]:
        raw = self._iris_obj().classMethodValue(_CLS, operation, *args)
        reply = json.loads(raw) if raw else {}
        if reply.get("status") == "error":
            raise FHIRGraphError(operation, reply.get("error", "unknown error"))
        return reply

    def _ensure_fhir_graph_tables(self) -> None:
        """Create the FHIR-graph tables where they are missing. They are not in the
        base schema, so the first registration in a namespace creates them. Without a
        FHIR repository there is nothing to register, and Register says so."""
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES"
                " WHERE TABLE_SCHEMA = 'HS_FHIRServer' AND TABLE_NAME = 'Repo'"
            )
            row = cursor.fetchone()
            if not row or not row[0]:
                return
            for stmt in _split_sql_statements(GraphSchema.get_fhir_graph_schema_sql()):
                if not stmt.strip():
                    continue
                try:
                    cursor.execute(stmt)
                except Exception as e:
                    if "already" not in str(e).lower():
                        raise
            self.conn.commit()
        finally:
            cursor.close()

    # --------------------------------------------------------------- registry

    def fhir_graph_register(
        self,
        endpoint: str = "",
        denylist: Optional[List[str]] = None,
        interval_s: int = 60,
        json_links: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Register the connected namespace's FHIR repository as a named graph.

        ``endpoint`` picks the repository when the namespace has more than one.
        ``denylist`` holds ``Type.param`` entries whose references are not edges.
        ``json_links`` names links the search index cannot see (``Type.element`` or
        ``extension:<url>``); ``None`` keeps the default, ``[]`` turns them off.
        Re-registering updates the settings and keeps the watermarks; a changed
        denylist applies at the next rebuild, a changed json_links list rebuilds now
        (``"rebuilt"`` in the reply).
        """
        deny = list(denylist or [])
        for entry in deny:
            if not isinstance(entry, str) or not _DENY_ENTRY.match(entry):
                raise ValueError(f"denylist entry {entry!r} is not 'Type.param'")
        if not isinstance(interval_s, int) or interval_s < 60:
            raise ValueError(
                f"interval_s must be at least 60 (Task Manager counts minutes), got {interval_s!r}"
            )
        links = "" if json_links is None else json.dumps(parse_json_links(json_links))
        self._ensure_fhir_graph_tables()
        return self._fhir_call("Register", endpoint or "", json.dumps(deny), interval_s, links)

    def fhir_graph_rebuild(self, graph: str) -> Dict[str, Any]:
        """Reconcile the graph to the repository and reset its watermarks."""
        return self._fhir_call("Rebuild", _fhir_graph(graph))

    def fhir_graph_sync(self, graph: str) -> Dict[str, Any]:
        """Apply every change since the watermarks. ``{"status": "busy"}`` when a
        sync or rebuild of the same graph holds its lock."""
        return self._fhir_call("SyncOnce", _fhir_graph(graph))

    def fhir_graph_status(self, graph: str) -> Dict[str, Any]:
        st = self._fhir_call("Status", _fhir_graph(graph))
        if "interpretation_current" in st:
            version = st.get("interpretation_version")
            st["interpretation_version"] = None if version in (None, "") else int(version)
            st["interpretation_stale"] = _interp_stale(version, st["interpretation_current"])
        return st

    def fhir_reinterpret(self, graph: str) -> Dict[str, Any]:
        """Re-derive the interpretation (compartment edges, ``category``,
        ``meta_profile``, category labels) of every live resource and set the graph's
        ``interp_version`` (spec 235). ``{"status": "busy"}`` when a sync or rebuild
        holds the graph's lock."""
        return self._fhir_call("Reinterpret", _fhir_graph(graph))

    def fhir_link_report(self, graph: str, source: Optional[str] = None) -> Dict[str, Any]:
        """Every canonical and json-link reference in the graph, or one source key's,
        with its outcome: the edge target key or the unresolved reason."""
        name = _fhir_graph(graph)
        if source is not None and not (isinstance(source, str) and _SOURCE_KEY.match(source)):
            raise ValueError(f"source must be a 'Type/id' key, got {source!r}")
        return self._fhir_call("LinkReport", name, source or "")

    def fhir_graph_schedule(self, graph: str, interval_s: Optional[int] = None) -> Dict[str, Any]:
        """Create or update the graph's Task Manager task; ``None`` keeps the
        registered interval."""
        if interval_s is not None and (not isinstance(interval_s, int) or interval_s < 60):
            raise ValueError(
                f"interval_s must be at least 60 (Task Manager counts minutes), got {interval_s!r}"
            )
        return self._fhir_call("Schedule", _fhir_graph(graph), interval_s or 0)

    def fhir_graph_unschedule(self, graph: str) -> Dict[str, Any]:
        return self._fhir_call("Unschedule", _fhir_graph(graph))

    # --------------------------------------------------------------- concepts

    def fhir_resolve_concepts(
        self,
        graph: str,
        concept_graph: Optional[str],
        ids: List[str],
        *,
        params: Optional[Any] = None,
        relations: Optional[List[str]] = None,
        detail: bool = False,
    ) -> Any:
        """Keys of live resources in ``graph`` whose token param (default ``code``)
        matches a code crosswalked to one of ``ids`` in ``concept_graph``, plus the
        MedicationRequest/Statement/Administration/Dispense resources whose
        ``medication`` references a resolved Medication (spec 235).

        ``params="clinical"`` is ``CLINICAL_PARAMS`` minus the params the repository
        does not index. ``detail=True`` returns ``{"keys", "params_used",
        "dropped_params", "via", "medication_hop"}`` instead of the key list."""
        g = _fhir_graph(graph)
        cg = validate_graph_name(concept_graph)
        concept_ids = _strings(ids, "concept ids")
        ps = _check_params(params)
        relations_json = _relations(relations)
        dropped: List[str] = []

        def result(keys, used, reply):
            if not detail:
                return keys
            return {
                "keys": keys,
                "params_used": used,
                "dropped_params": dropped,
                "via": dict(reply.get("via") or {}),
                "medication_hop": dict(reply.get("medication_hop") or _NO_HOP),
            }

        if not concept_ids:
            return result([], [] if ps == "clinical" else (ps or ["code"]), {})
        if ps == "clinical":
            idx = self._fhir_call("IndexedTokenParams", g, json.dumps(list(CLINICAL_PARAMS)))
            ps = list(idx.get("used", []))
            dropped = list(idx.get("dropped", []))
            if not ps:
                return result([], [], {})
        reply = self._fhir_call(
            "ResolveConcepts",
            g,
            cg,
            json.dumps(concept_ids),
            "" if ps is None else json.dumps(ps),
            relations_json,
        )
        return result(list(reply.get("keys", [])), ["code"] if ps is None else ps, reply)

    def fhir_expand_concepts(
        self,
        concept_graph: Optional[str],
        ids: List[str],
        *,
        predicates: Optional[List[str]] = None,
        hops: int = 1,
        direction: str = "in",
    ) -> List[str]:
        """``ids`` and every concept reachable from them in at most ``hops`` over
        ``concept_graph``, optionally only along ``predicates``.

        ``direction`` is the edge direction followed at every hop. The default ``"in"``
        goes from object to subject: under the child-to-parent edges of
        ``rdfs:subClassOf``, ``skos:broader`` and OBO ``is_a`` that is a class to its
        subclasses, and a disease to its associated genes. ``"out"`` goes from subject
        to object, which is downward only for parent-to-child edges such as
        ``narrower``. ``"both"`` follows either."""
        cg = validate_graph_name(concept_graph)
        if isinstance(hops, bool) or not isinstance(hops, int) or not 0 <= hops <= _MAX_HOPS:
            raise ValueError(f"hops must be an integer in 0..{_MAX_HOPS}, got {hops!r}")
        _check_direction(direction)
        concept_ids = _strings(ids, "concept ids")
        preds = "" if predicates is None else json.dumps(_strings(predicates, "predicates"))
        reply = self._fhir_call("ExpandConcepts", cg, json.dumps(concept_ids), preds, hops, direction)
        return list(reply.get("ids", []))

    def fhir_concept_ppr(
        self,
        graph: str,
        concept_graph: Optional[str],
        ids: List[str],
        *,
        hops: int = 1,
        direction: str = "in",
        predicates: Optional[List[str]] = None,
        params: Optional[Any] = None,
        relations: Optional[List[str]] = None,
        top_k: Optional[int] = 50,
        damping_factor: float = 0.85,
        max_iterations: int = 20,
        exclude_predicates: Optional[Any] = FHIR_PPR_EXCLUDE,
        group_by: Optional[str] = None,
        via: Optional[List[str]] = None,
        explain_top: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Expand concepts, resolve them to FHIR resources, and rank the FHIR graph
        around them: one operator per graph, joined as a pipeline (spec 231).

        Returns PPR scores keyed by FHIR key; empty when nothing resolves.
        ``exclude_predicates`` names edges the walk skips (default
        ``FHIR_PPR_EXCLUDE``; ``[]`` walks every edge). ``group_by="patient"``
        sums the uncapped scores over each patient's compartment resources and
        returns ``{"patients": [...], "unattributed": {...}}`` instead (spec 235):
        ``via`` keeps only compartment edges reached through those params,
        ``explain_top`` (default 5) is how many contributors each patient lists,
        and ``top_k`` caps the patients. ``params`` is as in
        ``fhir_resolve_concepts``, ``"clinical"`` included.
        """
        from iris_vector_graph._engine.algorithms import _check_exclusions

        g = _fhir_graph(graph)
        _check_direction(direction)
        exclude = _check_exclusions(exclude_predicates)
        _check_params(params)
        if group_by is None:
            if via is not None or explain_top is not None:
                raise ValueError("via and explain_top need group_by='patient'")
        elif group_by != "patient":
            raise ValueError(f"group_by must be 'patient', got {group_by!r}")
        else:
            if via is not None:
                if isinstance(via, str) or not all(isinstance(v, str) and _VIA.match(v) for v in via):
                    raise ValueError(f"via must be a list of search param names, got {via!r}")
            if explain_top is None:
                explain_top = 5
            elif isinstance(explain_top, bool) or not isinstance(explain_top, int) or explain_top < 0:
                raise ValueError(f"explain_top must be an integer >= 0, got {explain_top!r}")
        concepts = (
            self.fhir_expand_concepts(
                concept_graph, ids, predicates=predicates, hops=hops, direction=direction
            )
            if hops
            else list(ids)
        )
        seeds = self.fhir_resolve_concepts(
            g, concept_graph, concepts, params=params, relations=relations
        )
        if not seeds:
            return {"patients": [], "unattributed": {"count": 0, "score": 0.0}} if group_by else {}
        if group_by:
            # One call walks and groups, so no score list crosses the wire: every score
            # as JSON passed IRIS's string limit at ~100 Synthea patients (R17).
            out = self._fhir_call(
                "GroupPPR",
                g,
                json.dumps(seeds),
                damping_factor,
                max_iterations,
                "" if exclude is None else json.dumps(exclude),
                json.dumps(list(via)) if via else "",
                explain_top,
                top_k or 0,
            )
            return {"patients": out.get("patients", []), "unattributed": out.get("unattributed")}
        return self.kg_PERSONALIZED_PAGERANK(
            seeds,
            damping_factor=damping_factor,
            max_iterations=max_iterations,
            return_top_k=top_k,
            bidirectional=True,
            graph=g,
            exclude_predicates=exclude,
        )

    # --------------------------------------------------------------- reports

    def _indexed_clinical(self, g: str):
        idx = self._fhir_call("IndexedTokenParams", g, json.dumps(list(CLINICAL_PARAMS)))
        return list(idx.get("used", [])), list(idx.get("dropped", []))

    def fhir_coverage_report(self, graph: str, *, id_prefix: Optional[str] = None) -> Dict[str, Any]:
        """How much of the graph the interpretation covers, computed live (spec 235,
        FR-019): per type counts, compartment share, ``meta_profile`` histogram and
        category split; code resolution per system for the ``clinical`` params; the
        number of linked patients; and ``fhir_link_report(graph)`` unchanged.

        ``id_prefix`` counts only resources whose id starts with it."""
        g = _fhir_graph(graph)
        prefix = _check_prefix(id_prefix)
        used, dropped = self._indexed_clinical(g)
        rep = self._fhir_call("CoverageReport", g, json.dumps(used), prefix)
        rep.pop("status", None)
        resolution = dict(rep.get("resolution") or {})
        rep["resolution"] = {
            "params_used": used,
            "dropped_params": dropped,
            "by_system": dict(resolution.get("by_system") or {}),
        }
        if "interpretation_current" in rep:
            version = rep.get("interpretation_version")
            rep["interpretation_version"] = None if version in (None, "") else int(version)
            rep["stale"] = _interp_stale(version, rep["interpretation_current"])
        rep["link_report"] = self.fhir_link_report(g)
        return rep

    def fhir_concept_gaps(
        self,
        graph: str,
        *,
        params: Optional[Any] = None,
        top: int = 20,
        id_prefix: Optional[str] = None,
    ) -> Dict[str, Any]:
        """The codes the graph holds that no ``code_crosswalk`` row maps, computed live
        (spec 235, FR-020): the ``top`` (system, code) pairs by resource count, the
        totals, and per param the resources whose field has only ``text``.

        ``params`` is as in ``fhir_resolve_concepts`` (default ``["code"]``).
        ``skipped_paths`` lists the FHIRPath parts the text-only count cannot read."""
        g = _fhir_graph(graph)
        ps = _check_params(params)
        if isinstance(top, bool) or not isinstance(top, int) or top < 1:
            raise ValueError(f"top must be an integer >= 1, got {top!r}")
        prefix = _check_prefix(id_prefix)
        dropped: List[str] = []
        if ps == "clinical":
            ps, dropped = self._indexed_clinical(g)
        elif ps is None:
            ps = ["code"]
        out = {"graph": g, "params_used": ps, "dropped_params": dropped}
        if not ps:
            empty = {"unmatched": [], "unmatched_total": {"codes": 0, "resources": 0}}
            return {**out, **empty, "text_only": {}, "skipped_paths": []}
        paths = self._fhir_call("TokenPaths", g, json.dumps(ps)).get("paths") or {}
        fields: Dict[str, Dict[str, List[List[str]]]] = {}
        skipped: List[str] = []
        for rtype, by_param in paths.items():
            for param, fhirpath in by_param.items():
                got, bad = _token_fields(fhirpath, rtype)
                skipped.extend(b for b in bad if b not in skipped)
                if got:
                    fields.setdefault(rtype, {})[param] = [list(p) for p in got]
        rep = self._fhir_call("ConceptGaps", g, json.dumps(ps), json.dumps(fields), top, prefix)
        return {
            **out,
            "unmatched": list(rep.get("unmatched") or []),
            "unmatched_total": dict(rep.get("unmatched_total") or {}),
            "text_only": dict(rep.get("text_only") or {}),
            "skipped_paths": skipped,
        }

    def fhir_patient_anchors(self, patient_id: str, *, graph: Optional[str] = None) -> Dict[str, Any]:
        """The concepts a patient's own resources point at, from the synced graph
        (spec 235, FR-021): ``code_crosswalk`` targets of the ``clinical`` tokens on
        the resources in ``Patient/<patient_id>``'s compartment.

        ``graphs`` lists the FHIR graphs holding the patient (``graph`` alone when
        given, else every registered one); ``anchors`` is ``[{id, graph}]``,
        deduplicated and sorted."""
        if not isinstance(patient_id, str) or not _ID_PREFIX.match(patient_id):
            raise ValueError(f"patient_id must be a FHIR id, got {patient_id!r}")
        if graph is not None:
            candidates = [_fhir_graph(graph)]
        else:
            cursor = self.conn.cursor()
            try:
                cursor.execute("SELECT graph_id FROM Graph_KG.fhir_graphs ORDER BY graph_id")
                candidates = [r[0] for r in cursor.fetchall()]
            except Exception as e:
                # No FHIR graph was ever registered here, so the table does not exist.
                if "<-30>" not in str(e):
                    raise
                candidates = []
            finally:
                cursor.close()
        key = f"Patient/{patient_id}"
        graphs: List[str] = []
        anchors = set()
        for g in candidates:
            used, _ = self._indexed_clinical(g)
            rep = self._fhir_call("PatientConcepts", g, key, json.dumps(used))
            if rep.get("present"):
                graphs.append(g)
                anchors.update((c, g) for c in rep.get("concepts") or [])
        return {"graphs": graphs, "anchors": [{"id": i, "graph": g} for i, g in sorted(anchors)]}

    # --------------------------------------------------------------- crosswalk

    _CROSSWALK_UPSERT = (
        "INSERT OR UPDATE INTO Graph_KG.code_crosswalk "
        "(code_system_uri, code, target_graph, target_node_id, relation, source, "
        "source_version, confidence) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    )

    def code_crosswalk_add(
        self,
        code_system_uri: str,
        code: str,
        target_node_id: str,
        *,
        target_graph: Optional[str] = None,
        relation: str = "exact",
        source: Optional[str] = None,
        source_version: Optional[str] = None,
        confidence: float = 1.0,
    ) -> None:
        """Map ``(code_system_uri, code)`` to a node; re-adding updates the row."""
        for name, value in (
            ("code_system_uri", code_system_uri),
            ("code", code),
            ("target_node_id", target_node_id),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} is required")
        if relation not in CROSSWALK_RELATIONS:
            raise ValueError(f"relation {relation!r} is not one of {CROSSWALK_RELATIONS}")
        params = [
            code_system_uri,
            code,
            validate_graph_name(target_graph),
            target_node_id,
            relation,
            source,
            source_version,
            float(confidence),
        ]
        cursor = self.conn.cursor()
        try:
            cursor.execute(self._CROSSWALK_UPSERT, params)
            self.conn.commit()
        finally:
            cursor.close()

    def migrate_fhir_bridges_to_crosswalk(self) -> Dict[str, int]:
        """Copy every ``fhir_bridges`` row into ``code_crosswalk`` (FR-015).

        Bridges point at default-graph nodes and say nothing about how close the
        codes are, so each becomes a ``related`` row in the default graph with
        ``source='fhir_bridges'`` and ``source_version=bridge_type``. An upsert,
        so running it again rewrites the same rows.
        """
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT fhir_code, kg_node_id, fhir_code_system, bridge_type, confidence "
                f"FROM {self._t('fhir_bridges')}"
            )
            rows = cursor.fetchall()
            for code, node, system, bridge_type, confidence in rows:
                cursor.execute(
                    self._CROSSWALK_UPSERT,
                    [
                        code_system_uri(system or "ICD10CM"),
                        code,
                        "",
                        node,
                        "related",
                        "fhir_bridges",
                        bridge_type,
                        1.0 if confidence is None else float(confidence),
                    ],
                )
            self.conn.commit()
            return {"migrated": len(rows)}
        finally:
            cursor.close()
