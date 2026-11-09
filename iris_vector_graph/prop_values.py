"""How property values are spelled in the text columns that hold them.

`rdf_props.val` is VARCHAR with no type tag. A boolean is written as the JSON
text 'true' / 'false', so `toString(n.flag)` and a Bolt client both see a
boolean. Rows written before that hold '1' / '0' (the DB-API binds a Python
bool as an integer) or 'True' / 'False' (`str(bool)` in the Python API); a
comparison against a boolean accepts every spelling so those rows still match.

Typed values (the 4.6.0 R308 / R310 contract)
---------------------------------------------

One rule for how a value keeps its type from SQL to the rows `execute_cypher` answers; the translator, the SQL
functions and the result decoding follow it (oracle-cypher's 460-cmptypes and fixes64's 460-fixes67 agreed it). Reads
outside a Cypher result (`get_node`, the embedding reads, a write's capture) decode with the same functions.

1. Inside SQL. A stage column (what a WITH or an UNWIND carries) is never marked; each kind has one spelling: a
   number an IRIS number ($LISTBUILD type 4 to 9: an integer, a decimal, a $DOUBLE); a string its text, '' as
   $Char(0); a boolean 1 / 0 with a `<col>__b` flag column where its kind is not known when the query is translated
   (R273, R286), or the text 'true' / 'false' as a stored property or CY_LIST_VAL holds it; a list or map its JSON
   text; null SQL's NULL. A value marked for decoding or for a comparison is TYPED_MARK ($Char(1)), a kind letter and
   text: "N" an IRIS integer's or decimal's text, "D" a $DOUBLE's text (CY_JSON_TYPED marks a decimal "D" too; "D"
   decodes as a float, "N" as a float when its text has a fraction or exponent, else an int), "S" a string that would
   otherwise begin with $Char(1), "J" the JSON text of any value. No other letters. CY_JCMP (the comparison of two
   typed values) takes each operand with a tag: "j" JSON text or a marked value, "n" a number, "s" a string, "b" a
   boolean, "v" / "w" a CY_LIST_VAL value without a flag / with a 0 flag.
2. Who marks and who strips. A mark is made by a SQL function (CY_TYPED, CY_JSON_TYPED, CY_LIST_ELEM) or by the
   translator's `CHAR(1) || 'J' || <json>` around a RETURN column's JSON text, in a RETURN column, as a function's
   argument, or in a write's capture SELECT (its `__typed_` columns, decoded and dropped by the write path, never a
   result row). A mark never reaches a stage column or the inside of a JSON list. Python strips once per value. A
   parameter (and `$p[i]`, `$p[a..b]`, `$m.k` at literal indexes) is known before the query runs: the translator
   binds its value, a list or map as its JSON text, a boolean decoded through `bool_expr_columns`; no mark is
   involved.
3. One decode point for result rows: `_finish_sql_result`, by the columns the translator marks: `typed_value_columns`
   through `read_prop_value` (`decode_typed_text`, then the stored-text rule for a stored list or map);
   `json_value_columns` (columns proven to hold a list, map or path) through `decode_json_value` (plain JSON, NaN
   accepted, a non-string or null passed through); `mixed_expr_columns` through `_mixed_scalar_value`, or
   `_literal_mixed_value` where `mixed_expr_strings` holds the column's source literal list's string elements (those
   stay strings; a Decimal with digits after the point is a float, else an int: Decimal('1E+2') is 100). A column in
   both `typed_value_columns` and `json_value_columns` is decoded by the first, and the second passes its list or map
   through. Bolt, REST and GraphQL read `_finish_sql_result`'s output: Bolt's `_coerce_scalar` passes a scalar
   through (a Decimal becomes a float) and reads JSON only for whole nodes and relationships (`_recompose_graph_row`,
   `_parse_props_field`); GraphQL's `serialize_value` decodes no string.
4. collect() and list elements. collect() builds `'[' || LIST(text) || ']'` from each element's JSON text
   (`_list_element_text`): a list or map column its raw JSON; a list or map expression its raw JSON (container kind,
   checked first, fixes64's); a flagged boolean 'true' / 'false'; an all-boolean UNWIND's 1 / 0 as 'true' / 'false';
   a JSON_TABLE row of a list known when the query is translated by its text (`_unwound_known_text`, read by
   collect() and a returned row only: inside a list literal a nested list made JSON_TABLE iterate the inner list); a
   stored property as RETURN reads it; a float through CY_JSON_NUM; a collected node `_id, _labels, _props[, _graph]`
   in that order. JSON_ARRAYAGG stays for scalars of one kind and for the JSON objects of nodes, `nodes(p)` and
   paths, and of a RETURN's `collect(r)` relationships, which it lists as JSON strings (clause 6 reads them
   back). A list or map literal whose items are all literals or known parameters is bound
   whole as one JSON parameter. A list built at run time whose elements cannot be told apart by their text, and a
   known list holding a string that is the same text as another element, are a known issue (docs/KNOWN_ISSUES.md).
6. Graph values (4.6.0 R314, oracle-cypher and fixes64). A node, relationship or path answers one dict wherever it
   sits: a node held by a list or map at any depth, or taken from one (`head()`, a subscript, an UNWIND element),
   `{"_id", "_labels", "_props"}` with `_props` a list of JSON text (a node returned by itself stays three columns,
   `n_id`, `n_labels`, `n_props`); a relationship, by itself or held, `{"type", "props"}` with `props` a dict; a path
   `{"nodes", "rels"}`, the node ids and relationship types as `RETURN p` answers them. A procedure's path is a dict
   of its own keys (ivg.shortestPath.weighted: `nodes`, `rels`, `costs`, `length`, `totalCost`), which its procedure
   must answer as a Python value (the CALL sweep, 460-fixes80-call), not through this clause's passes. JSON_ARRAYAGG and JSON_ARRAY quote an element's JSON text
   as a string, so two passes decode them, in `_finish_sql_result` and on the BFS routes' answer alike:
   `json_value_columns` through `decode_json_value` as in clause 3 (a list, map, path or `RETURN r` column's own
   JSON), then `_decode_graph_json_values` for `node_json_columns` and `graph_json_columns` (the columns
   `_holds_collected_nodes` and `_holds_graph_json` mark: collected nodes, relationships and `nodes(p)`, a list mixing
   graph values with others, and an element of one). That pass reads each string of the value that parses to an
   object with exactly the node's, relationship's or path's keys, in that order, and no other string; it does not
   read inside a graph element (its properties and a path's ids are the user's). A column in both
   `typed_value_columns` and `json_value_columns` is still decoded once, by the first. A typed column whose "J" text
   is a JSON string (`head(collect(n))`: CY_LIST_ELEM over JSON_ARRAYAGG's quoted node) decodes to that string, the
   node's JSON, which the graph pass then reads; a "J" text that is an object or a list decodes to it, and the graph
   pass reads only the strings inside. `RETURN collect(r)`, at any
   depth of a RETURN item, collects each relationship's JSON; a WITH's `collect(r)` keeps the types, which later stages
   read (a known issue). The graph values a comparison reads (CY_JCMP over a WITH list mixing a node with other values,
   CY_CMP over `mixed_value_vars`) are not result values; this clause does not change them. Bolt packs a relationship's
   dict as its Relationship structure, by itself (`_pack_rel_from_value`) and between two nodes' columns
   (`_recompose_graph_row`).
"""

