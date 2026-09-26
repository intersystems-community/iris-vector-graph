"""Record every scenario's actual result once, so a new scoring rule (typed
result cells, a stricter error kind, ...) can be tried offline against the
recording instead of a fresh IRIS run.

``IVG_TCK_CAPTURE=<dir>`` makes every scenario append one JSON line to
``<dir>/<area>.jsonl`` (``area`` = the feature file's parent directory name,
e.g. ``create``). Off by default; reading the flag and writing the record
never raises and never changes a scenario's verdict — every entry point here
is wrapped so a capture bug degrades to "no capture", not a broken run.

The step definitions in ``results.py`` call :func:`note` with whatever they
already computed (the expected table, the measured side effects, the
classified error) at the point they compute it; ``environment.py`` calls
:func:`flush` from ``after_scenario`` to assemble the final record — the
query text (:func:`note_query`, called from ``steps/query.py``), the measured
side effects already on the context, the error already on the context, and
the scenario's own verdict (``scenario.status``) — and append it.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from tests.tck.steps.comparison import (
    TCKValue,
    _as_props,
    _collapse_columns,
    _collapsed_value,
    _json_or,
    _Matcher,
)
from tests.tck.steps.errors import classify


def capture_dir() -> Optional[str]:
    """The directory ``IVG_TCK_CAPTURE`` names, or ``None`` when unset."""
    d = os.environ.get("IVG_TCK_CAPTURE", "").strip()
    return d or None


def enabled() -> bool:
    return capture_dir() is not None


def note(context, **fields) -> None:
    """Merge ``fields`` into the scenario's pending capture record. A no-op
    unless capture is enabled; never raises."""
    if not enabled():
        return
    try:
        buf = getattr(context, "_capture_fields", None)
        if buf is None:
            buf = {}
            context._capture_fields = buf
        buf.update(fields)
    except Exception:
        pass


def note_query(context, query: str) -> None:
    """Record the most recently executed query's text (called from
    ``steps/query.py`` on every ``When executing query`` step, so the Then
    steps' capture sees the query that produced ``context.last_result``)."""
    if not enabled():
        return
    try:
        context.last_query = query
    except Exception:
        pass


# ---------------------------------------------------------------------------
# JSON-safe, type-preserving tagging: {"t": "<pytype>", "v": ...}, recursing
# into lists and maps. Round-trips through :func:`untag_scalar` for a
# rescorer that needs the original Python value back (e.g. to feed
# ``comparison._Matcher.match`` unchanged).
# ---------------------------------------------------------------------------


def tag(v: Any) -> dict:
    if isinstance(v, bool):
        return {"t": "bool", "v": v}
    if isinstance(v, int):
        return {"t": "int", "v": v}
    if isinstance(v, float):
        return {"t": "float", "v": v}
    if v is None:
        return {"t": "null", "v": None}
    if isinstance(v, Decimal):
        return {"t": "decimal", "v": str(v)}
    if isinstance(v, str):
        return {"t": "str", "v": v}
    if isinstance(v, (list, tuple)):
        return {"t": "list", "v": [tag(x) for x in v]}
    if isinstance(v, dict):
        return {"t": "map", "v": {k: tag(x) for k, x in v.items()}}
    return {"t": type(v).__name__, "v": repr(v)}


def untag_scalar(tagged: dict) -> Any:
    """The Python value ``tag()`` produced, for a non-list/map leaf."""
    t, v = tagged.get("t"), tagged.get("v")
    if t == "decimal":
        return Decimal(v)
    return v


def untag(tagged: dict) -> Any:
    """The full Python value ``tag()`` produced, recursing into list/map."""
    t = tagged.get("t")
    if t == "list":
        return [untag(x) for x in tagged.get("v", [])]
    if t == "map":
        return {k: untag(x) for k, x in tagged.get("v", {}).items()}
    return untag_scalar(tagged)


