"""4.6.0 R307: the documented answer for every (store, writer, value, reader) cell of the value matrix.

Each answer comes from the documentation (docs/USER_GUIDE.md, docs/setup/DATA_FORMATS.md, docs/KNOWN_ISSUES.md,
CHANGELOG.md 4.6.0), cited by a phrase that tests/unit/test_460_r307_value_spec.py finds in that file; where the
documentation says nothing, the answer is openCypher's, and the cell's source says so (OPENCYPHER). Nothing here is
read from IVG's output.

The rules, in short (the citations below hold the exact words):
- A node property written by the Python API (`create_node`, `bulk_create_nodes`, an NDJSON node line, which
  `import_graph_ndjson` writes with `create_node`) is the text `prop_text` makes. A number reads back as that text, a
  string (`'0.95'`), from `RETURN n.p`, `get_node()` and a whole node; `properties(n)` reads a canonical number text
  (`'7'`, `'0.95'`, `'1.0'`) as the number, and so it reads the string `"0.95"` as 0.95 too.
- A node property written by Cypher (CREATE, SET, MERGE, literal or parameter) reads back with its type.
- A relationship property comes back with its type. A canonical number string (`"7"`, `"0.95"`) reads as the number on
  every relationship read; `".95"` and `'007'` stay strings. `SET r.p = 1.0` stores the JSON number 1.
- `toString()` answers a stored string as it is and a stored number in openCypher's text (Java's `Double.toString`).
- Arithmetic reads a string that spells a number as the number; any other string concatenates with `+`.
- `ORDER BY` treats a node's number text as the number, and a node's well-formed ISO 8601 duration text as a
  duration; durations sort by length (openCypher CIP2015-08-06-date-time; the TCK has no scenario).
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

from tests.oracle.values import VALUES, Value

# ── citations ──────────────────────────────────────────────────────────────────────────────────────────────────────
#: name -> (file, phrase). The phrase is matched with runs of whitespace collapsed (the docs wrap at 120 columns).
CITATIONS = {
    "node_api_number_text": (
        "docs/KNOWN_ISSUES.md",
        "`RETURN n.score` and `get_node()` read the number 0.95 as the string `'0.95'` when `create_node`, "
        "`bulk_create_nodes` or `store_node` wrote it, and as the float 0.95 when a Cypher `CREATE` or `SET` wrote it",
    ),
    "whole_node_as_prop": (
        "docs/KNOWN_ISSUES.md",
        "A whole node (`RETURN n`, `collect(n)`, a node in a map, a variable-length path's target) carries each "
        "property as `RETURN n.score` reads it",
    ),
    "props_fn_number_text": (
        "docs/KNOWN_ISSUES.md",
        "`properties(n)` reads both as 0.95, and reads the string `\"0.95\"` as 0.95 too.",
    ),
    "node_cypher_typed": (
        "docs/USER_GUIDE.md",
        "a Cypher `CREATE` or `SET` stores a number as a number",
    ),
    "whole_float_point": (
        "CHANGELOG.md",
        "A native read of a stored whole float keeps its point",
    ),
    "rel_typed": ("docs/USER_GUIDE.md", "A relationship property comes back with its type"),
    "rel_string_number": (
        "docs/KNOWN_ISSUES.md",
        "`RETURN r.score`, `properties(r)` and a returned `r` read the string `\"0.95\"` as the number 0.95. The "
        "string `\".95\"` stays a string.",
    ),
    "rel_set_whole_float": (
        "docs/KNOWN_ISSUES.md",
        "`SET r.score = 1.0` on a relationship stores the JSON number `1`, so `toString(r.score)` answers `'1'`",
    ),
    "rel_set_007": ("CHANGELOG.md", "`SET r.p = '007'` on a relationship stores the string `'007'`."),
    "rel_list": ("CHANGELOG.md", "A list-valued relationship property is stored as a JSON list."),
    "rel_empty_string": ("CHANGELOG.md", "An empty-string relationship property reads as the empty string"),
    "varlen_rel_typed": (
        "CHANGELOG.md",
        "Variable-length relationship lists read stored numbers and booleans with their type.",
    ),
    "list_types": ("CHANGELOG.md", "A list's elements keep their types."),
    "string_spells_boolean": (
        "docs/KNOWN_ISSUES.md",
        "a property holding the string `'true'` or `'false'` reads back as the boolean `True` or `False`, and one "
        "holding the one-character string `\"\\u0000\"` reads back as `''`",
    ),
    "rel_cypher_float_text": (
        "docs/KNOWN_ISSUES.md",
        "so one Python spells with an exponent (`1e-07`, `1e+20`) reads back as that string, and so does NaN or an "
        "infinity written by any Cypher write (`'nan'`, `'inf'`).",
    ),
    "order_spelled": (
        "docs/KNOWN_ISSUES.md",
        "`ORDER BY` sorts a string that spells a number as that number, and a string that is an ISO 8601 duration "
        "(`'P1D'`) as a duration, whichever writer stored it, on nodes and relationships",
    ),
    "rel_expression_refused": (
        "docs/KNOWN_ISSUES.md",
        "So does a list holding such a value (`{l: [a.name, 1]}`) or an expression (`{w: 1 + 2}`). A literal (a list or "
        "a map too), a parameter and a `WITH`'s column are stored.",
    ),
    "weight_not_finite": ("CHANGELOG.md", "An omitted weight still stores nothing and costs 1.0; a non-finite one raises `ValueError`."),
    "tostring": (
        "docs/KNOWN_ISSUES.md",
        "`toString(x.score)` answers a stored string as it is, and a stored number in openCypher's text "
        "(`'0.95'`, `'1.0'`, `'7'`) from 4.6.0.",
    ),
    "arith_number_text": (
        "docs/KNOWN_ISSUES.md",
        "Arithmetic reads a string that spells a number as the number",
    ),
    "arith_concat": (
        "docs/KNOWN_ISSUES.md",
        "Any other string concatenates with `+`",
    ),
    "filter_number_text": (
        "docs/KNOWN_ISSUES.md",
        "a number filter also matches a string that spells the number",
    ),
    "order_number_text": (
        "docs/setup/DATA_FORMATS.md",
        "Filters, `ORDER BY` and arithmetic treat the string as the number",
    ),
    "order_duration_text": (
        "CHANGELOG.md",
        "Only a well-formed ISO 8601 duration (`P1D`, `PT1H30M`) sorts as one now",
    ),
}
#: The answer where the documentation says nothing: openCypher's (Cypher 9 and the TCK).
OPENCYPHER = "openCypher (docs silent)"


@dataclass(frozen=True)
class Expect:
    """One cell's documented answer: `value` with its Python type, and where the answer comes from."""

    value: Any
    source: str


