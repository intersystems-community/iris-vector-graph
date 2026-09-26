"""Spec 229 Phase 5 (US3): an expected TCK error is matched by kind, not by existence.

`tests/tck/steps/errors.py` maps whatever IVG or IRIS raised onto an openCypher
error kind, phase and detail. These tests pin that mapping, and the step that uses it.
"""
from unittest.mock import MagicMock

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _translate_error(query: str) -> BaseException:
    """The exception IVG raises translating `query` (no database needed)."""
    try:
        translate_to_sql(parse_query(query), {}, engine=None)
    except Exception as exc:  # noqa: BLE001 - the exception is the subject under test
        return exc
    raise AssertionError(f"expected {query!r} to raise")


def _raised(exc: BaseException) -> BaseException:
    """`exc` after a real raise, so it carries a traceback from this test file."""
    try:
        raise exc
    except BaseException as caught:  # noqa: BLE001
        return caught


class _DBError(Exception):
    """Stands in for iris.dbapi's error classes: the SQLCODE lives in the text."""


def _sql_error(code: int, text: str, location: str, msg: str = "") -> _DBError:
    return _DBError(
        f"<SQL ERROR>; Details: [SQLCODE: <{code}>:<{text}>]\r\n"
        f"[Location: <{location}>]\r\n[%msg: <{msg}>]"
    )


SQL_119 = _sql_error(
    -119,
    "UNIQUE or PRIMARY KEY Constraint failed uniqueness check upon INSERT",
    "ServerLoop - Query Execute",
    "Table 'Graph_KG.rdf_edges', Constraint 'U_EDGE' failed uniqueness check",
)
SQL_121 = _sql_error(
    -121,
    "FOREIGN KEY constraint failed referential check upon INSERT of row in referencing table",
    "ServerLoop - Query Execute",
)
SQL_124 = _sql_error(
    -124,
    "FOREIGN KEY constraint failed referential check upon DELETE of row in referenced table",
    "ServerLoop - Query Execute",
)
SQL_23 = _sql_error(
    -23,
    "Label is not listed among the applicable tables",
    "Prepare",
    "Label 'N0' is not listed among the applicable tables",
)


def _mismatch(expected_kind, phase, detail, err):
    from tests.tck.steps.errors import classify, mismatch

    return mismatch(expected_kind, phase, detail, classify(err))


# ---------------------------------------------------------------------------
# T026 / T027: integrity errors are ConstraintVerificationFailed; prepare errors are not
# ---------------------------------------------------------------------------


class TestConstraintVerificationFailed:
    @pytest.mark.parametrize("err", [SQL_119, SQL_121, SQL_124], ids=["-119", "-121", "-124"])
    def test_integrity_sqlcode_satisfies_kind(self, err):
        assert _mismatch("ConstraintVerificationFailed", "runtime", "*", err) is None

    def test_fk_violation_on_delete_is_delete_connected_node(self):
        assert _mismatch(
            "ConstraintVerificationFailed", "runtime", "DeleteConnectedNode", SQL_124
        ) is None

    def test_uniqueness_violation_is_not_delete_connected_node(self):
        why = _mismatch("ConstraintVerificationFailed", "runtime", "DeleteConnectedNode", SQL_119)
        assert why is not None and "-119" in why

    def test_prepare_time_23_does_not_satisfy(self):
        why = _mismatch("ConstraintVerificationFailed", "runtime", "DeleteConnectedNode", SQL_23)
        assert why is not None

    def test_message_distinguishes_prepare_error_from_integrity_error(self):
        from tests.tck.steps.errors import classify

        prep, integ = classify(SQL_23), classify(SQL_119)
        assert prep.sqlcode == -23 and integ.sqlcode == -119
        assert prep.kind is None
        assert integ.kind == "ConstraintVerificationFailed"
        assert prep.phase == "compile time" and integ.phase == "runtime"
        why = _mismatch("ConstraintVerificationFailed", "runtime", "*", SQL_23)
        assert "-23" in why and "Prepare" in why

    def test_ivg_delete_connected_check(self):
        err = _ivg_raised(Exception(
            "ConstraintVerificationFailed: Cannot delete node with existing relationships. "
            "Use DETACH DELETE."
        ), where="stores/iris_sql_store.py")
        from tests.tck.steps.errors import classify

        assert classify(err).phase == "runtime"
        assert _mismatch(
            "ConstraintVerificationFailed", "runtime", "DeleteConnectedNode", err
        ) is None


# ---------------------------------------------------------------------------
# T028: a result carrying an arbitrary error string no longer passes (FR-008)
# ---------------------------------------------------------------------------


