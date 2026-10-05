"""Shared helpers for the spec 236 unit tests: fake library reports and the proof schema."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "specs/236-arno-callout-adoption/contracts/arno-reader-proof.schema.json"

LIBRARY_VERSION = "arno-callout 1.1.0+3b2123b8e7bb"
SHA = "da28ca7184e2d33a68af86a5f875cca32293a021dbf5388d776f03c9c299ab03"
FIXTURES = ["path", "star", "dense", "multi_component", "tie_heavy"]


def fake_report(readers=None, version=LIBRARY_VERSION) -> str:
    """The text the library's ``capabilities`` entry returns."""
    return json.dumps({"version": version, "readers": dict(readers or {})})


def proof_doc(**min_revisions) -> dict:
    """A schema-valid proof document with the given ``algorithm=min_revision`` entries."""
    return {
        "library": {"version": LIBRARY_VERSION, "sha256": {"arm64": SHA}},
        "proven_at": "2026-10-04",
        "algorithms": {
            name: {"min_revision": rev, "fixtures": list(FIXTURES), "repeats": 5}
            for name, rev in min_revisions.items()
        },
    }


def schema_errors(doc) -> list[str]:
    """Messages from validating ``doc`` against the committed proof schema."""
    import jsonschema

    schema = json.loads(SCHEMA_PATH.read_text())
    validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
    return sorted(e.message for e in validator.iter_errors(doc))


def arm_native(store, algorithms=("pagerank", "ppr", "wcc", "cdlp", "subgraph")):
    """Make a hand-built store route ``algorithms`` to their Native Readers.

    Legacy dispatch tests stub ``_arno_available``; since spec 236 the Native Reader
    also needs a reported revision and a proof entry.
    """
    from iris_vector_graph.arno_readers import Proof

    store._arno_reader_report = {name: 1 for name in algorithms}
    store._arno_proof = Proof.from_dict(proof_doc(**{name: 1 for name in algorithms}))
    return store
