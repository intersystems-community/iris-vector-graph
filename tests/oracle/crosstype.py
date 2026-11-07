"""Cross-type cases with openCypher's answers, worked out without IVG (4.6.0 R308, the cmptypes family).

Three families, each over the value kinds int, float, string, boolean, null, list and map, and the read forms a value
reaches an operator by:

- `comparison`: `<`, `<=`, `>`, `>=`, `=` and `<>` of every pair of kinds (one query per pair and form, the six
  operators as six columns);
- `math`: abs, ceil, floor, round, sign, sqrt, exp, log, log10, the trigonometric functions, degrees, radians and
  haversin of every kind;
- `size`: size() of every kind.

Read forms: `literal` (`RETURN 1 < 'a'`), `with` (a WITH column), `param` (a query parameter), `subscript` (an
element of a WITH-held list), `unwound` (an UNWIND variable), `collected` (an element of a collect()ed list) and
`stored` (a node property a Cypher SET wrote; a map cannot be stored, a null is a missing property).

openCypher's rules, as `compare`, `math_fn` and `size_fn` apply them (openCypher 9, and the TCK's Comparison1-4 and Mathematical features):

- null on either side of any comparison is null;
- `=` / `<>`: values of different kinds are not equal (an integer equals a float of the same value); lists compare
  element by element, maps key by key, and a null inside makes the answer null where nothing else decides;
- an ordering (`<` ...) holds between two numbers, two strings, two booleans (false < true) or two lists (element by
  element, a shorter prefix first, null where an element pair does not order); anything else is null (a map orders
  with nothing);
- a math function of null is null, of a number a number, of anything else a TypeError;
- size() of a list or a string is its length, of null null, of anything else a TypeError.

A math result is compared as a float (`numbers are unchanged` is all this family asks of them; the type of
`ceil(1)` is not its subject).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from tests.oracle.cypher_gen import cy_eq as _cy_eq
from tests.oracle.cypher_gen import cy_text as _cy_text

import datetime as _dt
import re as _re


@dataclass(frozen=True)
class Temporal:
    """A temporal value the comparison family writes as `kind('text')` (4.6.0 fixes81)."""

    kind: str  # duration, date, time, datetime, localdatetime
    text: str

    def key(self):
        """What openCypher compares: a duration's components (months, days, seconds; it orders with nothing), a
        date's day, a zoned time's or datetime's instant, a local datetime's value."""
        if self.kind == "duration":
            m = _re.fullmatch(r"P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)W)?(?:(\d+)D)?"
                              r"(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?", self.text)
            y, mo, w, d, h, mi, sec = (float(g) if g else 0 for g in m.groups())
            return (int(y * 12 + mo), int(w * 7 + d), h * 3600 + mi * 60 + sec)
        if self.kind == "date":
            return _dt.date.fromisoformat(self.text)
        if self.kind == "time":
            t = _dt.time.fromisoformat(self.text.replace("Z", "+00:00"))
            off = t.utcoffset().total_seconds()
            return t.hour * 3600 + t.minute * 60 + t.second - off
        if self.kind == "datetime":
            return _dt.datetime.fromisoformat(self.text.replace("Z", "+00:00"))
        return _dt.datetime.fromisoformat(self.text)


def cy_text(v) -> str:
    """A Cypher literal; a temporal value as its constructor."""
    if isinstance(v, Temporal):
        return f"{v.kind}('{v.text}')"
    return _cy_text(v)


def cy_eq(a, b):
    if isinstance(a, Temporal) or isinstance(b, Temporal):
        if a is None or b is None:
            return None
        return isinstance(a, Temporal) and isinstance(b, Temporal) and a.kind == b.kind and a.key() == b.key()
    return _cy_eq(a, b)

KINDS = ("int", "float", "string", "bool", "null", "list", "map")
LEFT = {"int": 1, "float": 1.5, "string": "a", "bool": False, "null": None, "list": [1], "map": {"k": 1}}
RIGHT = {"int": 2, "float": 2.5, "string": "b", "bool": True, "null": None, "list": [1, 2], "map": {"k": 2}}
#: 4.6.0 fixes81: the comparison family's temporal kinds (comparison only: no math or size() of them here)
TEMPORAL_KINDS = ("duration", "date", "time", "datetime", "localdatetime")
LEFT.update({"duration": Temporal("duration", "P1D"), "date": Temporal("date", "2020-01-01"),
             "time": Temporal("time", "12:00:00Z"), "datetime": Temporal("datetime", "2020-01-01T00:00:00Z"),
             "localdatetime": Temporal("localdatetime", "2020-01-01T00:00:00")})
RIGHT.update({"duration": Temporal("duration", "P2D"), "date": Temporal("date", "2020-01-02"),
              "time": Temporal("time", "13:00:00Z"), "datetime": Temporal("datetime", "2020-01-02T00:00:00Z"),
              "localdatetime": Temporal("localdatetime", "2020-01-02T00:00:00")})
COMPARISON_KINDS = KINDS + TEMPORAL_KINDS
#: 4.6.1 (KNOWN_ISSUES "A date or time inside a list compares as its text"): a date, time, datetime or local datetime
#: read by position from a list (a WITH-held literal list or a collect()ed one) compares as its ISO text against a
#: string or another temporal type (and, collected, against a list or a map), where openCypher answers null for the
#: orderings. Measured on 4.6.0 fixes81 (both drivers, both graph modes); the E2E family requires exactly these to
#: still disagree, so a fix shows as a failure until this set is emptied.
KNOWN_WRONG_461 = frozenset({
    ('collected', 'date vs datetime'),
    ('collected', 'date vs list'),
    ('collected', 'date vs localdatetime'),
    ('collected', 'date vs map'),
    ('collected', 'date vs string'),
    ('collected', 'date vs time'),
    ('collected', 'datetime vs date'),
    ('collected', 'datetime vs list'),
    ('collected', 'datetime vs localdatetime'),
    ('collected', 'datetime vs map'),
    ('collected', 'datetime vs string'),
    ('collected', 'datetime vs time'),
    ('collected', 'list vs date'),
    ('collected', 'list vs datetime'),
    ('collected', 'list vs localdatetime'),
    ('collected', 'list vs time'),
    ('collected', 'localdatetime vs date'),
    ('collected', 'localdatetime vs datetime'),
    ('collected', 'localdatetime vs list'),
    ('collected', 'localdatetime vs map'),
    ('collected', 'localdatetime vs string'),
    ('collected', 'localdatetime vs time'),
    ('collected', 'map vs date'),
    ('collected', 'map vs datetime'),
    ('collected', 'map vs localdatetime'),
    ('collected', 'map vs time'),
    ('collected', 'string vs date'),
    ('collected', 'string vs datetime'),
    ('collected', 'string vs localdatetime'),
    ('collected', 'string vs time'),
    ('collected', 'time vs date'),
    ('collected', 'time vs datetime'),
    ('collected', 'time vs list'),
    ('collected', 'time vs localdatetime'),
    ('collected', 'time vs map'),
    ('collected', 'time vs string'),
    ('subscript', 'date vs datetime'),
    ('subscript', 'date vs localdatetime'),
    ('subscript', 'date vs string'),
    ('subscript', 'date vs time'),
    ('subscript', 'datetime vs date'),
    ('subscript', 'datetime vs localdatetime'),
    ('subscript', 'datetime vs string'),
    ('subscript', 'datetime vs time'),
    ('subscript', 'localdatetime vs date'),
    ('subscript', 'localdatetime vs datetime'),
    ('subscript', 'localdatetime vs string'),
    ('subscript', 'localdatetime vs time'),
    ('subscript', 'string vs date'),
    ('subscript', 'string vs datetime'),
    ('subscript', 'string vs localdatetime'),
    ('subscript', 'string vs time'),
    ('subscript', 'time vs date'),
    ('subscript', 'time vs datetime'),
    ('subscript', 'time vs localdatetime'),
    ('subscript', 'time vs string'),
})
OPS = ("<", "<=", ">", ">=", "=", "<>")
FORMS = ("literal", "with", "param", "subscript", "unwound", "collected", "stored")
MATH = ("abs", "ceil", "floor", "round", "sign", "sqrt", "exp", "log", "log10", "sin", "cos", "tan", "cot", "asin",
        "acos", "atan", "degrees", "radians", "haversin")
#: The value each math function reads: inside every function's domain (asin and acos need [-1, 1], log a positive).
MATH_VALUES = dict(LEFT, float=0.5)
TYPE_ERROR = "TypeError"
#: The label of the nodes the `stored` form reads; each holds `k` (its kind) and `v` (the value; none for null).
STORED_LABEL = "ZZT308"


class CypherTypeError(Exception):
    pass


def _number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def order(a, b) -> Optional[int]:
    """-1, 0 or 1 where openCypher orders `a` against `b` with `<`, else None (null)."""
    if a is None or b is None:
        return None
    if isinstance(a, Temporal) or isinstance(b, Temporal):
        # two values of one temporal kind order by their key; a duration orders with nothing (openCypher)
        if not (isinstance(a, Temporal) and isinstance(b, Temporal)) or a.kind != b.kind or a.kind == "duration":
            return None
        ka, kb = a.key(), b.key()
        return (ka > kb) - (ka < kb)
    if _number(a) and _number(b):
        if isinstance(a, float) and math.isnan(a) or isinstance(b, float) and math.isnan(b):
            return None
        return (a > b) - (a < b)
    for t in (str, bool):
        if isinstance(a, t) and isinstance(b, t) and not (t is str and (isinstance(a, bool) or isinstance(b, bool))):
            return (a > b) - (a < b)
    if isinstance(a, list) and isinstance(b, list):
        for x, y in zip(a, b):
            c = order(x, y)
            if c is None:
                return None
            if c:
                return c
        return (len(a) > len(b)) - (len(a) < len(b))
    return None


def compare(a, op: str, b):
    """openCypher's `a <op> b`: True, False or None."""
    if a is None or b is None:
        return None
    if op in ("=", "<>"):
        eq = cy_eq(a, b)
        return eq if eq is None or op == "=" else not eq
    c = order(a, b)
    if c is None:
        return None
    return {"<": c < 0, "<=": c <= 0, ">": c > 0, ">=": c >= 0}[op]


