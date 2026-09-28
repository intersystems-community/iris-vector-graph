"""Spec 235: the Synthea R4 fixture and the interpretation oracles.

Pure helpers, no IRIS. `assemble` turns Synthea's transaction bundles into one
resource set (research R16); the `expected_*` functions compute, from that set
alone, what the graph's interpretation must hold. The fixture is vendored once:

    python -m tests.e2e.interp_fixture vendor ~/.cache/ivg-235/synthea-10/fhir
"""

from __future__ import annotations

import argparse
import collections
import copy
import glob
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(_HERE, "fixtures", "fhir", "synthea", "synthea-r4-10.json")
DROP_TYPES = {"Claim", "ExplanationOfBenefit"}
MAX_ID = 54  # 64, less the 10-character run prefix `t` + 8 hex + `-`
SUPPORT_PREFIXES = ("hospitalInformation", "practitionerInformation")

_KEY = re.compile(r"^[A-Z][A-Za-z]+/[A-Za-z0-9\-.]{1,64}$")
_CONDITIONAL = re.compile(r"^([A-Z][A-Za-z]+)\?identifier=([^|]*)\|(.+)$")


def _key(resource: dict) -> str:
    return f"{resource['resourceType']}/{resource['id']}"


def _refs(node):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "reference" and isinstance(v, str):
                yield v
            else:
                yield from _refs(v)
    elif isinstance(node, list):
        for v in node:
            yield from _refs(v)


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


def _is_dropped(node) -> bool:
    ref = node.get("reference") if isinstance(node, dict) else None
    return isinstance(ref, str) and ref.split("/", 1)[0] in DROP_TYPES


def _prune_dropped(node, owner: str) -> int:
    """Remove list items that are References to a DROP_TYPES resource. A
    single-valued reference to one raises: removing it would lose data silently."""
    removed = 0
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, list):
                kept = [x for x in v if not _is_dropped(x)]
                removed += len(v) - len(kept)
                node[k] = kept
                for x in kept:
                    removed += _prune_dropped(x, owner)
            elif _is_dropped(v):
                raise ValueError(f"{owner}.{k} references {v['reference']}, a dropped type")
            else:
                removed += _prune_dropped(v, owner)
    return removed


def _strip(node) -> None:
    """Drop the `data` of every Attachment, at any depth. An Attachment is known by
    its `contentType`, because it need not sit under an `attachment` key
    (DiagnosticReport.presentedForm is a list of them)."""
    if isinstance(node, dict):
        if "contentType" in node:
            node.pop("data", None)
        for v in node.values():
            _strip(v)
    elif isinstance(node, list):
        for v in node:
            _strip(v)


def _strip_narrative(resource: dict) -> None:
    for r in [resource, *resource.get("contained", [])]:
        if isinstance(r.get("text"), dict):
            del r["text"]


def assemble(patient_bundles: list[dict], support_bundles: list[dict]) -> tuple[list[dict], dict]:
    """Synthea output -> (resources sorted by key, counts). See contracts/fixture.md
    and research R16. The inputs are not modified."""
    counts = {"rewritten": 0, "conditional": 0, "dropped_refs": 0, "links_added": 0}

    # Support resources, found by identifier or by their own bundle's fullUrl.
    ident, support = {}, {}
    for bundle in support_bundles:
        urls = {e["fullUrl"]: _key(e["resource"]) for e in bundle.get("entry", []) if "fullUrl" in e}
        for e in bundle.get("entry", []):
            r = copy.deepcopy(e["resource"])
            support[_key(r)] = (r, urls)
            for i in r.get("identifier", []):
                ident[(r["resourceType"], i.get("system", ""), i.get("value"))] = _key(r)

    def resolver(urls: dict, owner: str):
        def fn(ref: str) -> str:
            if ref.startswith("urn:uuid:"):
                if ref not in urls:
                    raise ValueError(f"{owner}: unresolvable {ref}")
                counts["rewritten"] += 1
                return urls[ref]
            m = _CONDITIONAL.match(ref)
            if m:
                target = ident.get(m.groups())
                if target is None:
                    raise ValueError(f"{owner}: unresolvable conditional {ref}")
                counts["conditional"] += 1
                return target
            return ref

        return fn

    kept = {}
    for bundle in patient_bundles:
        entries = bundle.get("entry", [])
        urls = {e["fullUrl"]: _key(e["resource"]) for e in entries if "fullUrl" in e}
        for e in entries:
            r = copy.deepcopy(e["resource"])
            if r["resourceType"] in DROP_TYPES:
                continue
            _walk_refs(r, resolver(urls, _key(r)))
            kept[_key(r)] = r

    # Support resources reached from the kept set, transitively.
    pending = [ref for r in kept.values() for ref in _refs(r) if ref in support and ref not in kept]
    while pending:
        k = pending.pop()
        if k in kept:
            continue
        r, urls = support[k]
        _walk_refs(r, resolver(urls, k))
        kept[k] = r
        pending.extend(ref for ref in _refs(r) if ref in support and ref not in kept)

    for k, r in kept.items():
        counts["dropped_refs"] += _prune_dropped(r, k)
        _strip_narrative(r)
        _strip(r)

    patients = sorted(k for k in kept if k.startswith("Patient/"))
    if len(patients) >= 2:
        a, b = patients[:2]
        for src, dst in ((a, b), (b, a)):
            kept[src].setdefault("link", []).append({"other": {"reference": dst}, "type": "seealso"})
            counts["links_added"] += 1

    bad = sorted({ref for r in kept.values() for ref in _refs(r) if not _KEY.match(ref) or ref not in kept})
    if bad:
        raise ValueError(f"{len(bad)} references outside the set: {bad[:5]}")

    resources = [kept[k] for k in sorted(kept)]
    counts["types"] = dict(sorted(collections.Counter(r["resourceType"] for r in resources).items()))
    counts["kept"] = len(resources)
    return resources, counts