def tag_maybe_json(v: Any) -> dict:
    """``tag(v)``, recursing into JSON structure IVG uses for graph values and
    Cypher lists/maps, so a nested node/relationship/path -- one element of
    ``collect(n)``, say -- is not flattened to plain text.

    IVG returns list- and map-valued Cypher results, and list/map-valued node
    and relationship properties, as JSON text over the wire, and a
    ``collect()``ed node or path as a further-JSON-encoded string *inside*
    that list. A string that decodes to the same raw shape
    ``comparison._Matcher._node_obj``/``path``/rel-column reads is tagged
    ``node``/``path``/``rel`` directly, exactly as ``_serialize_cell`` does
    for a top-level column.

    A string that decodes to a plain list or dict, but isn't one of those
    shapes, is tagged ``{"t": "jsontext", "raw": v, "decoded": ...}``: the
    live matcher decodes such a string with
    ``comparison._json_or(av, expected_type)`` at match time, guided by the
    expected value's type, and capture does not have that type yet, so it
    keeps both the raw text (for a plain string comparison) and the decoded
    structure (for a list/map comparison) — the rescorer picks one once it
    has ``ev`` (see ``rescore.py``'s ``_unwrap_jsontext``).

    Shape detection (``_dict_shape``) runs on a dict whether it arrived as
    JSON text or already-decoded (``relationships(p)`` returns a JSON-text
    *list* whose elements are native dicts once that list is parsed, e.g.
    ``[{"type": "REL", "props": {...}}, ...]`` for `Path2`)."""
    if v is None:
        return {"t": "null", "v": None}
    if isinstance(v, dict):
        shape = _dict_shape(v)
        return shape if shape is not None else {"t": "map", "v": {k: tag_maybe_json(x) for k, x in v.items()}}
    if isinstance(v, (list, tuple)):
        return {"t": "list", "v": [tag_maybe_json(x) for x in v]}
    if isinstance(v, str):
        d = _json_or(v, dict)
        if d is not None:
            shape = _dict_shape(d)
            if shape is not None:
                return shape
            return {"t": "jsontext", "raw": v, "decoded": {"t": "map", "v": {k: tag_maybe_json(x) for k, x in d.items()}}}
        decoded_list = _json_or(v, list)
        if decoded_list is not None:
            return {"t": "jsontext", "raw": v, "decoded": {"t": "list", "v": [tag_maybe_json(x) for x in decoded_list]}}
        return {"t": "str", "v": v}
    return tag(v)


_REL_KEYS = frozenset({"type", "props", "id", "_id", "start", "end", "s", "o"})


def _dict_shape(d: dict) -> Optional[dict]:
    """A node/path/relationship tag for ``d``'s raw shape, or ``None`` when
    it is not one of those -- just a plain map."""
    if "_labels" in d:
        return {
            "t": "node",
            "labels": sorted(_Matcher._labels(d.get("_labels"))),
            "props": {k: tag_maybe_json(pv) for k, pv in _Matcher._blob_props(d.get("_props")).items()},
        }
    if "nodes" in d and "rels" in d:
        return _serialize_path(d)
    # A relationship column carries only type/props (plus id fields); a plain
    # map that merely has a "type" key (Literals7/8 [18]) has other keys too.
    if "type" in d and set(d) <= _REL_KEYS:
        return {
            "t": "rel",
            "type": d.get("type"),
            "props": {k: tag_maybe_json(pv) for k, pv in _as_props(d.get("props")).items()},
        }
    return None


# ---------------------------------------------------------------------------
# Actual result rows: node/relationship/path columns serialized with their
# labels, type and properties (each property value tagged); everything else
# tagged as-is. This is a snapshot for offline rescoring, not the live
# matcher's input — the live matcher still reads the raw DB rows directly.
# ---------------------------------------------------------------------------


def grouped_columns(actual_cols: list) -> list:
    """The engine's raw column list, collapsed the same way
    ``comparison.TCKResultTable.compare()`` does (``n_id``/``n_labels``/``n_props``
    -> ``n``), so a captured ``actual_columns`` lines up with ``expected_result``'s
    TCK column names instead of the engine's internal per-field names."""
    return [name for name, _group, _kind in _collapse_columns(list(actual_cols))]


def serialize_actual_rows(actual_rows: list, actual_cols: list, hydrator=None) -> list:
    groups = _collapse_columns(list(actual_cols))
    m = _Matcher(hydrator)
    out = []
    for row in actual_rows:
        out.append([_serialize_cell(_collapsed_value(row, group, kind), kind, m) for _, group, kind in groups])
    return out