def math_fn(fn: str, v):
    if v is None:
        return None
    if not _number(v):
        raise CypherTypeError(fn)
    x = float(v)
    if fn == "abs":
        return abs(v)
    if fn == "ceil":
        return float(math.ceil(x))
    if fn == "floor":
        return float(math.floor(x))
    if fn == "round":
        return float(math.floor(x + 0.5))
    if fn == "sign":
        return (x > 0) - (x < 0)
    if fn == "sqrt":
        return math.sqrt(x) if x >= 0 else float("nan")
    if fn == "exp":
        return math.exp(x)
    if fn in ("log", "log10"):
        if x <= 0:
            return float("-inf") if x == 0 else float("nan")
        return math.log(x) if fn == "log" else math.log10(x)
    if fn in ("asin", "acos"):
        return getattr(math, fn)(x) if -1 <= x <= 1 else float("nan")
    if fn == "cot":
        return 1 / math.tan(x)
    if fn == "degrees":
        return math.degrees(x)
    if fn == "radians":
        return math.radians(x)
    if fn == "haversin":
        return (1 - math.cos(x)) / 2
    return getattr(math, fn)(x)


def size_fn(v):
    if v is None:
        return None
    if isinstance(v, (str, list)):
        return len(v)
    raise CypherTypeError("size")


