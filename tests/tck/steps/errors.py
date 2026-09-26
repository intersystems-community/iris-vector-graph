"""Map what IVG or IRIS raised onto an openCypher error kind, phase and detail.

Spec 229 Phase 5 (US3, FR-007, FR-008). A TCK step such as

    Then a SyntaxError should be raised at compile time: UndefinedVariable

used to pass on any exception at all, so a translator regression that made IRIS reject
the generated SQL (``SQLCODE -23`` at Prepare) was indistinguishable from the engine
refusing the query for the right reason. :func:`classify` turns an exception (or an
``IVGResult.error`` string) into an :class:`ObservedError`; :func:`mismatch` compares it
with the scenario's expectation and says why it does not match.

The IVG/IRIS error surface for each openCypher kind (``KIND_SURFACE``):

- **SyntaxError** -- ``CypherParseError`` (parser, and translator checks that raise it),
  or a ``SyntaxError`` raised by a ``raise`` statement inside ``iris_vector_graph``.
- **TypeError** -- a ``TypeError`` raised by a ``raise`` statement inside
  ``iris_vector_graph``, or IRIS ``SQLCODE -149`` from ``CypherFn_IVGTYPEERROR``, the UDF
  the translator emits to raise a Cypher type error while the query runs.
- **ArgumentError** -- a ``ValueError`` raised inside ``iris_vector_graph`` whose message
  names an openCypher detail code; or a UDF failure whose ``%msg`` is tagged
  ``ArgumentError:``.
- **EntityNotFound** -- ``translator.EntityNotFoundError``.
- **ParameterMissing** -- a ``KeyError`` raised inside ``iris_vector_graph`` naming
  ``MissingParameter``.
- **ProcedureError** / **SemanticError** -- the specific IVG messages in
  ``_MESSAGE_PATTERNS``, or a message tagged with the kind.
- **ConstraintVerificationFailed** -- IRIS integrity SQLCODEs ``-119``..``-124`` at run
  time, or an IVG message tagged ``ConstraintVerificationFailed:``.

Everything else carries **no** openCypher kind and satisfies no expected error: SQL
prepare failures (``-1``, ``-23``, ``-29``, ``-359``, ...), a UDF failing without a tag,
``RuntimeError`` (a stale connection), and any Python ``TypeError``/``ValueError``/
``KeyError`` that the interpreter raised rather than an IVG ``raise`` statement
(``json.dumps`` on an AST node is not a Cypher type error).

Phase: parser, translator and SQL-prepare errors are *compile time*; errors raised while
IRIS executes or fetches, or by IVG code outside ``iris_vector_graph.cypher``, are
*runtime*. A scenario expecting a compile-time error fails on a runtime one. A scenario
expecting a runtime error accepts a compile-time one, because IVG's translator binds
parameter values and folds constants, so its "compile" step is not openCypher's; this is
recorded in ``docs/KNOWN_ISSUES.md``.

Detail: read from the message when IVG names the code (``VariableAlreadyBound: ...``,
``(MissingParameter)``) or from a known IVG message (``Undefined variable: x``). A
detail that is present and different fails the scenario. When IVG's message names no
detail (a bare parse error such as ``Expected ), got EOF``) the scenario is matched on
kind and phase alone -- the harness cannot map it, and says so in ``ObservedError``.
"""
from __future__ import annotations

import dis
import re
from dataclasses import dataclass
from typing import Optional, Union

COMPILE = "compile time"
RUNTIME = "runtime"
ANY_TIME = "any time"

KINDS = (
    "SyntaxError",
    "SemanticError",
    "ParameterMissing",
    "ConstraintVerificationFailed",
    "ConstraintValidationFailed",
    "EntityNotFound",
    "PropertyNotFound",
    "LabelNotFound",
    "TypeError",
    "ArgumentError",
    "ArithmeticError",
    "ProcedureError",
)

KIND_SURFACE: dict[str, str] = {
    "SyntaxError": "CypherParseError, or SyntaxError raised by IVG",
    "TypeError": "TypeError raised by IVG, or SQLCODE -149 from CypherFn_IVGTYPEERROR",
    "ArgumentError": "ValueError raised by IVG naming a detail code, or a UDF %msg tagged ArgumentError",
    "EntityNotFound": "translator.EntityNotFoundError",
    "ParameterMissing": "KeyError raised by IVG naming MissingParameter",
    "ProcedureError": "IVG 'Procedure not found' message, or a message tagged ProcedureError",
    "SemanticError": "IVG 'Cannot merge on null property value' message, or a message tagged SemanticError",
    "ConstraintVerificationFailed": "IRIS SQLCODE -119..-124 at run time, or a message tagged ConstraintVerificationFailed",
}