# ── stores, writers, readers ───────────────────────────────────────────────────────────────────────────────────────
#: writer -> the class whose stored form it shares
NODE_WRITERS = {
    "api_create": "api",       # create_node(properties=...)
    "api_bulk": "api",         # bulk_create_nodes([...])
    "ndjson": "api",           # import_graph_ndjson, a node line (create_node after a JSON round trip)
    "cy_create": "cypher",     # CREATE (:L {p: <literal>})
    "cy_create_p": "cypher",   # CREATE (:L {p: $v})
    "cy_set": "cypher",        # SET n.p = <literal>
    "cy_set_p": "cypher",      # SET n.p = $v
    "cy_merge": "cypher",      # MERGE (:L {id: ..., p: <literal>})
}
REL_WRITERS = {
    "api_edge": "api",           # create_edge(qualifiers=...)
    "ndjson_edge": "api",        # import_graph_ndjson, an edge line (create_edge after a JSON round trip)
    "cy_create": "cy_create",    # MATCH (a), (b) CREATE (a)-[:R {p: <literal>}]->(b)
    "cy_create_p": "cy_create",  # ... {p: $v}
    "cy_merge": "cy_create",     # MATCH (a), (b) MERGE (a)-[:R {p: <literal>}]->(b)
    "cy_set": "cy_set",          # SET r.p = <literal>
    "cy_set_p": "cy_set_p",      # SET r.p = $v
}
WRITERS = {"node": NODE_WRITERS, "rel": REL_WRITERS}
LITERAL_WRITERS = {"cy_create", "cy_set", "cy_merge"}
PARAM_WRITERS = {"cy_create_p", "cy_set_p"}

