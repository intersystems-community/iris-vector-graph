"""How property values are spelled in the text columns that hold them.

`rdf_props.val` is VARCHAR with no type tag. A boolean is written as the JSON
text 'true' / 'false', so `toString(n.flag)` and a Bolt client both see a
boolean. Rows written before that hold '1' / '0' (the DB-API binds a Python
bool as an integer) or 'True' / 'False' (`str(bool)` in the Python API); a
comparison against a boolean accepts every spelling so those rows still match.
"""

from __future__ import annotations

import json
from typing import Any

TRUE_SPELLINGS = ("true", "1", "True")
FALSE_SPELLINGS = ("false", "0", "False")


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


def bool_spellings_sql(value: bool) -> str:
    """SQL list of every stored spelling of `value`, for `col IN (...)`."""
    spellings = TRUE_SPELLINGS if value else FALSE_SPELLINGS
    return "(" + ", ".join(f"'{s}'" for s in spellings) + ")"