# Every detail code the upstream corpus uses, plus the list-index code IVG maps to.
DETAILS = frozenset("""
AmbiguousAggregationExpression ColumnNameConflict CreatingVarLength DeleteConnectedNode
DeletedEntityAccess DifferentColumnsInUnion FloatingPointOverflow IntegerOverflow
InvalidAggregation InvalidArgumentPassingMode InvalidArgumentType InvalidArgumentValue
InvalidClauseComposition InvalidDelete InvalidNumberLiteral InvalidNumberOfArguments
InvalidParameterUse InvalidPropertyType InvalidRelationshipPattern InvalidUnicodeCharacter
InvalidUnicodeLiteral MapElementAccessByNonString MergeReadOwnWrites MissingParameter
NegativeIntegerArgument NestedAggregation NoExpressionAlias NonConstantExpression
NoSingleRelationshipType NoVariablesInScope NumberOutOfRange ProcedureNotFound
RelationshipUniquenessViolation RequiresDirectedRelationship UndefinedVariable
UnexpectedSyntax UnknownFunction VariableAlreadyBound VariableTypeConflict
ListElementAccessByNonInteger
""".split())

# IVG messages that name their error without a detail code: (regex, kind or None, detail).
# A kind here overrides the one the exception class implies (ValueError -> ArgumentError).
_MESSAGE_PATTERNS: tuple[tuple[re.Pattern, Optional[str], str], ...] = tuple(
    (re.compile(p), k, d)
    for p, k, d in (
        (r"^Undefined variable: ", None, "UndefinedVariable"),
        (r"All UNION branches must have the same column names", None, "DifferentColumnsInUnion"),
        (r"Cannot mix UNION and UNION ALL", None, "InvalidClauseComposition"),
        (r"^Procedure not found: ", "ProcedureError", "ProcedureNotFound"),
        (r"^Cannot merge on null property value", "SemanticError", "MergeReadOwnWrites"),
        (r"Cannot delete node with existing relationships", None, "DeleteConnectedNode"),
        (r"argument .* must be an integer, got", None, "InvalidArgumentType"),
        (r"^Type mismatch: expected ", None, "InvalidArgumentType"),
        (r"Map element access by non-string", None, "MapElementAccessByNonString"),
        (r"Non-integer index type for list subscript", None, "ListElementAccessByNonInteger"),
    )
)

# IRIS integrity SQLCODEs: uniqueness (-119/-120) and referential (-121..-124).
_INTEGRITY_SQLCODES = {-119, -120, -121, -122, -123, -124}
_SQLCODE_DETAIL = {-124: "DeleteConnectedNode"}  # FK check on DELETE of a referenced row

_SQLCODE_RE = re.compile(r"SQLCODE: <(-?\d+)>")
_LOCATION_RE = re.compile(r"\[Location: <([^>]*)>\]")
_UDF_RE = re.compile(
    r"SQL Function (\S+) failed with error:\s*SQLCODE=-?\d+,%msg=(.*?)>\]?\s*$", re.S
)
_PARSE_PREFIX_RE = re.compile(r"^Cypher error at line \d+, col \d+:\s*")
_KIND_TAG_RE = re.compile(r"^(" + "|".join(KINDS) + r")\s*:\s*")
_DETAIL_RE = re.compile(r"\b(" + "|".join(sorted(DETAILS, key=len, reverse=True)) + r")\b")
_TYPE_ERROR_UDF = "CYPHERFN_IVGTYPEERROR"
_PKG_MARKER = "/iris_vector_graph/"
_COMPILE_MARKER = "/iris_vector_graph/cypher/"


@dataclass(frozen=True)
class ObservedError:
    """What was raised, in openCypher terms. ``kind`` is None when it has none."""

    kind: Optional[str]
    phase: str
    detail: Optional[str]
    sqlcode: Optional[int]
    source: str
    message: str

    def describe(self) -> str:
        kind = self.kind or "no openCypher error kind"
        detail = self.detail or "detail not mappable"
        return f"{kind} at {self.phase} ({detail}); raised as {self.source}: {self.message[:300]}"


def classify(err: Union[BaseException, str]) -> ObservedError:
    """Map an exception, or an ``IVGResult.error`` string, onto an openCypher error."""
    if isinstance(err, str):
        return _classify_text(err, source="IVGResult.error")
    text = str(err)
    if _SQLCODE_RE.search(text):
        return _classify_text(text, source=type(err).__name__)
    return _classify_python(err)


