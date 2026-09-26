#!/usr/bin/env python3
"""Rescore a TCK capture (``IVG_TCK_CAPTURE=<dir>``, see ``tests/tck/capture.py``)
under a scoring rule, with no IRIS connection.

Usage::

    rescore.py <capture_dir> --mode default|typed|lenient

``default`` recomputes the same verdict the live run recorded — this is a
sanity check on the capture+rescore pipeline itself, not a new rule: the
score it reports should equal the recorded verdicts' score, and its mismatch
list should be empty (or explain a real bug in ``capture.py``/here).

``typed`` requires a top-level result cell to match by Python type, not
merely by text spelling: an integer column must return an ``int``, not the
text ``"1"``. Lists, maps, nodes, relationships and paths already compare
without text coercion (spec 229's ``comparison._Matcher`` inner calls all
pass ``text_ok=False``); ``typed`` only tightens the *outermost* cell, via
``_Matcher.match(..., text_ok=False)`` — the same call the live harness would
make, just fed the captured value instead of a fresh DB row.

Node/relationship property values are a separate case: ``comparison.py``'s
``stored()`` reads them with its own, permanent text leniency (storage
rendering, not the result-cell shape) — see ``replay_stored`` below. ``typed``
does not reach it; only a plain top-level column tightens.

``lenient`` reproduces the pre-spec-229 scoring: a raised error still counts
as an empty result, and a side-effect assertion is skipped. Error-kind
matching (spec 229 US3) is unconditional in the shipped harness — neither
mode nor ``IVG_TCK_LENIENT=1`` loosens it — so this mode leaves it alone.

Prints the score for ``--mode`` and every scenario whose recomputed verdict
disagrees with the one the capture recorded, grouped by an "expected type ->
actual type" tag for a scalar mismatch (``column_mismatch``/``row_count_mismatch``
for a table-shape mismatch, ``side_effects``/``error``/``result`` for those
channels).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional

from tests.tck import capture as tck_capture
from tests.tck.side_effects import compare_side_effects
from tests.tck.steps.comparison import (
    ExpNode,
    ExpPath,
    ExpRel,
    _Matcher,
    _perfect_matching,
    _without_isolation_labels,
    parse_expected,
)
from tests.tck.steps.errors import ObservedError, mismatch as error_mismatch

MODES = ("default", "typed", "lenient")


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_records(capture_dir: str) -> list[dict]:
    """Every scenario record under ``capture_dir``, one per ``.jsonl`` line."""
    out: list[dict] = []
    for path in sorted(glob.glob(os.path.join(capture_dir, "*.jsonl"))):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
    return out


# ---------------------------------------------------------------------------
# Replay matching: comparison._Matcher.match() is the source of truth for a
# scalar leaf (that is the part that varies with `mode`); node/rel/path/list/
# map structure is compared directly against the tagged capture, since it is
# already fully resolved (labels, type, props) at capture time.
# ---------------------------------------------------------------------------


def _ev_type_name(e: Any) -> str:
    if isinstance(e, bool):
        return "bool"
    if isinstance(e, int):
        return "int"
    if isinstance(e, float):
        return "float"
    if isinstance(e, str):
        return "str"
    if e is None:
        return "null"
    if isinstance(e, (ExpNode, ExpRel, ExpPath)):
        return type(e).__name__
    return type(e).__name__


def _decoded_blob_props(tagged_list: Optional[dict]) -> dict:
    """A decoded ``_props`` list (``[{"key":.., "value":..}, ...]``, tagged by
    the generic recursive ``tag()``) as a ``{key: tagged_value}`` dict -- the
    shape ``comparison._Matcher._blob_props`` reads from the wire directly."""
    out: dict = {}
    for item in (tagged_list or {}).get("v", []) or []:
        if item.get("t") != "map":
            continue
        kv = item.get("v") or {}
        key_tag = kv.get("key")
        if key_tag is None:
            continue
        out[tck_capture.untag_scalar(key_tag)] = kv.get("value") or {"t": "null", "v": None}
    return out


def _node_tag_from_decoded_map(decoded_map: dict) -> Optional[dict]:
    """A node value that arrived nested (not a top-level RETURN column, so
    ``capture.py`` decoded it as a generic map rather than the special
    ``node`` tag), reinterpreted from its raw ``_labels``/``_props`` shape."""
    v = decoded_map.get("v") or {}
    if "_labels" not in v:
        return None
    labels_tag = v.get("_labels") or {"t": "list", "v": []}
    labels = [
        lb for lb in (tck_capture.untag_scalar(x) for x in labels_tag.get("v", []))
        if not (isinstance(lb, str) and lb.startswith("TCK_"))
    ]
    return {"t": "node", "labels": labels, "props": _decoded_blob_props(v.get("_props"))}


def _rel_tag_from_decoded_map(decoded_map: dict) -> Optional[dict]:
    """A relationship value returned as one JSON column (``{"type":..,
    "props":..}``) but nested, so it decoded as a generic map."""
    v = decoded_map.get("v") or {}
    if "type" not in v or "_labels" in v:
        return None
    props_tag = v.get("props") or {"t": "map", "v": {}}
    return {"t": "rel", "type": tck_capture.untag_scalar(v.get("type")), "props": props_tag.get("v") or {}}


def _unwrap_jsontext(ev: Any, tagged: dict) -> dict:
    """A ``{"t": "jsontext", ...}`` capture tag resolved against ``ev``.

    IVG returns list/map results and properties as JSON text; capture keeps
    both the raw text and the decoded structure (``tag_maybe_json``) because
    it does not know ``ev`` yet. This is ``comparison._json_or(av, type(ev))``
    done at rescore time instead of capture time: decoded when ``ev`` needs
    that shape, else the raw text, so a plain string comparison still sees
    the original spelling.

    A node or relationship value nested inside a list/map (not a top-level
    RETURN column) decodes as a generic map; when ``ev`` is an ``ExpNode``/
    ``ExpRel`` this reinterprets that map from its raw shape instead of
    falling back to comparing it as text.

    Backward-compatible with a capture written before ``tag_maybe_json``
    detected a node/relationship/path shape *nested* inside a list/map
    element (e.g. one element of ``collect(n)``, or of a JSON-decoded list of
    relationships): such an element is a plain ``str`` tag holding node/rel
    JSON text, or a plain ``map`` tag with no shape detection at that depth --
    neither a ``node``/``rel``/``path`` tag nor even a ``jsontext`` one.
    Reinterpret it from its raw shape the same way ``tag_maybe_json`` would
    today, rather than falling back to comparing it as text."""
    if tagged.get("t") == "str" and isinstance(ev, (ExpNode, ExpRel, ExpPath, list, dict)):
        raw = tagged.get("v")
        if isinstance(raw, str):
            retagged = tck_capture.tag_maybe_json(raw)
            if retagged.get("t") != "str":
                tagged = retagged
    if tagged.get("t") == "map":
        if isinstance(ev, ExpNode):
            node_tag = _node_tag_from_decoded_map(tagged)
            if node_tag is not None:
                return node_tag
        if isinstance(ev, ExpRel):
            rel_tag = _rel_tag_from_decoded_map(tagged)
            if rel_tag is not None:
                return rel_tag
    if tagged.get("t") != "jsontext":
        return tagged
    decoded = tagged.get("decoded") or {}
    if isinstance(ev, list) and decoded.get("t") == "list":
        return decoded
    if isinstance(ev, dict) and decoded.get("t") == "map":
        return decoded
    if isinstance(ev, ExpNode) and decoded.get("t") == "map":
        node_tag = _node_tag_from_decoded_map(decoded)
        if node_tag is not None:
            return node_tag
    if isinstance(ev, ExpRel) and decoded.get("t") == "map":
        rel_tag = _rel_tag_from_decoded_map(decoded)
        if rel_tag is not None:
            return rel_tag
    return {"t": "str", "v": tagged.get("raw")}


def replay_scalar(ev: Any, tagged: Optional[dict], mode: str, top_level: bool) -> bool:
    av = tck_capture.untag(tagged) if tagged is not None else None
    # Only the outermost cell of a row is ever text-lenient (comparison.py's own
    # nested calls all pass text_ok=False); `typed` tightens that outermost cell too.
    text_ok = top_level and mode != "typed"
    return _Matcher().match(ev, av, text_ok=text_ok)


def replay_stored(ev: Any, tagged: Optional[dict], mode: str) -> bool:
    """A node/relationship property value, as ``comparison._Matcher.stored()``
    reads it: list/dict values compare without text coercion, everything else
    is always text-lenient -- storage rendering, not the top-level result-cell
    typing ``typed`` mode targets, so ``mode`` does not reach this leniency."""
    if tagged is None:
        tagged = {"t": "null", "v": None}
    tagged = _unwrap_jsontext(ev, tagged)
    if isinstance(ev, (list, dict)):
        return replay_match(ev, tagged, mode, top_level=False)
    av = tck_capture.untag(tagged)
    return _Matcher().match(ev, av, text_ok=True)


def replay_match(ev: Any, tagged: Optional[dict], mode: str, top_level: bool = False,
                  unordered: bool = False) -> bool:
    if tagged is None:
        tagged = {"t": "null", "v": None}
    tagged = _unwrap_jsontext(ev, tagged)
    t = tagged.get("t")
    if isinstance(ev, ExpNode):
        if t != "node":
            return False
        if set(tagged.get("labels") or []) != set(ev.labels):
            return False
        props = tagged.get("props") or {}
        if set(props) != set(ev.props):
            return False
        return all(replay_stored(ev.props[k], props[k], mode) for k in ev.props)
    if isinstance(ev, ExpRel):
        if t != "rel" or tagged.get("type") != ev.type:
            return False
        props = tagged.get("props") or {}
        if set(props) != set(ev.props):
            return False
        return all(replay_stored(ev.props[k], props[k], mode) for k in ev.props)
    if isinstance(ev, ExpPath):
        if t != "path":
            return False
        nodes, rels = tagged.get("nodes") or [], tagged.get("rels") or []
        if len(nodes) != len(ev.nodes) or len(rels) != len(ev.rels):
            return False
        for exp_node, n in zip(ev.nodes, nodes):
            if "labels" not in n:
                continue  # a bare id hop: cannot check without a hydrator
            if set(n.get("labels") or []) != set(exp_node.labels):
                return False
            props = n.get("props") or {}
            if set(props) != set(exp_node.props):
                return False
            if not all(replay_stored(exp_node.props[k], props[k], mode) for k in exp_node.props):
                return False
        for (exp_rel, _fwd), r in zip(ev.rels, rels):
            if (r or {}).get("type") != exp_rel.type:
                return False
        return True
    if isinstance(ev, list):
        if t != "list":
            return False
        al = tagged.get("v") or []
        # The harness tags every scenario node with a TCK_xxxxxxxx label, which
        # labels() then returns; comparison.py drops it unless `ev` holds it too.
        if any(a.get("t") == "str" for a in al):
            raw_al = [tck_capture.untag(a) for a in al]
            kept = _without_isolation_labels(raw_al, ev)
            if len(kept) != len(raw_al):
                al = [tck_capture.tag(x) for x in kept]
        if len(al) != len(ev):
            return False
        if unordered:
            return _perfect_matching(len(ev), len(al), lambda i, j: replay_match(ev[i], al[j], mode))
        return all(replay_match(e, a, mode) for e, a in zip(ev, al))
    if isinstance(ev, dict):
        if t != "map":
            return False
        ad = tagged.get("v") or {}
        if set(ad) != set(ev):
            return False
        return all(replay_match(ev[k], ad[k], mode) for k in ev)
    return replay_scalar(ev, tagged, mode, top_level)


def _mismatch_tag(exp_row: list, act_row: list, mode: str) -> str:
    for e, a in zip(exp_row, act_row):
        if not replay_match(e, a, mode, top_level=True):
            if a is None:
                actual_t = "missing"
            else:
                actual_t = _unwrap_jsontext(e, a).get("t", "missing")
            return f"{_ev_type_name(e)}->{actual_t}"
    return "row_shape"


# ---------------------------------------------------------------------------
# Scoring one scenario record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Verdict:
    passed: bool
    tag: Optional[str] = None  # only meaningful when passed is False


def _table_ok(rec: dict, exp: dict, mode: str) -> Verdict:
    got_cols = rec.get("actual_columns") or []
    if got_cols != exp.get("columns"):
        return Verdict(False, "column_mismatch")
    expected_rows = [[parse_expected(cell) for cell in row] for row in exp.get("rows") or []]
    actual_rows = rec.get("actual_rows") or []
    if len(expected_rows) != len(actual_rows):
        return Verdict(False, "row_count_mismatch")
    unordered_list = bool(exp.get("list_unordered"))

    def row_eq(i: int, j: int) -> bool:
        return all(
            replay_match(e, a, mode, top_level=True, unordered=unordered_list)
            for e, a in zip(expected_rows[i], actual_rows[j])
        )

    if exp.get("ordered"):
        for i in range(len(expected_rows)):
            if not row_eq(i, i):
                return Verdict(False, _mismatch_tag(expected_rows[i], actual_rows[i], mode))
        return Verdict(True)

    if _perfect_matching(len(expected_rows), len(actual_rows), row_eq):
        return Verdict(True)
    for i in range(len(expected_rows)):
        if not any(row_eq(i, j) for j in range(len(actual_rows))):
            j = min(i, len(actual_rows) - 1) if actual_rows else 0
            tag = _mismatch_tag(expected_rows[i], actual_rows[j], mode) if actual_rows else "row_count_mismatch"
            return Verdict(False, tag)
    return Verdict(False, "multiplicity")


def _result_ok(rec: dict, mode: str) -> Verdict:
    exp = rec.get("expected_result")
    if exp is None:
        return Verdict(True)
    error = rec.get("error")
    if error is not None:
        if mode == "lenient" and exp.get("empty"):
            return Verdict(True)
        return Verdict(False, "error")
    if exp.get("empty"):
        rows = rec.get("actual_rows") or []
        return Verdict(True) if len(rows) == 0 else Verdict(False, "row_count_mismatch")
    return _table_ok(rec, exp, mode)


def _side_effects_ok(rec: dict, mode: str) -> Verdict:
    expected = rec.get("expected_side_effects")
    if expected is None:
        return Verdict(True)
    if mode == "lenient":
        return Verdict(True)
    if rec.get("error") is not None:
        return Verdict(False, "error")
    diff = compare_side_effects(
        rec.get("side_effects"), expected, unexpected=rec.get("side_effects_unexpected")
    )
    return Verdict(True) if diff is None else Verdict(False, "side_effects")


def _error_ok(rec: dict) -> Verdict:
    exp = rec.get("expected_error")
    if exp is None:
        return Verdict(True)
    err = rec.get("error")
    if err is None:
        return Verdict(False, "error_not_raised")
    obs = ObservedError(
        kind=err.get("kind"), phase=err.get("phase"), detail=err.get("detail"),
        sqlcode=None, source=err.get("class") or "", message=err.get("message") or "",
    )
    why = error_mismatch(exp["kind"], exp["phase"], exp["detail"], obs)
    return Verdict(True) if why is None else Verdict(False, "wrong_error_kind")


def score_scenario(rec: dict, mode: str) -> Verdict:
    """The scenario's verdict recomputed under ``mode``, and (when failing) a
    tag naming which channel/mismatch shape caused it."""
    for check in (_result_ok, lambda r, m: _side_effects_ok(r, m), lambda r, m: _error_ok(r)):
        v = check(rec, mode)
        if not v.passed:
            return v
    return Verdict(True)


def is_untested(rec: dict) -> bool:
    """No ``When``/``Then`` step ever ran for this scenario -- e.g. the one
    upstream-``@ignore``d scenario (``Graph5 [2]``), which ``environment.py``
    skips before any step, so nothing was captured. ``summarize.py`` excludes
    such scenarios from both the numerator and denominator; rescoring them
    would compare two vacuous verdicts and call it a coincidence."""
    return (
        rec.get("query") is None
        and rec.get("expected_result") is None
        and rec.get("expected_side_effects") is None
        and rec.get("expected_error") is None
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@dataclass
class Report:
    mode: str
    total: int
    passed: int
    excluded: list[str] = field(default_factory=list)
    mismatches: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))

    @property
    def eligible(self) -> int:
        return self.total


def rescore(records: list[dict], mode: str) -> Report:
    eligible = [r for r in records if not is_untested(r)]
    excluded = [r.get("scenario", "?") for r in records if is_untested(r)]
    report = Report(mode=mode, total=len(eligible), passed=0, excluded=excluded)
    for rec in eligible:
        v = score_scenario(rec, mode)
        if v.passed:
            report.passed += 1
        recorded = rec.get("verdict")
        if bool(v.passed) != bool(recorded):
            tag = v.tag or "unknown"
            report.mismatches[tag].append(rec.get("scenario", "?"))
    return report


def _print_report(report: Report) -> None:
    print(f"{report.mode}: {report.passed} / {report.total}")
    if report.excluded:
        print(f"excluded (no step ran -- e.g. upstream @ignore): {len(report.excluded)}")
        for s in report.excluded:
            print(f"    {s}")
    n = sum(len(v) for v in report.mismatches.values())
    print(f"differ from recorded verdict: {n}")
    for tag in sorted(report.mismatches):
        scenarios = report.mismatches[tag]
        print(f"  {tag}: {len(scenarios)}")
        for s in scenarios:
            print(f"    {s}")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="rescore.py", description=__doc__)
    ap.add_argument("capture_dir")
    ap.add_argument("--mode", choices=MODES, default="default")
    ns = ap.parse_args(sys.argv[1:] if argv is None else argv)
    records = load_records(ns.capture_dir)
    if not records:
        print(f"no capture records under {ns.capture_dir}", file=sys.stderr)
        return 1
    _print_report(rescore(records, ns.mode))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
