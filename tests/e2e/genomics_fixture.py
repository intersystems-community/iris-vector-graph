"""Spec 233: the HL7 Genomics Reporting IG 3.0.0 examples as one E2E fixture, and
the SO + MONDO slice its codes resolve through.

Pure helpers, no IRIS. `assemble` turns the IG's example files into one resource set
(research R2), and `ontology_triples` cuts the ontology slice (research R5). Each is
vendored once:

    python -m tests.e2e.genomics_fixture vendor /tmp/gr/p.tgz
    python -m tests.e2e.genomics_fixture vendor-ontology --so /tmp/onto/so.owl --mondo /tmp/onto/mondo.owl
"""

from __future__ import annotations

import argparse
import collections
import copy
import hashlib
import json
import os
import re
import sys
import tarfile
import urllib.parse
import xml.etree.ElementTree as ET

SYSTEMS = {
    "http://www.genenames.org": "hgnc",
    "http://varnomen.hgvs.org": "hgvs",
    "http://www.ncbi.nlm.nih.gov/clinvar": "clinvar",
    "http://www.sequenceontology.org": "so",
    "http://purl.obolibrary.org/obo/mondo.owl": "mondo",
}
CONCEPT_GRAPH = "concepts:ivg233"
PACKAGE_SHA256 = "7a42cdfae8d47f0d39a2ad5c46ec9028ede4848655600e7deaff440b44d4d221"

_HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(_HERE, "fixtures", "fhir", "genomics", "genomics-reporting-3.0.0.json")
ONTOLOGY = os.path.join(_HERE, "fixtures", "fhir", "genomics", "ontology.ttl")

# Research R5 pins.
SO_SHA256 = "28d19f7767d8848ceb9088658079ede9b467f4ee95205220520229a45582c6aa"
MONDO_SHA256 = "358230f024897bdb7dd29ef3c19c59df3c6d7597d0b74b5ce926f7591f215105"

MAX_ID = 54  # 64, less the 10-character run prefix `t` + 8 hex + `-`
_ABSOLUTE = re.compile(r"^https?://[^ ]*?/([A-Z][A-Za-z]+/[A-Za-z0-9\-.]+)$")


def _key(resource: dict) -> str:
    return f"{resource['resourceType']}/{resource['id']}"