def load_fixture(path: str = FIXTURE) -> list[dict]:
    """The vendored collection Bundle's resources."""
    with open(path) as fh:
        return [e["resource"] for e in json.load(fh)["entry"]]


load_synthea = load_fixture

LOAD_ORDER = ("Organization", "Location", "Practitioner", "Patient", "Medication", "Encounter")


def load_order(resources: list[dict]) -> list[dict]:
    """LOAD_ORDER types first, the rest after, each group in its input order, so
    every PUT finds the resources it references."""
    rank = {t: i for i, t in enumerate(LOAD_ORDER)}
    return sorted(resources, key=lambda r: rank.get(r["resourceType"], len(LOAD_ORDER)))


def read_synthea(directory: str) -> tuple[list[dict], list[dict]]:
    """(patient bundles, support bundles) from a Synthea `fhir` output directory,
    each in file-name order."""
    patients, support = [], []
    for path in sorted(glob.glob(os.path.join(directory, "*.json"))):
        with open(path) as fh:
            bundle = json.load(fh)
        (support if os.path.basename(path).startswith(SUPPORT_PREFIXES) else patients).append(bundle)
    return patients, support


def counts_lines(counts: dict) -> list[str]:
    """The counts as SOURCE.md's `key: value` lines, per-type counts first."""
    lines = [f"{t}: {n}" for t, n in counts["types"].items()]
    lines += [f"{k}: {v}" for k, v in counts.items() if k != "types"]
    return lines


def vendor(directory: str, out: str = FIXTURE) -> dict:
    """Write the fixture Bundle for a Synthea output directory; returns the counts."""
    resources, counts = assemble(*read_synthea(directory))
    bundle = {
        "resourceType": "Bundle",
        "id": "synthea-r4-10",
        "type": "collection",
        "entry": [{"resource": r} for r in resources],
    }
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as fh:
        json.dump(bundle, fh, indent=1, sort_keys=True)
        fh.write("\n")
    return counts


# ------------------------------------------------------------------ compartment oracle

_PATH = re.compile(r"^([A-Z][A-Za-z]+)\.([A-Za-z][\w.]*?)(?:\.where\(resolve\(\) is ([A-Z][A-Za-z]+)\))?(\s+as\s+Reference)?$")


def reference_paths(fhirpath: str) -> list[tuple[str, str, str | None]]:
    """A SearchColumn FHIRPath as (type, element path, `resolve() is` type or None),
    one per `|` part. `X.a as Reference` is the R4 choice element `aReference`.
    Any other form raises, so an unhandled path is a finding, not a silent miss."""
    out = []
    for part in fhirpath.split("|"):
        part = part.strip()
        while part.startswith("(") and part.endswith(")"):
            part = part[1:-1].strip()
        if ".ofType(" in part:
            raise ValueError(f"unsupported ofType form: {part}")
        m = _PATH.match(part)
        if not m:
            raise ValueError(f"unsupported FHIRPath form: {part}")
        rtype, path, where, as_ref = m.groups()
        out.append((rtype, path + "Reference" if as_ref else path, where))
    return out


