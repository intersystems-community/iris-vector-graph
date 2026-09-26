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
    `SQLUser.JSON_VALUE(...)` always returns SQL VARCHAR regardless of what the
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
    parsed = parse_prop_text(text)
    if not isinstance(parsed, str):
        return parsed
    if _CANONICAL_INT_RE.match(parsed):
        return int(parsed)
    if _CANONICAL_FLOAT_RE.match(parsed):
        return float(parsed)
    return parsed


def bool_spellings_sql(value: bool) -> str:
    """SQL list of every stored spelling of `value`, for `col IN (...)`."""
    spellings = TRUE_SPELLINGS if value else FALSE_SPELLINGS
    return "(" + ", ".join(f"'{s}'" for s in spellings) + ")"
