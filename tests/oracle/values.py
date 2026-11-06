"""4.6.0 R307: an adversarial set of property values, each with the ways a writer can spell it.

Every value carries:
- ``key``: a short name, unique, used in node ids and as the stored ``k`` property that scopes a read to one value;
- ``group``: what the value is for ordering and for which readers apply (see GROUPS);
- ``py``: the Python value the API writers (``create_node``, ``bulk_create_nodes``, NDJSON, ``create_edge``) and the
  Cypher parameter writers (``{p: $v}``, ``SET x.p = $v``) pass, or None when no Python value spells it (a duration);
- ``cypher``: the Cypher expression the literal writers (CREATE, SET, MERGE) write, or None when Cypher has no
  literal for it (NaN and infinity);
- ``oc``: openCypher's answer when the value is read back, in Python. A temporal value reads as its ISO 8601 text, the
  way the TCK writes it in a result table (IVG has no Python temporal types); ``duration('P2W')`` is ``'P14D'``.

The answers are written by hand from openCypher's rules, not read from IVG.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional

#: The groups: the readers that apply to each, and how ORDER BY sorts it (tests/oracle/spec.py).
GROUPS = (
    "int",       # an integer
    "float",     # a float, finite or not
    "numstr",    # a string that spells a number
    "pdurstr",   # a string that is a well-formed ISO 8601 duration
    "pstr",      # a string starting with P that is not a duration
    "str",       # any other string
    "spelled",   # a string that spells another type's stored text: 'true', '[1, 2]', '{"a": 1}', '\x00'
    "dur",       # a Cypher duration
    "date",      # a Cypher date or datetime
    "bool",      # a boolean
    "list",      # a list
    "map",       # a map
)


@dataclass(frozen=True)
class Value:
    key: str
    group: str
    py: Any
    cypher: Optional[str]
    oc: Any
    #: the openCypher kind of `oc` when its Python type does not say it: 'duration', 'date', 'datetime',
    #: 'localdatetime'
    temporal: str = ""

    @property
    def api_writable(self) -> bool:
        """A Python value spells it, so the API and the Cypher parameter writers can write it."""
        return not self.temporal

    @property
    def literal_writable(self) -> bool:
        return self.cypher is not None

    @property
    def is_nan_or_inf(self) -> bool:
        return isinstance(self.oc, float) and not math.isfinite(self.oc)

    @property
    def is_nan(self) -> bool:
        return isinstance(self.oc, float) and math.isnan(self.oc)


def _same(key, group, v, cypher):
    return Value(key, group, v, cypher, v)


def _s(key, group, text, cypher):
    """A string value: its Python text and its Cypher literal."""
    return Value(key, group, text, cypher, text)


def _t(key, kind, cypher, iso):
    return Value(key, "dur" if kind == "duration" else "date", None, cypher, iso, temporal=kind)


VALUES: tuple[Value, ...] = (
    # integers
    _same("i0", "int", 0, "0"),
    _same("im1", "int", -1, "-1"),
    _same("i7", "int", 7, "7"),
    _same("i2p53", "int", 2**53 + 1, "9007199254740993"),
    _same("ibig", "int", 2**62 + 3, "4611686018427387907"),
    # floats
    _same("f095", "float", 0.95, "0.95"),
    _same("f1", "float", 1.0, "1.0"),
    _same("f2", "float", 2.0, "2.0"),
    _same("fm05", "float", -0.5, "-0.5"),
    _same("f1em7", "float", 1e-7, "1e-7"),
    _same("f1e20", "float", 1e20, "1e20"),
    Value("fnan", "float", math.nan, None, math.nan),
    Value("finf", "float", math.inf, None, math.inf),
    Value("fminf", "float", -math.inf, None, -math.inf),
    # strings that spell a number
    _s("s7", "numstr", "7", "'7'"),
    _s("s095", "numstr", "0.95", "'0.95'"),
    _s("sd95", "numstr", ".95", "'.95'"),
    _s("s007", "numstr", "007", "'007'"),
    _s("s1e3", "numstr", "1e3", "'1e3'"),
    _s("s10", "numstr", "1.0", "'1.0'"),
    # strings starting with P
    _s("sP", "pstr", "P", "'P'"),
    _s("sPat", "pstr", "Pat", "'Pat'"),
    _s("sP1D", "pdurstr", "P1D", "'P1D'"),
    _s("sPT1H", "pdurstr", "PT1H", "'PT1H'"),
    _s("sP1Y2M", "pdurstr", "P1Y2M", "'P1Y2M'"),
    # durations, dates and datetimes (Cypher only)
    _t("dP1D", "duration", "duration('P1D')", "P1D"),
    _t("dPT1H", "duration", "duration('PT1H')", "PT1H"),
    _t("dPT1H30M", "duration", "duration('PT1H30M')", "PT1H30M"),
    _t("dPT36H", "duration", "duration('PT36H')", "PT36H"),
    _t("dP2W", "duration", "duration('P2W')", "P14D"),
    _t("dP1Y2M", "duration", "duration('P1Y2M')", "P1Y2M"),
    _t("tdate1", "date", "date('2020-01-01')", "2020-01-01"),
    _t("tdate0", "date", "date('2019-12-31')", "2019-12-31"),
    _t("tdt", "datetime", "datetime('2020-01-02T03:04:05Z')", "2020-01-02T03:04:05Z"),
    _t("tldt", "localdatetime", "localdatetime('2020-01-02T03:04:05')", "2020-01-02T03:04:05"),
    # booleans, and strings that spell one
    _same("btrue", "bool", True, "true"),
    _same("bfalse", "bool", False, "false"),
    _s("strue", "spelled", "true", "'true'"),
    _s("sfalse", "spelled", "false", "'false'"),
    _s("sTrue", "str", "True", "'True'"),
    # empty, $Char(0) and its neighbours
    _s("sempty", "str", "", "''"),
    _s("snul", "spelled", "\x00", "'\\u0000'"),
    _s("snula", "str", "a\x00b", "'a\\u0000b'"),
    _s("sspace", "str", " ", "' '"),
    # unicode, quotes, backslashes
    _s("suni", "str", "café ☕ 日本", "'café ☕ 日本'"),
    _s("sastral", "str", "\U0001d11e", "'\U0001d11e'"),
    _s("squote", "str", "it's", "'it\\'s'"),
    _s("sdq", "str", 'say "hi"', "'say \"hi\"'"),
    _s("sbs", "str", "a\\b", "'a\\\\b'"),
    _s("snl", "str", "line1\nline2", "'line1\\nline2'"),
    _s("sdate", "str", "2020-01-01x", "'2020-01-01x'"),
    _s("sjson", "spelled", '{"a": 1}', "'{\"a\": 1}'"),
    _s("slist", "spelled", "[1, 2]", "'[1, 2]'"),
    # lists
    _same("li", "list", [1, 2, 3], "[1, 2, 3]"),
    _same("lf", "list", [0.5, 1.0], "[0.5, 1.0]"),
    _same("lmix", "list", [1, "a", 2.5, True], "[1, 'a', 2.5, true]"),
    _same("lnest", "list", [[1, 2], [3]], "[[1, 2], [3]]"),
    _same("lstr", "list", ["x", "P1D"], "['x', 'P1D']"),
    _same("lempty", "list", [], "[]"),
    # maps
    _same("m1", "map", {"a": 1, "b": "x"}, "{a: 1, b: 'x'}"),
    _same("mnest", "map", {"a": {"b": [1, 2]}}, "{a: {b: [1, 2]}}"),
)

BY_KEY = {v.key: v for v in VALUES}
assert len(BY_KEY) == len(VALUES), "value keys must be unique"
assert {v.group for v in VALUES} <= set(GROUPS)