def compartment_paths(rows, comp: dict) -> dict:
    """(type, param) -> element paths, from SearchColumn (ResourceType, ParamName,
    FHIRPath) rows, for the params in `comp` (type -> params). Parts naming another
    type, or restricted to a non-Patient target, are dropped."""
    out: dict = {}
    for rtype, param, fhirpath in rows:
        if param not in comp.get(rtype, ()):
            continue
        for ptype, path, where in reference_paths(fhirpath):
            if ptype == rtype and where in (None, "Patient"):
                paths = out.setdefault((rtype, param), [])
                if path not in paths:
                    paths.append(path)
    return out


def _at(node, path: list[str]):
    """The `reference` strings at a dotted element path, through lists."""
    if isinstance(node, list):
        for v in node:
            yield from _at(v, path)
    elif isinstance(node, dict):
        if not path:
            if isinstance(node.get("reference"), str):
                yield node["reference"]
        elif path[0] in node:
            yield from _at(node[path[0]], path[1:])


def expected_compartments(resources: list[dict], comp: dict, paths: dict) -> dict:
    """{source key: {Patient key: sorted params}}: which Patients of `resources`
    each non-Patient resource reaches through its type's compartment params."""
    patients = {_key(r) for r in resources if r["resourceType"] == "Patient"}
    out = {}
    for r in resources:
        rtype = r["resourceType"]
        if rtype == "Patient" or rtype not in comp:
            continue
        via: dict = {}
        for param in comp[rtype]:
            for path in paths.get((rtype, param), []):
                for ref in _at(r, path.split(".")):
                    if ref in patients:
                        via.setdefault(ref, set()).add(param)
        if via:
            out[_key(r)] = {p: sorted(v) for p, v in sorted(via.items())}
    return out


# ------------------------------------------------------------------ category / profile oracle

# Research R8: the systems whose category codes also become labels.
CATEGORY_LABEL_SYSTEMS = (
    "http://terminology.hl7.org/CodeSystem/observation-category",
    "http://terminology.hl7.org/CodeSystem/condition-category",
    "http://hl7.org/fhir/us/core/CodeSystem/us-core-documentreference-category",
    "http://hl7.org/fhir/us/core/CodeSystem/careplan-category",
    "http://hl7.org/fhir/us/core/CodeSystem/condition-category",
    "http://hl7.org/fhir/us/core/CodeSystem/us-core-category",
)


def pascal(code: str) -> str:
    """`vital-signs` -> `VitalSigns`, splitting on `-` and `_`."""
    return "".join(p[:1].upper() + p[1:] for p in re.split(r"[-_]", code) if p)


def _category_codings(resource: dict):
    """(system, code) per category coding; a plain `code` category has no system."""
    for c in resource.get("category") or []:
        if isinstance(c, str):
            yield "", c
        elif isinstance(c, dict):
            for coding in c.get("coding") or []:
                if coding.get("code"):
                    yield coding.get("system", ""), coding["code"]


def expected_categories(resource: dict) -> list[str]:
    """The `category` property: sorted, distinct `system|code` strings."""
    return sorted({f"{s}|{c}" for s, c in _category_codings(resource)})


def expected_profiles(resource: dict) -> list[str]:
    """The `meta_profile` property: the claimed profiles, sorted, as written."""
    return sorted(set((resource.get("meta") or {}).get("profile") or []))


def expected_labels(resource: dict, r4types) -> set[str]:
    """The type label plus one per allowlisted category code, minus R4 type names."""
    out = {resource["resourceType"]}
    for s, c in _category_codings(resource):
        if s in CATEGORY_LABEL_SYSTEMS and (lab := pascal(c)) not in r4types:
            out.add(lab)
    return out


# ------------------------------------------------------------------ coverage / gaps oracle

SAMPLE_MAX = 20


def _leaves(node, path):
    """The values at an element path, through lists."""
    if isinstance(node, list):
        for v in node:
            yield from _leaves(v, path)
    elif not path:
        yield node
    elif isinstance(node, dict) and path[0] in node:
        yield from _leaves(node[path[0]], path[1:])


def _leaf_tokens(leaf):
    """(system, code) pairs a token index row holds for one CodeableConcept, Coding
    or code value."""
    if isinstance(leaf, str):
        return [("", leaf)] if leaf else []
    if not isinstance(leaf, dict):
        return []
    codings = leaf.get("coding") if "coding" in leaf else [leaf] if "code" in leaf else []
    return [(c.get("system", ""), c["code"]) for c in codings or [] if isinstance(c, dict) and c.get("code")]