def _ctx(error=None, result_error=None):
    ctx = MagicMock()
    ctx.last_error = error
    if result_error is None:
        ctx.last_result = None
    else:
        ctx.last_result = MagicMock()
        ctx.last_result.error = result_error
        ctx.last_result.rows = []
    return ctx


class TestErrorStep:
    def test_arbitrary_result_error_string_fails(self):
        from tests.tck.steps.results import step_error_type_raised

        ctx = _ctx(result_error="something went wrong")
        with pytest.raises(AssertionError):
            step_error_type_raised(ctx, "SemanticError", "runtime", "MergeReadOwnWrites")

    def test_prepare_error_in_result_string_fails(self):
        from tests.tck.steps.results import step_error_type_raised

        ctx = _ctx(result_error=str(SQL_23))
        with pytest.raises(AssertionError, match="-23"):
            step_error_type_raised(ctx, "SyntaxError", "compile time", "VariableTypeConflict")

    def test_integrity_error_in_result_string_passes(self):
        from tests.tck.steps.results import step_error_type_raised

        ctx = _ctx(result_error=str(SQL_124))
        step_error_type_raised(ctx, "ConstraintVerificationFailed", "runtime", "DeleteConnectedNode")

    def test_no_error_fails(self):
        from tests.tck.steps.results import step_error_type_raised

        with pytest.raises(AssertionError):
            step_error_type_raised(_ctx(), "TypeError", "runtime", "*")

    def test_right_kind_passes(self):
        from tests.tck.steps.results import step_error_type_raised

        ctx = _ctx(error=_translate_error("RETURN foo"))
        step_error_type_raised(ctx, "SyntaxError", "compile time", "UndefinedVariable")

    def test_wrong_kind_fails(self):
        from tests.tck.steps.results import step_error_type_raised

        ctx = _ctx(error=_translate_error("RETURN foo"))
        with pytest.raises(AssertionError, match="TypeError"):
            step_error_type_raised(ctx, "TypeError", "runtime", "*")

    def test_unmapped_expected_kind_fails(self):
        from tests.tck.steps.results import step_error_type_raised

        ctx = _ctx(error=_translate_error("RETURN foo"))
        with pytest.raises(AssertionError):
            step_error_type_raised(ctx, "ArithmeticError", "runtime", "*")

    def test_step_signatures_pass_phase_and_detail(self):
        from tests.tck.steps import results

        ctx = _ctx(error=_translate_error("RETURN foo"))
        results.step_error_compile(ctx, "SyntaxError", "UndefinedVariable")
        with pytest.raises(AssertionError):
            results.step_error_compile(ctx, "SyntaxError", "VariableAlreadyBound")


# ---------------------------------------------------------------------------
# The mapping itself
# ---------------------------------------------------------------------------


