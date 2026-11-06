"""How property values are spelled in the text columns that hold them.

`rdf_props.val` is VARCHAR with no type tag. A boolean is written as the JSON
text 'true' / 'false', so `toString(n.flag)` and a Bolt client both see a
boolean. Rows written before that hold '1' / '0' (the DB-API binds a Python
bool as an integer) or 'True' / 'False' (`str(bool)` in the Python API); a
comparison against a boolean accepts every spelling so those rows still match.
"""

from __future__ import annotations

import json
import re
from typing import Any

TRUE_SPELLINGS = ("true", "1", "True")
FALSE_SPELLINGS = ("false", "0", "False")

_CANONICAL_INT_RE = re.compile(r"^-?(0|[1-9]\d*)$")
_CANONICAL_FLOAT_RE = re.compile(r"^-?(0|[1-9]\d*)\.\d+$")


def prop_text(value: Any) -> str:
    """The text stored for a property value."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)


def parse_prop_text(text: Any) -> Any:
    """A stored 'true' / 'false' as a bool; anything else unchanged.

    Legacy '1' / '0' stay as they are: they cannot be told apart from integers.
    """
    if text == "true":
        return True
    if text == "false":
        return False
    return text


def parse_rel_prop_text(text: Any) -> Any:
    """A relationship-qualifier property read back through `JSON_VALUE`, typed.

    `rdf_edges.qualifiers` packs every property into one JSON *string* whose
    leaves are themselves written as text (`{"num": "1"}`, never `{"num": 1}` —
    a real JSON number there comes back NULL from IRIS's `JSON_VALUE`), and
    `JSON_VALUE(...)` (IVG's, in the IVG Schema) always returns SQL VARCHAR regardless of what the
    JSON leaf held. A node property has none of this: `rdf_props.val` is a
    real column, and IRIS's driver hands back whatever type the bound
    parameter was written with (an INSERT of a Python `int` reads back an
    `int`), so a bare `RETURN n.num` is already typed by the time it reaches
    here — only the relationship path needs decoding.

    Unlike `parse_prop_text`, this also promotes canonical integer / float
    text to `int` / `float`. That is unambiguous here (unlike a node's
    `rdf_props.val`, which can hold legacy '1' / '0' spelling a boolean):
    relationship qualifiers only ever spell a boolean 'true' / 'false'
    (`prop_text`), so a bare digit string can only be a number.
    """
    # 4.6.0 R307: a list or map leaf, which CY_REL_PROP answers as its JSON
    parsed = decode_json_list_map(parse_prop_text(text))
    if not isinstance(parsed, str):
        return parsed
    if _CANONICAL_INT_RE.match(parsed):
        return int(parsed)
    if _CANONICAL_FLOAT_RE.match(parsed):
        return float(parsed)
    return parsed


def number_spellings(value: Any) -> list:
    """Every text a stored number reads back as through `JSON_VALUE` (4.6.0 R121).

    A JSON number in `qualifiers` (`create_edge(qualifiers={"c": 0.95})`) reads back in ObjectScript's canonical form,
    `.95`; one a Cypher CREATE wrote is the string `str(v)`, `0.95`. An equality on the text has to accept both.
    """
    from decimal import Decimal

    d = Decimal(repr(value)) if isinstance(value, float) else Decimal(value)
    plain = canonical = "0" if d == 0 else format(d.normalize(), "f")
    if canonical.startswith("0."):
        canonical = canonical[1:]
    elif canonical.startswith("-0."):
        canonical = "-" + canonical[2:]
    # `plain` is the JSON IRIS writes for a number a SET stored (0.0000001), which JSON_VALUE answers (4.6.0 R307)
    return list(dict.fromkeys((canonical, str(value), json.dumps(value), plain)))


def bool_spellings_sql(value: bool) -> str:
    """SQL list of every stored spelling of `value`, for `col IN (...)`."""
    spellings = TRUE_SPELLINGS if value else FALSE_SPELLINGS
    return "(" + ", ".join(f"'{s}'" for s in spellings) + ")"


#: The mark `CY_TYPED` puts before a node value's text when the value is stored as a number (4.6.0 R218):
#: `\x01D` a `$DOUBLE`, `\x01N` an integer or decimal, `\x01S` a string that itself began with `\x01`.
#: `CY_LIST_ELEM` puts `\x01J` before a list element's JSON text (4.6.0 R227).
TYPED_MARK = "\x01"
#: The prefix of a stage snapshot's extra column holding a captured column through `CY_TYPED` (4.6.0 R285): the
#: executor decodes `__typed_<col>` into `<col>` and drops it.
SNAPSHOT_TYPED = "__typed_"


def decode_typed_text(value: Any) -> Any:
    """A value `CY_TYPED` read, as the type it is stored with (4.6.0 R218).

    `rdf_props.val` is VARCHAR, and a Cypher write stores a number there as an IRIS number. intersystems-irispython
    5.4.0 read such a value as a Python number, 5.4.1 as IRIS's text (`.94999999999999995559`), so `CY_TYPED` marks
    it: a `$DOUBLE` is a float (0.95, and 5.0 for 5), an integer an int, a decimal with a fraction a float. Anything
    without the mark (a string, an API-written number's text, a value a driver already typed) is returned as it is.
    """
    if not isinstance(value, str) or not value.startswith(TYPED_MARK):
        return value
    kind, text = value[1:2], value[2:]
    if kind == "D":
        return float(text)
    if kind == "N":
        if "." in text or "E" in text or "e" in text:
            return float(text)
        return int(text)
    if kind == "S":
        return text
    if kind == "J":
        # a scalar element of a list a row holds, as JSON: 1, 2.5, "a", true (4.6.0 R227)
        return json.loads(text)
    return value


def decode_json_list_map(value: Any) -> Any:
    """A stored property value whose text is a JSON list or map, as that list or map (4.6.0 R307); anything else as it is.

    Every writer stores a node's list or map in `rdf_props.val` as its JSON text (`prop_text`), and IVG's JSON functions
    hand a relationship's list back as text, so `RETURN n.p` answered the string `'[1, 2, 3]'` where `get_node()` answered
    the list. The rule is get_node's: text that starts and ends with `[` and `]`, or `{` and `}`, and parses. A string
    that spells a list or map (`'[1, 2]'`) therefore reads as one too (docs/KNOWN_ISSUES.md). SQL builds JSON by the same
    rule (`CY_JSON_LISTMAP`), so text holding NaN or Infinity, which is not JSON, stays text on both."""
    if not isinstance(value, str) or len(value) < 2 or (value[0], value[-1]) not in (("[", "]"), ("{", "}")):
        return value
    try:
        parsed = json.loads(value, parse_constant=_not_json)
    except ValueError:
        return value
    return parsed if isinstance(parsed, (list, dict)) else value


def _not_json(name: str):
    """NaN, Infinity and -Infinity, which Python's json reads and JSON (and IRIS's %FromJSON) does not."""
    raise ValueError(name)


def read_prop_value(value: Any) -> Any:
    """A node property value as a read answers it: `CY_TYPED`'s mark decoded, then a list or map's text decoded."""
    if isinstance(value, str) and value.startswith(TYPED_MARK):
        return decode_typed_text(value)
    return decode_json_list_map(value)


def read_node_value(value: Any) -> Any:
    """A value `CY_TYPED` read from `rdf_props.val`, as `get_node()` and `get_nodes()` answer it: `read_prop_value`, and
    a stored 'true' / 'false' as the boolean. A number's text written by the API stays text ('7')."""
    if isinstance(value, str) and value.startswith(TYPED_MARK):
        return decode_typed_text(value)
    decoded = decode_json_list_map(value)
    return parse_prop_text(value) if decoded is value else decoded