from __future__ import annotations

import json
import numbers
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


_INT64_MIN, _INT64_MAX = -(2**63), 2**63 - 1


def prop_value(value: Any) -> Any:
    """What a Python write binds into `rdf_props.val`: what a Cypher write binds for the same value (4.6.4).

    An integer within 64 bits as an int and a float as a float, so IRIS stores the number with its type and
    `CY_TYPED` reads it back as one (an int 30 as 30, a float 2.0 as 2.0, NaN and infinities as floats); anything
    else as `prop_text`: a boolean 'true' / 'false', a list or map its JSON text, a string itself. An integer past
    64 bits is its text, as a Cypher parameter of one is. Before 4.6.4 every Python path bound `prop_text(v)`, so 30
    was stored, and read back, as the string '30'.
    """
    if isinstance(value, bool) or type(value).__name__ in ("bool_", "bool"):  # numpy's bool is not a bool
        return prop_text(bool(value))
    if not isinstance(value, numbers.Real):  # a string, list, map or Decimal (a Decimal is no numbers.Real)
        return prop_text(value)
    if isinstance(value, numbers.Integral):
        v = int(value)
        return v if _INT64_MIN <= v <= _INT64_MAX else str(v)
    if isinstance(value, numbers.Rational):  # a Fraction: its text, as before
        return prop_text(value)
    return float(value)


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
    without the mark (a string, the text a Python write stored for a number before 4.6.4, a value a driver already
    typed) is returned as it is.
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


def decode_json_value(value: Any) -> Any:
    """A column the translator marks as always a list, a map or a path (SQLQuery.json_value_columns, 4.6.0 R311): its
    JSON text as the list or dict. A value already decoded, and null, pass through. Unlike `decode_json_list_map`, no
    spelling test (the column never holds a string) and NaN reads as the float it is. Text that does not parse raises
    ValueError: the column's mark says it is JSON, so such text is a translator defect."""
    if not isinstance(value, str):
        return value
    return json.loads(value)


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