#: reader -> (stores it applies to, value groups it applies to, or None for every group)
_SCALAR = {"int", "float", "numstr", "pdurstr", "pstr", "str", "spelled", "dur", "date", "bool"}
READERS = {
    "get_node": ({"node"}, None),          # engine.get_node(id)["p"]
    "ret": ({"node", "rel"}, None),        # RETURN x.p
    "with": ({"node", "rel"}, None),       # WITH x.p AS v RETURN v
    "collect": ({"node", "rel"}, None),    # RETURN collect(x.p)[0], grouped by writer and value
    "distinct": ({"node", "rel"}, None),   # RETURN DISTINCT x.k, x.w, x.p
    "minmax": ({"node", "rel"}, _SCALAR - {"dur", "date"}),  # min(x.p), max(x.p) per writer and value
    "mapproj": ({"node", "rel"}, None),    # RETURN x {.p}
    "whole": ({"node", "rel"}, None),      # RETURN x
    "varlen": ({"node", "rel"}, None),     # a *1..1 read: the target node's n.p, or the relationship list's props
    "varlen_comp": ({"rel"}, None),        # [x IN rs | x.p][0] over a *1..1 relationship list
    "props": ({"node", "rel"}, None),      # RETURN properties(x).p  (properties(x) reads as its own family)
    "tostring": ({"node", "rel"}, _SCALAR),
    # a string spelling a boolean reads as the boolean, and true + 1 is a TypeError that fails the whole query
    "arith": ({"node", "rel"}, {"int", "float", "numstr", "pdurstr", "pstr", "str"}),
    "any_beside": ({"node", "rel"}, None),  # RETURN x.k, any(e IN [x.p] WHERE e IS NOT NULL)
    "list_fns": ({"node", "rel"}, {"list"}),
    "size_str": ({"node", "rel"}, {"str", "pstr", "pdurstr"}),
    "eq_param": ({"node", "rel"}, None),    # WHERE x.p = $v
    "eq_lit": ({"node", "rel"}, None),      # WHERE x.p = <literal>
    "ge_param": ({"node", "rel"}, {"int", "float", "numstr", "pdurstr", "pstr", "str", "spelled", "bool"}),
    "gt_param": ({"node", "rel"}, {"int", "float", "numstr", "pdurstr", "pstr", "str", "spelled", "bool"}),
}
#: ORDER BY groups: name -> value groups sorted together
ORDER_GROUPS = {
    "num": {"int", "float"},
    "numstr": {"numstr"},
    "pdurstr": {"pdurstr"},
    "str": {"str", "pstr"},
    "dur": {"dur"},
    "date": {"date"},
    "bool": {"bool"},
    "list": {"list"},
}


def same(got: Any, want: Any) -> bool:
    """Equal with the same Python type all the way down (1, 1.0 and True are three answers; NaN equals NaN)."""
    if type(got) is not type(want):
        return False
    if isinstance(want, float):
        return (math.isnan(got) and math.isnan(want)) or got == want
    if isinstance(want, list):
        return len(got) == len(want) and all(same(g, w) for g, w in zip(got, want))
    if isinstance(want, dict):
        return got.keys() == want.keys() and all(same(got[k], want[k]) for k in want)
    return got == want


def writes(store: str, writer: str, v: Value) -> bool:
    """Whether `writer` can write `v` at all: a literal writer needs a Cypher literal, every other one a Python value."""
    if writer in LITERAL_WRITERS:
        return v.literal_writable
    return v.api_writable


#: A Cypher property holds a scalar or a list of scalars: a map literal, or a list literal holding a list or a map, is
#: a TypeError (TCK Set1 [10], InvalidPropertyType; IVG's _check_property_value_type for SET). A map or nested list
#: passed as a parameter is stored as JSON (translator.py's existing contract for `SET n.p = $map`).
NOT_A_PROPERTY = {"m1", "mnest", "lnest"}


def write_error(store: str, writer: str, v: Value) -> Optional[Expect]:
    """The exception a write raises, by its class name, or None when the write succeeds."""
    rel_between_matched = store == "rel" and writer in ("cy_create", "cy_merge")
    if writer in LITERAL_WRITERS and v.key in NOT_A_PROPERTY:
        if rel_between_matched:
            # KNOWN_ISSUES: between matched nodes "A literal (a list or a map too) ... are stored"
            return None
        return Expect("TypeError", OPENCYPHER)
    if rel_between_matched and v.temporal:
        # duration('P1D') is an expression, which this writer refuses (KNOWN_ISSUES)
        return Expect("GraphWriteError", "rel_expression_refused")
    if store == "rel" and writer_class(store, writer) == "api" and isinstance(v.py, float) and not math.isfinite(v.py):
        # create_edge's qualifiers are JSON, which has no NaN or infinity (json.dumps wrote the invalid `NaN`, and one
        # such row failed every relationship read in the namespace); refused as create_edge refuses such a weight
        return Expect("ValueError", "weight_not_finite")
    return None