@dataclass
class Case:
    family: str
    form: str
    text: str
    params: Dict[str, Any]
    expected: Any  # a list of rows, or TYPE_ERROR
    label: str
    floats: bool = False  # compare numbers as floats (the math family)
    stored: List[Tuple[str, Any]] = field(default_factory=list)  # kinds the query reads from STORED_LABEL nodes


def _read(form: str, values: List[Any], names: List[str]) -> Tuple[str, List[str], Dict[str, Any]]:
    """The clauses that bring `values` in under `form`, and the expression reading each."""
    lits = [cy_text(v) for v in values]
    if form == "literal":
        return "", lits, {}
    if form == "with":
        return "WITH " + ", ".join(f"{t} AS {n}" for t, n in zip(lits, names)) + " ", names, {}
    if form == "param":
        # a temporal parameter is its text, read through its constructor
        exprs = [f"{v.kind}(${n})" if isinstance(v, Temporal) else f"${n}" for v, n in zip(values, names)]
        return "", exprs, {n: (v.text if isinstance(v, Temporal) else v) for v, n in zip(values, names)}
    if form == "subscript":
        return f"WITH [{', '.join(lits)}] AS l ", [f"l[{i}]" for i in range(len(values))], {}
    if form == "unwound":
        return " ".join(f"UNWIND [{t}] AS {n}" for t, n in zip(lits, names)) + " ", names, {}
    if form == "collected":
        return f"UNWIND [{', '.join(lits)}] AS e WITH collect(e) AS l ", [f"l[{i}]" for i in range(len(values))], {}
    raise ValueError(form)