def _walk_refs(node, fn):
    """Replace every string `reference` value in place with fn(value)."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "reference" and isinstance(v, str):
                node[k] = fn(v)
            else:
                _walk_refs(v, fn)
    elif isinstance(node, list):
        for v in node:
            _walk_refs(v, fn)


def _strip_narrative(resource: dict) -> None:
    for r in [resource, *resource.get("contained", [])]:
        if isinstance(r.get("text"), dict):
            del r["text"]


def _absolute_to_key(ref: str) -> str:
    m = _ABSOLUTE.match(ref)
    return m.group(1) if m else ref


def assemble(examples: list[dict]) -> tuple[list[dict], dict]:
    """IG example resources -> (merged resources sorted by key, counts). See
    contracts/fixture.md and research R2."""
    counts = dict.fromkeys(
        ["standalone", "bundle_entries", "dup_standalone", "dup_bundle", "differing",
         "shortened", "contained_refs", "assigned", "kept"],
        0,
    )
    standalone, bundles = [], []
    for r in examples:
        if r["resourceType"] == "Bundle":
            bundles.append(r)
        elif r["resourceType"] != "Parameters":
            standalone.append(copy.deepcopy(r))
    counts["standalone"] = len(standalone)

    for r in standalone:
        _walk_refs(r, _absolute_to_key)
    merged = {_key(r): r for r in standalone}
    origin = dict.fromkeys(merged, "standalone")

    for bundle in sorted(bundles, key=lambda b: b["id"]):
        entries = [copy.deepcopy(e) for e in bundle.get("entry", []) if "resource" in e]
        counts["bundle_entries"] += len(entries)
        local = {}
        for e in entries:
            res, url = e["resource"], e.get("fullUrl", "")
            if not res.get("id"):
                res["id"] = url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
                counts["assigned"] += 1
            if url:
                local[url] = _key(res)
        for e in entries:
            res = e["resource"]
            _walk_refs(res, lambda ref: local.get(ref, _absolute_to_key(ref)))
            k = _key(res)
            if k in merged:
                counts["dup_standalone" if origin[k] == "standalone" else "dup_bundle"] += 1
                if _comparable(merged[k]) != _comparable(res):
                    counts["differing"] += 1
                continue
            merged[k] = res
            origin[k] = bundle["id"]

    renames = {}
    for k, r in list(merged.items()):
        if len(r["id"]) > MAX_ID:
            new_id = r["id"][:41] + "-" + hashlib.sha256(r["id"].encode()).hexdigest()[:12]
            renames[k] = f"{r['resourceType']}/{new_id}"
            r["id"] = new_id
            counts["shortened"] += 1
    resources = sorted(merged.values(), key=_key)
    keys = {_key(r) for r in resources}
    for r in resources:
        _strip_narrative(r)
        contained = {c.get("id") for c in r.get("contained", [])}

        def check(ref, r=r, contained=contained):
            ref = renames.get(ref, ref)
            if ref.startswith("#"):
                if ref[1:] not in contained:
                    raise ValueError(f"{_key(r)}: {ref} names no contained resource")
                counts["contained_refs"] += 1
            elif ref not in keys:
                raise ValueError(f"{_key(r)}: {ref} resolves to no resource")
            return ref

        _walk_refs(r, check)
    counts["kept"] = len(resources)
    return resources, counts


def _comparable(resource: dict) -> dict:
    r = copy.deepcopy(resource)
    _strip_narrative(r)
    return r


# Reference search params per type, from HS_FHIRServer_Storage_Json.SearchColumn
# (research R3): param -> element path. `patient` params are `subject`/`for`/`target`
# where the target is a Patient. Canonical-typed params are left out.
_REF_PARAMS = {
    "DiagnosticReport": {
        "based-on": "basedOn", "encounter": "encounter", "media": "media.link",
        "performer": "performer", "result": "result", "results-interpreter": "resultsInterpreter",
        "specimen": "specimen", "subject": "subject",
    },
    "Device": {"location": "location", "organization": "owner", "patient": "patient"},
    "DocumentReference": {
        "authenticator": "authenticator", "author": "author", "custodian": "custodian",
        "encounter": "context.encounter", "related": "context.related",
        "relatesto": "relatesTo.target", "subject": "subject",
    },
    "MedicationStatement": {
        "context": "context", "medication": "medicationReference", "part-of": "partOf",
        "source": "informationSource", "subject": "subject",
    },
    "MolecularSequence": {"patient": "patient"},
    "Observation": {
        "based-on": "basedOn", "derived-from": "derivedFrom", "device": "device",
        "encounter": "encounter", "focus": "focus", "has-member": "hasMember", "part-of": "partOf",
        "performer": "performer", "specimen": "specimen", "subject": "subject",
    },
    "Organization": {"endpoint": "endpoint", "partof": "partOf"},
    "Patient": {"general-practitioner": "generalPractitioner", "link": "link.other", "organization": "managingOrganization"},
    "Procedure": {
        "based-on": "basedOn", "encounter": "encounter", "location": "location", "part-of": "partOf",
        "performer": "performer.actor", "reason-reference": "reasonReference", "subject": "subject",
    },
    "Provenance": {"agent": "agent.who", "entity": "entity.what", "location": "location", "target": "target"},
    "RiskAssessment": {
        "condition": "condition", "encounter": "encounter", "performer": "performer", "subject": "subject",
    },
    "ServiceRequest": {
        "based-on": "basedOn", "encounter": "encounter", "performer": "performer", "replaces": "replaces",
        "requester": "requester", "specimen": "specimen", "subject": "subject",
    },
    "Specimen": {"collector": "collection.collector", "parent": "parent", "subject": "subject"},
    "Task": {
        "based-on": "basedOn", "encounter": "encounter", "focus": "focus", "owner": "owner",
        "part-of": "partOf", "requester": "requester", "subject": "for",
    },
}
# The `patient` param indexes this path when the target is a Patient.
_PATIENT_OF = {
    "DiagnosticReport": "subject", "DocumentReference": "subject", "MedicationStatement": "subject",
    "Observation": "subject", "Procedure": "subject", "Provenance": "target", "RiskAssessment": "subject",
    "ServiceRequest": "subject", "Specimen": "subject", "Task": "for",
}


def _at(node, path: str) -> list[str]:
    """The `reference` strings at a dotted element path, through lists."""
    nodes = [node]
    for part in path.split("."):
        nxt = []
        for n in nodes:
            v = n.get(part) if isinstance(n, dict) else None
            nxt.extend(v if isinstance(v, list) else [] if v is None else [v])
        nodes = nxt
    return [n["reference"] for n in nodes if isinstance(n, dict) and isinstance(n.get("reference"), str)]


def expected_edges(resources: list[dict]) -> set[tuple[str, str, str]]:
    """(s_key, param, o_key) for every indexed reference element, mapped to its
    search param per research R3. A Patient subject yields both `subject` and
    `patient`. `#contained` references and references outside the search params
    (extensions, for instance) give nothing. The oracle for FR-004 and SC-001."""
    out = set()
    for r in resources:
        src, rtype = _key(r), r["resourceType"]
        for param, path in _REF_PARAMS.get(rtype, {}).items():
            for ref in _at(r, path):
                if not ref.startswith("#"):
                    out.add((src, param, ref))
        if rtype in _PATIENT_OF:
            for ref in _at(r, _PATIENT_OF[rtype]):
                if ref.startswith("Patient/"):
                    out.add((src, "patient", ref))
    return out


def _ref_paths(node, path=()):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "reference" and isinstance(v, str):
                yield ".".join(path), v
            elif k != "contained":
                yield from _ref_paths(v, (*path, k))
    elif isinstance(node, list):
        for v in node:
            yield from _ref_paths(v, path)


def unindexed_refs(resources: list[dict]) -> set[tuple[str, str, str]]:
    """(s_key, element path, o_key) for every non-contained reference that no
    search param indexes (extensions, Task.reasonReference, ...). These are
    neither edges nor unresolved rows; the topology E2E names them by path."""
    out = set()
    for r in resources:
        indexed = set(_REF_PARAMS.get(r["resourceType"], {}).values())
        for path, ref in _ref_paths(r):
            if not ref.startswith("#") and path not in indexed:
                out.add((_key(r), path, ref))
    return out


# ------------------------------------------------------------------ ontology (US2)

OBO = "http://purl.obolibrary.org/obo/"
GENE = "http://identifiers.org/hgnc/"
BIOLINK = "https://w3id.org/biolink/vocab/"
RDFS_SUB = "http://www.w3.org/2000/01/rdf-schema#subClassOf"
RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
GENE_OF_CONDITION = BIOLINK + "gene_associated_with_condition"
VARIANT_OF = BIOLINK + "is_sequence_variant_of"
RO_GENE = OBO + "RO_0004003"  # has material basis in germline mutation in

# Classes above most of the slice: walking in from any of them reaches everything.
STOP = {
    OBO + "BFO_0000001": "BFO entity",
    OBO + "BFO_0000002": "BFO continuant",
    OBO + "BFO_0000016": "BFO disposition",
    OBO + "BFO_0000017": "BFO realizable entity",
    OBO + "BFO_0000020": "BFO specifically dependent continuant",
    OBO + "MONDO_0000001": "disease: the root, above all 73 seeds",
    OBO + "MONDO_0700096": "human disease: above all 73 seeds",
    OBO + "MONDO_0003847": "hereditary disease: above 70+ seeds",
    **{OBO + f"MONDO_777000{n}": "a 'disease by ...' grouping, not a clinical class" for n in (6, 7, 8, 9)},
    OBO + "SO_0000110": "sequence_feature: root",
    OBO + "SO_0000001": "region: root",
    OBO + "SO_0001411": "biological_region: root",
    OBO + "SO_0001060": "sequence_variant: above every fixture variant code",
    OBO + "SO_0002072": "sequence_comparison: above every fixture variant code",
}

# (IRI, hops, direction): research R5 seeds, in spec US2 scenario order.
SEEDS = [
    (OBO + "MONDO_0019052", 6, "in"),
    (GENE + "2621", 0, "out"),
    (GENE + "1100", 0, "out"),
    (OBO + "SO_0001818", 3, "in"),
]

_DIRECTIONS = ("out", "in", "both")
_OBO_CODE = re.compile(r"^(SO|MONDO)[:_](\d{7})$")


def code_iri(system: str, code) -> str | None:
    """The concept IRI for a coding, or None for an uncrosswalked system (RefSeq)
    or a coding with no code. Research R5, "Ids"."""
    if not isinstance(code, str) or not code:
        return None
    kind = SYSTEMS.get(system)
    if kind == "hgnc":
        m = re.fullmatch(r"HGNC:(\d+)", code)
        return GENE + m.group(1) if m else None
    if kind in ("so", "mondo"):
        m = _OBO_CODE.match(code)
        return f"{OBO}{m.group(1)}_{m.group(2)}" if m and m.group(1).lower() == kind else None
    if kind == "clinvar":
        return "http://identifiers.org/clinvar:" + code
    if kind == "hgvs":
        return "urn:ivg233:hgvs:" + urllib.parse.quote(code, safe="")
    return None


def _codings(node):
    if isinstance(node, dict):
        if isinstance(node.get("system"), str) and ("code" in node or "display" in node):
            yield node
        for v in node.values():
            yield from _codings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _codings(v)


def crosswalk_rows(resources: list[dict]) -> tuple[list[tuple[str, str, str]], dict]:
    """Distinct (system, code, iri) over every coding of a crosswalked system, sorted,
    and counts over distinct codings: `clean` (the code is already canonical),
    `normalized` (the code needed rewriting, `SO_0002054`) and `unmapped` (a display
    with no code: JAK2, KDR, ERBB4). The code wins over the display."""
    rows, unmapped = {}, set()
    for r in resources:
        for c in _codings(r):
            system = c["system"]
            if system not in SYSTEMS:
                continue
            iri = code_iri(system, c.get("code"))
            if iri is None:
                unmapped.add((system, c.get("code"), c.get("display")))
            else:
                rows[(system, c["code"])] = iri
    normalized = sum(1 for (system, code) in rows if SYSTEMS[system] in ("so", "mondo") and ":" not in code)
    counts = {"normalized": normalized, "clean": len(rows) - normalized, "unmapped": len(unmapped)}
    return [(s, c, i) for (s, c), i in sorted(rows.items())], counts


def _observation_iris(obs: dict) -> set[str]:
    """code_iri of every coding on `component[*].valueCodeableConcept` and on
    `valueCodeableConcept`: the paths `component-value-concept` and `value-concept`
    index (research R4)."""
    ccs = [c.get("valueCodeableConcept") for c in obs.get("component", [])]
    ccs.append(obs.get("valueCodeableConcept"))
    out = set()
    for cc in ccs:
        for c in (cc or {}).get("coding", []):
            iri = code_iri(c.get("system"), c.get("code"))
            if iri:
                out.add(iri)
    return out


def ontology_triples(so, mondo, resources: list[dict]) -> set[tuple]:
    """rdflib Graphs in, (s, p, o) rdflib-term triples out (research R5):

    - the named-class `rdfs:subClassOf` closure above the fixture's SO and MONDO
      codes and above every disease with an RO:0004003 axiom to a fixture gene,
      cut at STOP and skipping deprecated classes;
    - `gene_associated_with_condition`, gene -> disease, from those axioms;
    - `is_sequence_variant_of`, variant -> gene, for an HGVS or ClinVar code and an
      HGNC code on one Observation;
    - `rdfs:label` of every node and every fixture SO or MONDO code, where the
      ontology has one."""
    from rdflib import OWL, RDFS, BNode, Literal, URIRef

    iris = [code_iri(c["system"], c.get("code")) for r in resources for c in _codings(r)]
    genes = {i for i in iris if i and i.startswith(GENE)}
    fixture_obo = {i for i in iris if i and i.startswith(OBO)}

    def deprecated(g, node):
        return (node, OWL.deprecated, Literal(True)) in g

    out = set()
    diseases = set()
    for restriction in mondo.subjects(OWL.onProperty, URIRef(RO_GENE)):
        for gene in mondo.objects(restriction, OWL.someValuesFrom):
            if str(gene) not in genes:
                continue
            for disease in mondo.subjects(RDFS.subClassOf, restriction):
                if isinstance(disease, URIRef) and not deprecated(mondo, disease):
                    out.add((gene, URIRef(GENE_OF_CONDITION), disease))
                    diseases.add(str(disease))

    nodes = set()
    for g, seeds in ((so, {i for i in fixture_obo if i.startswith(OBO + "SO_")}),
                     (mondo, diseases | {i for i in fixture_obo if i.startswith(OBO + "MONDO_")})):
        todo = [URIRef(i) for i in seeds if i not in STOP]
        while todo:
            node = todo.pop()
            if node in nodes:
                continue
            nodes.add(node)
            for parent in g.objects(node, RDFS.subClassOf):
                if isinstance(parent, BNode) or str(parent) in STOP or deprecated(g, parent):
                    continue
                out.add((node, RDFS.subClassOf, parent))
                todo.append(parent)

    for r in resources:
        if r["resourceType"] != "Observation":
            continue
        codes = _observation_iris(r)
        obs_genes = {i for i in codes if i.startswith(GENE)}
        variants = {i for i in codes if i.startswith(("urn:ivg233:hgvs:", "http://identifiers.org/clinvar:"))}
        for v in variants:
            for gene in obs_genes:
                out.add((URIRef(v), URIRef(VARIANT_OF), URIRef(gene)))

    # A fixture code whose only parents are stop-listed (SO_0002073) has no edge;
    # its label keeps it a node the crosswalk can point to.
    labelled = {t for s, _, o in out for t in (s, o)} | {URIRef(i) for i in fixture_obo if i not in STOP}
    for node in labelled:
        for g in (so, mondo):
            for label in g.objects(node, RDFS.label):
                if isinstance(label, Literal):
                    out.add((node, RDFS.label, Literal(str(label))))
    return out


def ntriples(triples) -> str:
    """Sorted N-Triples lines, one per triple: the vendored file's exact form."""
    return "".join(sorted(f"{s.n3()} {p.n3()} {o.n3()} .\n" for s, p, o in triples))