def resource_tokens(resource, params, fields) -> dict:
    """{param: {(system, code)}} for the params `fields` indexes on the resource's
    type; `fields` is type -> param -> element paths."""
    by_param = fields.get(resource["resourceType"], {})
    out = {}
    for param in params:
        if param in by_param:
            out[param] = {t for path in by_param[param] for leaf in _leaves(resource, list(path)) for t in _leaf_tokens(leaf)}
    return out


def expected_coverage(resources, comp, paths, fields, resolved, cat_systems=CATEGORY_LABEL_SYSTEMS) -> dict:
    """The `types`, `resolution.by_system` and `linked_patients` of
    fhir_coverage_report over `resources`. A type is a compartment type when it has
    patient-compartment params and is not Patient. `resolved` is the set of
    (system, code) pairs code_crosswalk maps."""
    reached = expected_compartments(resources, comp, paths)
    params = sorted({p for by in fields.values() for p in by})
    types: dict = {}
    by_system: dict = {}
    linked = set()
    keys = {_key(r) for r in resources}
    for r in resources:
        rtype, key = r["resourceType"], _key(r)
        t = types.setdefault(rtype, {"count": 0, "compartment_type": bool(comp.get(rtype)) and rtype != "Patient"})
        t["count"] += 1
        if t["compartment_type"]:
            t.setdefault("with_compartment", 0)
            t.setdefault("patientless", [])
            if key in reached:
                t["with_compartment"] += 1
            else:
                t["patientless"].append(key)
        hist = t.setdefault("meta_profile", {})
        for p in expected_profiles(r):
            hist[p] = hist.get(p, 0) + 1
        cat = t.setdefault("category", {"label": 0, "local": 0, "none": 0})
        codings = list(_category_codings(r))
        cat["label" if any(s in cat_systems for s, _ in codings) else "local" if codings else "none"] += 1
        for pair in set().union(*resource_tokens(r, params, fields).values()):
            s = by_system.setdefault(pair[0], {"codes": set(), "resources": set(), "resolved_codes": set(), "resolved_resources": set()})
            s["codes"].add(pair[1])
            s["resources"].add(key)
            if pair in resolved:
                s["resolved_codes"].add(pair[1])
                s["resolved_resources"].add(key)
        if rtype == "Patient":
            for link in r.get("link") or []:
                ref = (link.get("other") or {}).get("reference")
                if isinstance(ref, str) and ref.startswith("Patient/") and ref in keys:
                    linked.update((key, ref))
    for t in types.values():
        if t["compartment_type"]:
            less = sorted(t.pop("patientless"))
            t["patientless"] = len(less)
            t["patientless_sample"] = less[:SAMPLE_MAX]
    return {
        "types": types,
        "resolution": {"by_system": {sys: {k: len(v) for k, v in s.items()} for sys, s in by_system.items()}},
        "linked_patients": len(linked),
    }


def expected_gaps(resources, params, fields, resolved, top=20) -> dict:
    """The `unmatched`, `unmatched_total` and `text_only` of fhir_concept_gaps: the
    (system, code) pairs of the params with no crosswalk row, by resource count, then
    system, then code; and per param the resources with no token but a `text`."""
    pairs: dict = {}
    text_only = {p: 0 for p in params}
    for r in resources:
        toks = resource_tokens(r, params, fields)
        for param, found in toks.items():
            for pair in found:
                if pair not in resolved:
                    pairs.setdefault(pair, set()).add(_key(r))
            if not found:
                by_param = fields[r["resourceType"]][param]
                leaves = [leaf for path in by_param for leaf in _leaves(r, list(path))]
                if any(isinstance(leaf, dict) and leaf.get("text") for leaf in leaves):
                    text_only[param] += 1
    rows = sorted(({"system": s, "code": c, "resources": len(k)} for (s, c), k in pairs.items()),
                  key=lambda x: (-x["resources"], x["system"], x["code"]))
    return {
        "unmatched": rows[:top],
        "unmatched_total": {"codes": len(pairs), "resources": len(set().union(*pairs.values()) if pairs else set())},
        "text_only": text_only,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tests.e2e.interp_fixture")
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("vendor", help="write FIXTURE from a Synthea fhir output directory")
    v.add_argument("directory")
    args = ap.parse_args(argv)
    counts = vendor(args.directory)
    print("```text")
    print("\n".join(counts_lines(counts)))
    print("```")
    print(f"{FIXTURE}: {os.path.getsize(FIXTURE)} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