def _stored(kinds: List[str], names: List[str]) -> Tuple[str, List[str]]:
    pats = ", ".join(f"({n}:{STORED_LABEL} {{k: '{k}'}})" for k, n in zip(kinds, names))
    return f"MATCH {pats} ", [f"{n}.v" for n in names]


def comparison_cases() -> List[Case]:
    out = []
    for ka in COMPARISON_KINDS:
        for kb in COMPARISON_KINDS:
            a, b = LEFT[ka], RIGHT[kb]
            for form in FORMS:
                if form == "collected" and (a is None or b is None):
                    continue  # collect() drops a null: the positions would shift
                if form == "stored" and "map" in (ka, kb):
                    continue  # a map is no property value
                if form == "stored":
                    head, (ea, eb) = _stored([ka + "L", kb + "R"], ["x", "y"])
                    params = {}
                else:
                    head, (ea, eb), params = _read(form, [a, b], ["x", "y"])
                cols = ", ".join(f"{ea} {op} {eb} AS c{i}" for i, op in enumerate(OPS))
                expected = [tuple(compare(a, op, b) for op in OPS)]
                out.append(Case("comparison", form, f"{head}RETURN {cols}", params, expected, f"{ka} vs {kb}",
                                stored=[(ka + "L", a), (kb + "R", b)] if form == "stored" else []))
    return out


def math_cases() -> List[Case]:
    out = []
    for k in KINDS:
        v = MATH_VALUES[k]
        for form in FORMS:
            if form == "collected" and v is None:
                continue
            if form == "stored" and k == "map":
                continue
            if form == "stored":
                head, (e,) = _stored([k + "M"], ["x"])
                params = {}
            else:
                head, (e,), params = _read(form, [v], ["x"])
            stored = [(k + "M", v)] if form == "stored" else []
            if v is None or _number(v):
                cols = ", ".join(f"{fn}({e}) AS c{i}" for i, fn in enumerate(MATH))
                expected = [tuple(math_fn(fn, v) for fn in MATH)]
                out.append(Case("math", form, f"{head}RETURN {cols}", params, expected, f"math of {k}", True, stored))
            else:
                for fn in MATH:
                    out.append(Case("math", form, f"{head}RETURN {fn}({e}) AS c", params, TYPE_ERROR,
                                    f"{fn} of {k}", True, stored))
            try:
                expected = [(size_fn(v),)]
            except CypherTypeError:
                expected = TYPE_ERROR
            out.append(Case("size", form, f"{head}RETURN size({e}) AS c", params, expected, f"size of {k}",
                            stored=stored))
    return out


def all_cases() -> List[Case]:
    return comparison_cases() + math_cases()


def stored_values() -> Dict[str, Any]:
    """`k` → value of the nodes the `stored` form reads (L: the left values, R: the right ones, M: the math ones)."""
    out = {}
    for k in COMPARISON_KINDS:
        if k == "map":
            continue
        out[k + "L"] = LEFT[k]
        out[k + "R"] = RIGHT[k]
        if k in KINDS:
            out[k + "M"] = MATH_VALUES[k]
    return out