class TestKindFromIVG:
    def test_parse_error_is_compile_time_syntax_error(self):
        from tests.tck.steps.errors import classify

        o = classify(_translate_error("MATCH ("))
        assert (o.kind, o.phase) == ("SyntaxError", "compile time")

    def test_tagged_detail_is_read_from_the_message(self):
        from tests.tck.steps.errors import classify

        o = classify(_translate_error("MATCH (a)-[r]-(r) RETURN r"))
        assert o.kind == "SyntaxError"
        assert o.detail in {"VariableAlreadyBound", "VariableTypeConflict"}

    @pytest.mark.parametrize("query,detail", [
        # Match3 [29]: one relationship twice in a pattern.
        ("MATCH (a)-[r]->()-[r]->(a) RETURN r", "RelationshipUniquenessViolation"),
        ("MATCH ()-[r]-(), ()-[r]-() RETURN r", "RelationshipUniquenessViolation"),
        # Match2 [11]: a node variable reused as a relationship.
        ("MATCH (r)-[r]-() RETURN r", "VariableTypeConflict"),
        ("MATCH (r)-[r]-(r) RETURN r", "VariableTypeConflict"),
        # Match6 [21]-[24]: a path variable naming something already bound.
        ("MATCH (p)-[]-() MATCH p = ()-[]-() RETURN p", "VariableAlreadyBound"),
        ("MATCH ()-[p]-() MATCH p = ()-[]-() RETURN p", "VariableAlreadyBound"),
        ("MATCH p = (p)-[]-() RETURN p", "VariableAlreadyBound"),
        ("MATCH p = ()-[p]-() RETURN p", "VariableAlreadyBound"),
        # Match1 [8]/[10], Match2 [10]/[12]: a path reused as a node or relationship.
        ("MATCH r = ()-[]-() MATCH (r) RETURN r", "VariableTypeConflict"),
        ("MATCH r = ()-[]-(), (r) RETURN r", "VariableTypeConflict"),
        ("MATCH r = ()-[]-() MATCH ()-[r]-() RETURN r", "VariableTypeConflict"),
    ])
    def test_rebinding_detail(self, query, detail):
        assert _mismatch("SyntaxError", "compile time", detail, _translate_error(query)) is None

    def test_conflicting_detail_fails(self):
        err = _translate_error("RETURN foo")
        why = _mismatch("SyntaxError", "compile time", "VariableAlreadyBound", err)
        assert why is not None and "UndefinedVariable" in why

    def test_wildcard_detail(self):
        assert _mismatch("SyntaxError", "compile time", "*", _translate_error("RETURN foo")) is None

    def test_unmappable_detail_is_matched_on_kind(self):
        from tests.tck.steps.errors import classify

        err = _translate_error("MATCH (")
        assert classify(err).detail is None
        assert _mismatch("SyntaxError", "compile time", "UnexpectedSyntax", err) is None

    def test_translator_type_error(self):
        err = _translate_error("RETURN labels(1)")
        assert _mismatch("TypeError", "compile time", "InvalidArgumentValue", err) is None

    def test_runtime_expectation_accepts_early_detection(self):
        # IVG's translator sees parameter values and folds constants, so an error the
        # TCK places at runtime may surface before any SQL runs. Accepted, not the reverse.
        err = _translate_error("RETURN labels(1)")
        assert _mismatch("TypeError", "runtime", "InvalidArgumentValue", err) is None

    def test_compile_time_expectation_rejects_runtime_error(self):
        err = _sql_error(
            -149, "SQL Function encountered an error", "ServerLoop - Query Open()",
            "SQL Function SQLUSER.CYPHERFN_IVGTYPEERROR failed with error:  SQLCODE=-400,"
            "%msg=Map element access by non-string",
        )
        from tests.tck.steps.errors import classify

        o = classify(err)
        assert (o.kind, o.phase) == ("TypeError", "runtime")
        assert o.detail == "MapElementAccessByNonString"
        assert _mismatch("TypeError", "runtime", "MapElementAccessByNonString", err) is None
        why = _mismatch("TypeError", "compile time", "MapElementAccessByNonString", err)
        assert why is not None and "runtime" in why

    def test_value_error_with_detail_is_argument_error(self):
        err = _translate_error("RETURN range(1, 2, 0)")
        assert _mismatch("ArgumentError", "runtime", "NumberOutOfRange", err) is None

    def test_value_error_without_detail_has_no_kind(self):
        from tests.tck.steps.errors import classify

        assert classify(_raised(ValueError("bad"))).kind is None

    def test_tagged_udf_message(self):
        err = _sql_error(
            -149, "SQL Function encountered an error", "ServerLoop - Query Fetch",
            "SQL Function IVG.PERCENTILE_PCONT failed with error:  SQLCODE=-400,"
            "%msg=ArgumentError: NumberOutOfRange: percentile must be between 0.0 and 1.0",
        )
        assert _mismatch("ArgumentError", "runtime", "NumberOutOfRange", err) is None

    def test_untagged_udf_failure_has_no_kind(self):
        from tests.tck.steps.errors import classify

        err = _sql_error(
            -149, "SQL Function encountered an error", "ServerLoop - Query Open()",
            "SQL Function SQLUSER.JSON_VALUE failed with error:  SQLCODE=-400,%msg=",
        )
        assert classify(err).kind is None

    def test_accidental_python_type_error_has_no_kind(self):
        import json

        from tests.tck.steps.errors import classify

        try:
            json.dumps(object())
        except TypeError as exc:
            assert classify(exc).kind is None
        else:  # pragma: no cover
            raise AssertionError("json.dumps(object()) should raise")

    def test_type_error_not_raised_by_ivg_has_no_kind(self):
        from tests.tck.steps.errors import classify

        assert classify(TypeError("bad type")).kind is None
        assert classify(_raised(TypeError("bad type"))).kind is None

    def test_generic_runtime_error_has_no_kind(self):
        from tests.tck.steps.errors import classify

        assert classify(_raised(RuntimeError("IRIS connection is stale"))).kind is None

    def test_known_untagged_messages(self):
        from tests.tck.steps.errors import classify

        cases = {
            "Procedure not found: 'x'. Available: none registered": (
                "ProcedureError", "ProcedureNotFound"),
            "Cannot merge on null property value: property 'k' has value null": (
                "SemanticError", "MergeReadOwnWrites"),
        }
        for msg, want in cases.items():
            o = classify(_ivg_raised(ValueError(msg)))
            assert (o.kind, o.detail) == want, msg

    def test_entity_not_found(self):
        from iris_vector_graph.cypher.translator import EntityNotFoundError
        from tests.tck.steps.errors import classify

        o = classify(_ivg_raised(EntityNotFoundError("DeletedEntityAccess: n was deleted")))
        assert (o.kind, o.detail) == ("EntityNotFound", "DeletedEntityAccess")

    def test_parameter_missing(self):
        from tests.tck.steps.errors import classify

        o = classify(_ivg_raised(KeyError("Procedure p: missing parameter 'a' (MissingParameter)")))
        assert (o.kind, o.detail) == ("ParameterMissing", "MissingParameter")

    def test_store_keeps_the_whole_sql_error(self):
        # The UDF's %msg -- where IVG names the Cypher error -- sits past character 200
        # of the IRIS text; cutting it there left only "SQLCODE=-40" (spec 229 US3).
        from iris_vector_graph.stores.iris_sql_store import IRISGraphStore
        from tests.tck.steps.errors import classify

        err = _sql_error(
            -149, "SQL Function encountered an error", "ServerLoop - Query Open()",
            "SQL Function SQLUSER.CYPHERFN_IVGTYPEERROR failed with error:  SQLCODE=-400,"
            "%msg=Map element access by non-string",
        )
        conn = MagicMock()
        conn.cursor.return_value.execute.side_effect = err
        store = IRISGraphStore.__new__(IRISGraphStore)
        store.conn = conn
        result = store.execute_sql("SELECT 1", [])
        assert result.error == str(err)
        o = classify(result.error)
        assert (o.kind, o.detail) == ("TypeError", "MapElementAccessByNonString")

    def test_plain_string_is_classified(self):
        from tests.tck.steps.errors import classify

        assert classify(str(SQL_119)).kind == "ConstraintVerificationFailed"
        assert classify("something went wrong").kind is None