def load_ontology(path: str = ONTOLOGY) -> list[tuple[str, str, str]]:
    """The vendored slice as (s, p, o) strings; a label's o is its text."""
    from rdflib import Graph

    g = Graph()
    g.parse(path, format="nt")
    return sorted((str(s), str(p), str(o)) for s, p, o in g)


def expand(edges, seeds: list[str], hops: int, direction: str = "out") -> set[str]:
    """Mirrors Graph.KG.FHIRGraph.ExpandConcepts over (s, p, o) triples; seeds included."""
    if direction not in _DIRECTIONS:
        raise ValueError(f"direction must be one of {_DIRECTIONS}, got {direction!r}")
    step: dict[str, set[str]] = {}
    for s, _, o in edges:
        if direction in ("out", "both"):
            step.setdefault(s, set()).add(o)
        if direction in ("in", "both"):
            step.setdefault(o, set()).add(s)
    seen = set(seeds)
    frontier = set(seeds)
    for _ in range(hops):
        frontier = {n for f in frontier for n in step.get(f, ())} - seen
        if not frontier:
            break
        seen |= frontier
    return seen


def expected(resources: list[dict], iris: set[str]) -> set[str]:
    """The `Observation/id` keys whose component value or value carries a code whose
    code_iri is in iris. The oracle for FR-006 and SC-002."""
    return {_key(r) for r in resources if r["resourceType"] == "Observation" and _observation_iris(r) & set(iris)}