def mismatch(
    expected_kind: str,
    expected_phase: str,
    expected_detail: str,
    observed: ObservedError,
) -> Optional[str]:
    """None when ``observed`` satisfies the expectation, else the reason it does not."""
    want = f"{expected_kind} at {expected_phase}: {expected_detail}"
    if observed.kind is None:
        return f"Expected {want}; IVG raised an error with no openCypher kind: {observed.describe()}"
    if observed.kind != expected_kind:
        return f"Expected {want}; IVG raised {observed.describe()}"
    if expected_phase == COMPILE and observed.phase != COMPILE:
        return f"Expected {want}; IVG raised it at {observed.phase}: {observed.describe()}"
    if expected_detail != "*" and observed.detail is not None and observed.detail != expected_detail:
        return f"Expected {want}; IVG raised detail {observed.detail}: {observed.describe()}"
    return None


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _tag_and_detail(message: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """(kind tag, detail, pattern kind) read from an IVG message."""
    body = message.strip().strip("'\"")
    body = _PARSE_PREFIX_RE.sub("", body)
    tag = None
    m = _KIND_TAG_RE.match(body)
    if m:
        tag = m.group(1)
        body = body[m.end():]
    detail = None
    pattern_kind = None
    d = _DETAIL_RE.search(body)
    if d:
        detail = d.group(1)
    for rx, kind, code in _MESSAGE_PATTERNS:
        if rx.search(body):
            pattern_kind = kind
            detail = detail or code
            break
    return tag, detail, pattern_kind


def _classify_text(text: str, source: str) -> ObservedError:
    m = _SQLCODE_RE.search(text)
    if not m:
        return ObservedError(None, RUNTIME, None, None, source, text)
    code = int(m.group(1))
    loc = _LOCATION_RE.search(text)
    location = loc.group(1) if loc else "unknown location"
    phase = COMPILE if "Prepare" in location else RUNTIME
    src = f"{source} SQLCODE {code} at {location}"
    if phase == RUNTIME and code in _INTEGRITY_SQLCODES:
        detail = _SQLCODE_DETAIL.get(code, f"SQLCODE {code}")
        return ObservedError("ConstraintVerificationFailed", phase, detail, code, src, text)
    udf = _UDF_RE.search(text)
    if phase == RUNTIME and udf:
        fn, payload = udf.group(1), udf.group(2)
        tag, detail, pattern_kind = _tag_and_detail(payload)
        kind = tag or pattern_kind
        if fn.upper().endswith(_TYPE_ERROR_UDF):
            kind = kind or "TypeError"
        return ObservedError(kind, phase, detail, code, f"{src} in {fn}", text)
    return ObservedError(None, phase, None, code, src, text)


def _deepest_frame(err: BaseException):
    tb = err.__traceback__
    last = None
    while tb is not None:
        last, tb = tb, tb.tb_next
    return last


def _is_raise_statement(tb) -> bool:
    """True when the traceback's last instruction is a Python ``raise``."""
    code = tb.tb_frame.f_code.co_code
    raise_op = dis.opmap["RAISE_VARARGS"]
    for off in (tb.tb_lasti, tb.tb_lasti * 2):
        if 0 <= off < len(code) and code[off] == raise_op:
            return True
    return False


def _classify_python(err: BaseException) -> ObservedError:
    from iris_vector_graph.cypher.parser import CypherParseError
    from iris_vector_graph.cypher.translator import EntityNotFoundError

    message = str(err)
    last = _deepest_frame(err)
    filename = last.tb_frame.f_code.co_filename if last is not None else ""
    in_pkg = _PKG_MARKER in filename.replace("\\", "/")
    phase = COMPILE if _COMPILE_MARKER in filename.replace("\\", "/") else RUNTIME
    where = filename.split(_PKG_MARKER)[-1] if in_pkg else (filename or "no traceback")
    source = f"{type(err).__name__} from {where}"
    tag, detail, pattern_kind = _tag_and_detail(message)

    if isinstance(err, CypherParseError):
        return ObservedError(tag or pattern_kind or "SyntaxError", COMPILE, detail, None,
                             source, message)
    if isinstance(err, EntityNotFoundError):
        return ObservedError(tag or "EntityNotFound", phase, detail, None, source, message)

    deliberate = in_pkg and last is not None and _is_raise_statement(last)
    if not deliberate:
        return ObservedError(None, phase, detail, None, source, message)

    kind = tag or pattern_kind
    if kind is None:
        if isinstance(err, SyntaxError):
            kind = "SyntaxError"
        elif isinstance(err, TypeError):
            kind = "TypeError"
        elif isinstance(err, KeyError):
            kind = "ParameterMissing" if detail == "MissingParameter" else None
        elif isinstance(err, ValueError):
            kind = "ArgumentError" if detail is not None else None
    return ObservedError(kind, phase, detail, None, source, message)