def applies(store: str, reader: str, v: Value) -> bool:
    stores, groups = READERS[reader]
    if store not in stores or (groups is not None and v.group not in groups):
        return False
    if reader == "eq_lit":
        return v.literal_writable
    if reader in ("eq_param", "ge_param", "gt_param"):
        return v.api_writable
    return True


# ── value helpers ──────────────────────────────────────────────────────────────────────────────────────────────────
_CANONICAL_INT = re.compile(r"^-?(0|[1-9]\d*)$")
_CANONICAL_FLOAT = re.compile(r"^-?(0|[1-9]\d*)\.\d+$")
_SPELLS_NUMBER = re.compile(r"^[-+]?(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?$")
_INTEGER_TEXT = re.compile(r"^[-+]?\d+$")


def prop_text(value: Any) -> str:
    """The text the Python API stores for a node property (docs/iris-sql-quirks.md §7: `true`/`false` for booleans,
    JSON for lists and maps, else `str()`). Written out here, not imported, so the spec does not move with the code."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)


def canonical_number(text: str) -> Any:
    """A canonical number text as the number (`'7'` -> 7, `'0.95'` -> 0.95); any other text unchanged."""
    if _CANONICAL_INT.match(text):
        return int(text)
    if _CANONICAL_FLOAT.match(text):
        return float(text)
    return text


_JSON_NUMBER = re.compile(r"^-?(0|[1-9]\d*)(\.\d+)?([eE][-+]?\d+)?$")


def json_number(text: str) -> Any:
    """Text JSON spells a number as that number (`'1.0'` -> 1.0, `'1e3'` -> 1000.0); any other text unchanged. How
    properties(n) reads a node's number text (CY_PROPS_MAP, 4.6.0 R121)."""
    return json.loads(text) if _JSON_NUMBER.match(text) else text


def spelled_number(text: str) -> Optional[Any]:
    """The number a string spells (`'007'` -> 7, `'1e3'` -> 1000.0, `'.95'` -> 0.95), or None."""
    if not _SPELLS_NUMBER.match(text):
        return None
    return int(text) if _INTEGER_TEXT.match(text) else float(text)


def java_double(x: float) -> str:
    """openCypher's text for a float: Java's `Double.toString` (shortest digits; plain between 1e-3 and 1e7)."""
    if math.isnan(x):
        return "NaN"
    if math.isinf(x):
        return "Infinity" if x > 0 else "-Infinity"
    if x == 0:
        return "-0.0" if math.copysign(1.0, x) < 0 else "0.0"
    sign = "-" if x < 0 else ""
    _, digits, exp = Decimal(repr(abs(x))).as_tuple()
    ds = "".join(map(str, digits)).rstrip("0") or "0"
    exp += len("".join(map(str, digits))) - len(ds)
    sci = len(ds) + exp - 1  # x = d.ddd * 10^sci
    if 1e-3 <= abs(x) < 1e7:
        if sci >= 0:
            whole, frac = ds[: sci + 1].ljust(sci + 1, "0"), ds[sci + 1:]
        else:
            whole, frac = "0", "0" * (-sci - 1) + ds
        return f"{sign}{whole}.{frac or '0'}"
    return f"{sign}{ds[0]}.{ds[1:] or '0'}E{sci}"