def patients_of(resources: list[dict], obs_keys: set[str]) -> set[str]:
    """The `Patient/id` keys that `subject` points to from obs_keys."""
    out = set()
    for r in resources:
        if _key(r) in obs_keys:
            ref = (r.get("subject") or {}).get("reference", "")
            if ref.startswith("Patient/"):
                out.add(ref)
    return out


# ------------------------------------------------------------------ US3 provenance


def model_result(version: int, patient_key: str, input_keys: list[str]) -> list[dict]:
    """[Device, Observation, Provenance] for one model version (data-model.md): the
    model, its prediction for patient_key, and the Provenance linking the prediction
    to the model and to input_keys. Ids are unprefixed; run them through
    prefix_resources."""
    dev, pred, prov = f"model-v{version}", f"pred-v{version}", f"prov-v{version}"
    return [
        {
            "resourceType": "Device",
            "id": dev,
            "deviceName": [{"name": "genomic-risk-model", "type": "model-name"}],
            "version": [{"value": str(version)}],
        },
        {
            "resourceType": "Observation",
            "id": pred,
            "status": "final",
            "code": {"text": "genomic risk score"},
            "subject": {"reference": patient_key},
            "valueQuantity": {"value": round(0.5 + version / 100, 2)},
        },
        {
            "resourceType": "Provenance",
            "id": prov,
            "target": [{"reference": f"Observation/{pred}"}],
            "recorded": "2026-09-24T00:00:00Z",
            "agent": [{"who": {"reference": f"Device/{dev}"}}],
            "entity": [{"role": "source", "what": {"reference": k}} for k in input_keys],
        },
    ]