def _serialize_cell(val: Any, kind: str, m: _Matcher) -> dict:
    if val is None:
        return {"t": "null", "v": None}
    if kind == "node":
        d = m._node_obj(val)
        if d is None:
            return tag_maybe_json(val)
        return {
            "t": "node",
            "labels": sorted(m._labels(d.get("_labels"))),
            "props": {k: tag_maybe_json(v) for k, v in m._blob_props(d.get("_props")).items()},
        }
    if kind == "rel":
        rel_type = val.get("_type")
        props: dict = {}
        if m.hydrator is not None:
            cands = m.hydrator.edges(val.get("_s"), val.get("_o_id"), rel_type)
            if cands:
                props = {k: tag_maybe_json(v) for k, v in cands[0].items()}
        return {"t": "rel", "type": rel_type, "props": props}
    # "plain": tag_maybe_json already detects the node/path/rel JSON shapes
    # `_serialize_cell` special-cases above for a grouped column (needed here
    # for a value that never went through column grouping -- e.g. one
    # element of a heterogeneous UNWIND, or of a `collect()`ed list).
    return tag_maybe_json(val)


def _serialize_path(d: dict) -> dict:
    nodes = []
    for n in d.get("nodes") or []:
        if isinstance(n, dict) and "_labels" in n:
            nodes.append({
                "labels": sorted(_Matcher._labels(n.get("_labels"))),
                "props": {k: tag_maybe_json(v) for k, v in _Matcher._blob_props(n.get("_props")).items()},
            })
        else:
            nid = n.get("_id", n.get("id")) if isinstance(n, dict) else n
            nodes.append({"id": tag(nid)})
    rels = []
    for r in d.get("rels") or []:
        rels.append({"type": r.get("type", r.get("_type")) if isinstance(r, dict) else r})
    return {"t": "path", "nodes": nodes, "rels": rels}


def serialize_expected_table(columns: list, rows: list, ordered: bool, list_unordered: bool) -> dict:
    """``rows``: ``list[list[TCKValue]]`` — the raw cell strings, not the
    parsed values, so the rescorer parses them itself (``parse_expected``)."""
    return {
        "columns": list(columns),
        "rows": [[c.raw if isinstance(c, TCKValue) else str(c) for c in row] for row in rows],
        "ordered": bool(ordered),
        "list_unordered": bool(list_unordered),
    }


# ---------------------------------------------------------------------------
# Flush: assemble the scenario's record from the context and append it.
# ---------------------------------------------------------------------------


def error_record(context) -> Optional[dict]:
    err = getattr(context, "last_error", None)
    raised: Any = err
    cls = type(err).__name__ if err is not None else None
    if raised is None:
        result = getattr(context, "last_result", None)
        result_error = getattr(result, "error", None) if result is not None else None
        if isinstance(result_error, str) and result_error:
            raised = result_error
            cls = "IVGResult.error"
    if raised is None:
        return None
    try:
        obs = classify(raised)
    except Exception as exc:  # classification itself must not break capture
        return {"class": cls, "message": str(raised), "kind": None, "phase": None,
                "detail": None, "classify_error": f"{type(exc).__name__}: {exc}"}
    return {
        "class": cls,
        "message": str(raised),
        "kind": obs.kind,
        "phase": obs.phase,
        "detail": obs.detail,
    }


def _scenario_id(scenario) -> str:
    feature_name = getattr(getattr(scenario, "feature", None), "name", None) or "?"
    return f"{feature_name}::{getattr(scenario, 'name', '?')}"


def _area(scenario) -> str:
    filename = getattr(getattr(scenario, "feature", None), "filename", None)
    if not filename:
        return "unknown"
    return Path(filename).parent.name


def _verdict(scenario) -> Optional[bool]:
    status = getattr(scenario, "status", None)
    name = getattr(status, "name", None)
    if name is None:
        return None
    return name == "passed"


def flush(context, scenario) -> None:
    """Assemble this scenario's record and append it to ``<dir>/<area>.jsonl``.
    A no-op unless capture is enabled; never raises."""
    d = capture_dir()
    if d is None:
        return
    try:
        fields = dict(getattr(context, "_capture_fields", None) or {})
        record = {
            "scenario": _scenario_id(scenario),
            "area": _area(scenario),
            "query": getattr(context, "last_query", None),
            "side_effects": getattr(context, "side_effects", None),
            "side_effects_unexpected": getattr(context, "side_effects_unexpected", None) or {},
            "error": error_record(context),
            "verdict": _verdict(scenario),
            **fields,
        }
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"{record['area']}.jsonl")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except Exception:
        pass  # capture must never affect the run
    finally:
        if hasattr(context, "_capture_fields"):
            try:
                del context._capture_fields
            except Exception:
                pass