def cypher_text(value: Any) -> str:
    """openCypher's toString() of a scalar."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return java_double(value)
    return value


_DUR = re.compile(r"^P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$")
#: the CIP's month: 30.436875 days (365.2425 / 12), in seconds
_AVG_MONTH_S = 2_629_746


def duration_seconds(iso: str) -> float:
    """A duration's length in seconds: the order openCypher's ORDER BY gives durations. CIP2015-08-06-date-time
    (vendor/opencypher/cip/2.testable, "Ordering"): "normalising all components as if all years were 365.2425 days
    long, all months were 30.436875 (1/12 year) days long, and all days were 24 hours long". The TCK has no duration
    ORDER BY scenario."""
    m = _DUR.match(iso)
    if not m or iso in ("P", "PT"):
        raise ValueError(f"not an ISO 8601 duration: {iso!r}")
    y, mo, w, d, h, mi, s = (float(g) if g else 0.0 for g in m.groups())
    return (y * 12 + mo) * _AVG_MONTH_S + (w * 7 + d) * 86400 + h * 3600 + mi * 60 + s


#: openCypher orderability, ascending (Cypher 9, "Ordering and comparison"): map < node < relationship < list < path
#: < datetime < localdatetime < date < time < localtime < duration < string < boolean < number < null; NaN after every
#: other number.
_RANK = {"map": 0, "list": 3, "datetime": 5, "localdatetime": 6, "date": 7, "duration": 10, "string": 11,
         "boolean": 12, "number": 13}


def order_key(value: Any, kind: str = "") -> tuple:
    """openCypher's ascending sort key of a value; `kind` names a temporal held as its ISO text."""
    if kind == "duration":
        return (_RANK["duration"], duration_seconds(value))
    if kind in ("date", "datetime", "localdatetime"):
        return (_RANK[kind], value)
    if isinstance(value, bool):
        return (_RANK["boolean"], value)
    if isinstance(value, (int, float)):
        return (_RANK["number"], 1, 0) if math.isnan(value) else (_RANK["number"], 0, value)
    if isinstance(value, str):
        return (_RANK["string"], value)
    if isinstance(value, list):
        return (_RANK["list"], tuple(order_key(e) for e in value))
    if isinstance(value, dict):
        return (_RANK["map"], json.dumps(value, sort_keys=True))
    raise TypeError(value)


# ── the documented reads ───────────────────────────────────────────────────────────────────────────────────────────
def writer_class(store: str, writer: str) -> str:
    return WRITERS[store][writer]


def read(store: str, writer: str, v: Value, family: str = "prop") -> Expect:
    """What a property read answers. `family` "prop" is `RETURN x.p` and every read that carries a value as it does;
    "props" is `properties(x)`."""
    cls = writer_class(store, writer)
    if v.key in SPELLED_BOOLEAN:
        return Expect(SPELLED_BOOLEAN[v.key], "string_spells_boolean")
    if store == "node":
        return _read_node(cls, v, family)
    return _read_rel(cls, writer, v)


#: strings the stored form cannot tell from another value (KNOWN_ISSUES): what they read back as
SPELLED_BOOLEAN = {"strue": True, "sfalse": False, "snul": ""}


def _read_node(cls: str, v: Value, family: str) -> Expect:
    if v.temporal:
        return Expect(v.oc, OPENCYPHER)
    if v.group in ("int", "float"):
        if cls == "api":
            text = prop_text(v.py)
            if family == "props":
                return Expect(json_number(text), "props_fn_number_text")
            return Expect(text, "node_api_number_text")
        if v.group == "float" and math.isfinite(v.oc) and v.oc.is_integer():
            return Expect(v.oc, "whole_float_point")
        return Expect(v.oc, "node_cypher_typed")
    if family == "props" and isinstance(v.oc, str):
        number = json_number(v.oc)
        if number is not v.oc:
            return Expect(number, "props_fn_number_text")
    return Expect(v.oc, OPENCYPHER)


def _read_rel(cls: str, writer: str, v: Value) -> Expect:
    if v.temporal:
        return Expect(v.oc, OPENCYPHER)
    if isinstance(v.oc, str):
        if writer.startswith("cy_set") and v.key == "s007":
            return Expect(v.oc, "rel_set_007")
        number = canonical_number(v.oc)
        if number is not v.oc:
            return Expect(number, "rel_string_number")
        if v.oc == "":
            return Expect("", "rel_empty_string")
        return Expect(v.oc, OPENCYPHER)
    if isinstance(v.oc, float) and cls == "cy_create":
        # a Cypher CREATE or MERGE stores the JSON string of Python's spelling: canonical text reads as the number
        text = str(v.py if v.py is not None else v.oc)
        number = canonical_number(text)
        return Expect(text, "rel_cypher_float_text") if number is text else Expect(number, "rel_typed")
    if isinstance(v.oc, float) and cls in ("cy_set", "cy_set_p") and not math.isfinite(v.oc):
        return Expect(str(v.oc), "rel_cypher_float_text")
    if cls in ("cy_set", "cy_set_p") and isinstance(v.oc, float) and math.isfinite(v.oc) and v.oc == int(v.oc):
        return Expect(int(v.oc), "rel_set_whole_float")
    if isinstance(v.oc, list):
        return Expect(v.oc, "rel_list")
    if isinstance(v.oc, float) and not math.isfinite(v.oc):
        return Expect(v.oc, OPENCYPHER)
    return Expect(v.oc, "rel_typed")