_RDF = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
_OWL = "{http://www.w3.org/2002/07/owl#}"
_RDFS = "{http://www.w3.org/2000/01/rdf-schema#}"


def load_owl(path: str):
    """The rdflib Graph of an OWL RDF/XML file, cut to what ontology_triples reads:
    each top-level owl:Class's named and RO:0004003 restriction superclasses, its
    rdfs:label, and owl:deprecated. Streaming, because rdflib's full parse of MONDO
    (3.2M triples) is too slow and large to run as a vendoring step.

    A restriction is either inline under rdfs:subClassOf or a top-level
    owl:Restriction the subClassOf names by rdf:nodeID; MONDO writes most of its
    7,450 RO:0004003 axioms the second way."""
    from rdflib import OWL, RDFS, BNode, Graph, Literal, URIRef

    g = Graph()
    node_ref = []  # (class, nodeID) from <rdfs:subClassOf rdf:nodeID=...>
    gene_of = {}  # nodeID -> someValuesFrom, for RO:0004003 restrictions

    def restriction_target(r):
        prop = r.find(_OWL + "onProperty")
        val = r.find(_OWL + "someValuesFrom")
        if prop is None or val is None or prop.get(_RDF + "resource") != RO_GENE:
            return None
        return val.get(_RDF + "resource")

    def add_restriction(node, target):
        b = BNode()
        g.add((node, RDFS.subClassOf, b))
        g.add((b, OWL.onProperty, URIRef(RO_GENE)))
        g.add((b, OWL.someValuesFrom, URIRef(target)))

    depth = 0
    for event, el in ET.iterparse(path, events=("start", "end")):
        if event == "start":
            depth += 1
            continue
        depth -= 1
        if depth != 1:
            continue
        if el.tag == _OWL + "Restriction" and el.get(_RDF + "nodeID"):
            target = restriction_target(el)
            if target:
                gene_of[el.get(_RDF + "nodeID")] = target
        elif el.tag == _OWL + "Class" and el.get(_RDF + "about"):
            node = URIRef(el.get(_RDF + "about"))
            for child in el:
                if child.tag == _RDFS + "label" and child.text:
                    g.add((node, RDFS.label, Literal(child.text)))
                elif child.tag == _OWL + "deprecated" and (child.text or "").strip() == "true":
                    g.add((node, OWL.deprecated, Literal(True)))
                elif child.tag == _RDFS + "subClassOf":
                    if child.get(_RDF + "resource"):
                        g.add((node, RDFS.subClassOf, URIRef(child.get(_RDF + "resource"))))
                    elif child.get(_RDF + "nodeID"):
                        node_ref.append((node, child.get(_RDF + "nodeID")))
                    for r in child.iter(_OWL + "Restriction"):
                        target = restriction_target(r)
                        if target:
                            add_restriction(node, target)
        el.clear()
    for node, node_id in node_ref:
        if node_id in gene_of:
            add_restriction(node, gene_of[node_id])
    return g


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _vendor_ontology(so_path: str, mondo_path: str) -> int:
    for path, want in ((so_path, SO_SHA256), (mondo_path, MONDO_SHA256)):
        got = _sha256(path)
        if got != want:
            print(f"{path}: sha256 {got}, expected {want}", file=sys.stderr)
            return 1
    triples = ontology_triples(load_owl(so_path), load_owl(mondo_path), load_fixture())
    with open(ONTOLOGY, "w") as fh:
        fh.write(ntriples(triples))
    edges = [t for t in triples if str(t[1]) != RDFS_LABEL]
    # As import_rdf counts them: a labelled concept with no edge is still a node.
    nodes = {str(s) for s, _, _ in triples} | {str(o) for _, _, o in edges}
    by_pred = collections.Counter(str(p).rsplit("/", 1)[-1].rsplit("#", 1)[-1] for _, p, _ in edges)
    print(f"nodes: {len(nodes)}, edges: {len(edges)}, labels: {len(triples) - len(edges)}")
    for p, n in sorted(by_pred.items()):
        print(f"{p}: {n}")
    return 0