def _ivg_raised(exc: BaseException, where: str = "cypher/translator.py") -> BaseException:
    """`exc` as if raised by a `raise` statement inside the iris_vector_graph package."""
    import iris_vector_graph

    pkg = iris_vector_graph.__file__.rsplit("/", 1)[0]
    src = "def _f(e):\n    raise e\n"
    code = compile(src, f"{pkg}/{where}", "exec")
    ns: dict = {}
    exec(code, ns)  # noqa: S102 - builds a frame whose filename is inside the package
    try:
        ns["_f"](exc)
    except BaseException as caught:  # noqa: BLE001
        return caught
    raise AssertionError("unreachable")


# ---------------------------------------------------------------------------
# T029: the runner refuses a deferral entry with no reason (FR-011, SC-006)
# ---------------------------------------------------------------------------


class TestDeferralReasons:
    def test_entry_without_reason_is_refused(self, tmp_path):
        from tests.tck.conftest import load_wip_registry

        wip = tmp_path / "wip.txt"
        wip.write_text("clauses/match/Match1.feature::Scenario: [3] no reason given\n")
        with pytest.raises(ValueError, match="reason"):
            load_wip_registry(str(wip))

    def test_empty_reason_is_refused(self, tmp_path):
        from tests.tck.conftest import load_wip_registry

        wip = tmp_path / "wip.txt"
        wip.write_text("# reason:\nclauses/match/Match1.feature::Scenario: [3] x\n")
        with pytest.raises(ValueError, match="reason"):
            load_wip_registry(str(wip))

    def test_non_reason_comment_is_not_a_reason(self, tmp_path):
        from tests.tck.conftest import load_wip_registry

        wip = tmp_path / "wip.txt"
        wip.write_text("# just a header\nclauses/match/Match1.feature::Scenario: [3] x\n")
        with pytest.raises(ValueError, match="reason"):
            load_wip_registry(str(wip))

    def test_entry_with_reason_is_loaded(self, tmp_path):
        from tests.tck.conftest import load_wip_registry

        wip = tmp_path / "wip.txt"
        wip.write_text(
            "# header\n\n# reason: not supported\n"
            "clauses/match/Match1.feature::Scenario: [3] x\n"
        )
        assert load_wip_registry(str(wip)) == {"clauses/match/Match1.feature::Scenario: [3] x"}

    def test_checked_in_deferral_file_loads(self):
        from tests.tck.conftest import load_wip_registry

        load_wip_registry()  # raises if any entry lacks a reason