def expect(store: str, writer: str, v: Value, reader: str) -> Expect:
    """The documented answer of one cell. Not defined for ORDER BY (see `order`) or where `applies` is false."""
    base = read(store, writer, v)
    if reader in ("get_node", "ret", "with", "collect", "distinct", "minmax", "mapproj"):
        return base
    if reader in ("whole", "varlen"):
        if store == "node":
            return Expect(base.value, "whole_node_as_prop" if base.source != OPENCYPHER else OPENCYPHER)
        return Expect(base.value, "varlen_rel_typed" if base.source == "rel_typed" else base.source)
    if reader == "varlen_comp":
        return base
    if reader == "props":
        return read(store, writer, v, "props")
    if reader == "tostring":
        return _tostring(store, writer, v, base)
    if reader == "arith":
        return _arith(store, writer, v, base)
    if reader == "any_beside":
        return Expect(True, OPENCYPHER)
    if reader == "list_fns":
        lst = base.value
        return Expect({"size": len(lst), "head": lst[0] if lst else None, "last": lst[-1] if lst else None,
                       "first": lst[0] if lst else None, "any": bool(lst), "all": True}, "list_types")
    if reader == "size_str":
        return Expect(len(v.oc), OPENCYPHER)
    if reader in ("eq_param", "eq_lit"):
        return Expect(not v.is_nan, OPENCYPHER if base.source == OPENCYPHER else "filter_number_text")
    if reader == "ge_param":
        return Expect(not v.is_nan, OPENCYPHER)
    if reader == "gt_param":
        return Expect(False, OPENCYPHER)
    raise KeyError(reader)


def _tostring(store, writer, v, base) -> Expect:
    if v.temporal:
        return Expect(v.oc, OPENCYPHER)
    if isinstance(v.oc, str):
        # every string as it is, also a relationship's canonical number string that reads as the number; '\x00'
        # reads as '' (KNOWN_ISSUES)
        return Expect("" if v.key == "snul" else v.oc, "tostring")
    if isinstance(base.value, str):
        # the API's number text on a node is a stored string
        return Expect(base.value, "tostring")
    return Expect(cypher_text(base.value), "tostring")


def _arith(store, writer, v, base) -> Expect:
    val = base.value
    if isinstance(val, str):
        number = spelled_number(val)
        if number is not None:
            return Expect(number + 1, "arith_number_text")
        return Expect(val + "1", "arith_concat")
    if isinstance(val, bool):
        raise ValueError("booleans are not in the arith groups")
    return Expect(val + 1, OPENCYPHER if base.source == OPENCYPHER else "node_cypher_typed")


def order_value(store: str, writer: str, v: Value) -> tuple:
    """How ORDER BY sorts one written value: openCypher's key of the documented read, with a string that spells a number
    sorted as the number and one that is an ISO 8601 duration as a duration (KNOWN_ISSUES, DATA_FORMATS)."""
    base = read(store, writer, v).value
    if v.temporal:
        return order_key(v.oc, v.temporal)
    if isinstance(base, str):
        if base not in ("P", "PT") and _DUR.match(base):
            return order_key(base, "duration")
        number = spelled_number(base)
        if number is not None:
            return order_key(number)
    return order_key(base)


def order_source(store: str, writer: str, group: str) -> str:
    if group in ("num", "numstr", "pdurstr"):
        return "order_spelled"
    return OPENCYPHER


def order(store: str, writer: str, group: str) -> Expect:
    """The value keys of one writer's group in ascending ORDER BY x.p, x.k order."""
    members = [v for v in VALUES if v.group in ORDER_GROUPS[group] and writes(store, writer, v)
               and not write_error(store, writer, v)]
    ranked = sorted(members, key=lambda v: (order_value(store, writer, v), v.key))
    return Expect([v.key for v in ranked], order_source(store, writer, group))


def cells(store: str):
    """Every (writer, value, reader) cell of a store that has a documented answer."""
    for writer in WRITERS[store]:
        for v in VALUES:
            if not writes(store, writer, v) or write_error(store, writer, v):
                continue
            for reader in READERS:
                if applies(store, reader, v):
                    yield writer, v, reader