def read_package(tgz: str) -> list[dict]:
    """The `package/example/*.json` resources of the IG tarball, in file-name order.
    Raises ValueError when the tarball's sha256 is not the pinned one."""
    with open(tgz, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    if digest != PACKAGE_SHA256:
        raise ValueError(f"{tgz}: sha256 {digest}, expected {PACKAGE_SHA256}")
    with tarfile.open(tgz) as tar:
        members = sorted(
            (m for m in tar.getmembers() if re.fullmatch(r"package/example/[^/]+\.json", m.name)),
            key=lambda m: m.name,
        )
        return [json.load(tar.extractfile(m)) for m in members]


def load_fixture(path: str = FIXTURE) -> list[dict]:
    """The vendored collection Bundle's resources."""
    with open(path) as fh:
        return [e["resource"] for e in json.load(fh)["entry"]]


def _vendor(tgz: str, exclude: list[str]) -> int:
    try:
        examples = read_package(tgz)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    resources, counts = assemble(examples)
    resources = [r for r in resources if _key(r) not in set(exclude)]
    bundle = {
        "resourceType": "Bundle",
        "id": "genomics-reporting-3.0.0",
        "type": "collection",
        "entry": [{"resource": r} for r in resources],
    }
    with open(FIXTURE, "w") as fh:
        json.dump(bundle, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print("```text")
    for k, v in counts.items():
        print(f"{k}: {v}")
    print("```")
    print(f"excluded: {len(exclude)}, written: {len(resources)}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tests.e2e.genomics_fixture")
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("vendor", help="write FIXTURE from the IG tarball")
    v.add_argument("tgz")
    v.add_argument("--exclude", action="append", default=[], metavar="Type/id")
    o = sub.add_parser("vendor-ontology", help="write ONTOLOGY from the pinned SO and MONDO OWL files")
    o.add_argument("--so", required=True)
    o.add_argument("--mondo", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "vendor-ontology":
        return _vendor_ontology(args.so, args.mondo)
    return _vendor(args.tgz, args.exclude)


if __name__ == "__main__":
    sys.exit(main())
