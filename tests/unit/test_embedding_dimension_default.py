"""One default embedding dimension for the whole library.

Reported 2026-09-18: four entry points carried three different literals, so the
width a table ended up with depended on which one created it, and nothing
downstream recorded which one that was.

| Site                                          | Was     |
| --------------------------------------------- | ------- |
| `schema.py` `get_base_schema_sql`             | 768     |
| `cypher_api.py` `AdminSchemaRequest`          | 768     |
| `status.py` `EngineStatus`                    | 768     |
| `schema.py` `get_procedures_sql_list`         | **1000** |
| `cli.py` `--embedding-dim`                    | 768     |

The 1000 is the damaging one: `get_procedures_sql_list` compiles the `kg_KNN_VEC`
retrieval procedure with a `DECLARE ... VECTOR(DOUBLE, N)` clause, so a caller
who took the default got procedures declared at 1000 against tables created at
768. `initialize_schema` always passes the real dimension, which is why this
stayed quiet.
"""

import ast
import inspect
import pathlib
import re

from iris_vector_graph.constants import DEFAULT_EMBEDDING_DIMENSION
from iris_vector_graph.schema import GraphSchema


def _default_of(func, param):
    return inspect.signature(func).parameters[param].default


def test_there_is_one_constant_and_it_is_768():
    """768 is what shipped, so the single value has to stay 768 — collapsing the
    defaults must not silently change the width of anyone's next table."""
    assert DEFAULT_EMBEDDING_DIMENSION == 768


def test_get_base_schema_sql_uses_the_constant():
    assert (
        _default_of(GraphSchema.get_base_schema_sql, "embedding_dimension")
        == DEFAULT_EMBEDDING_DIMENSION
    )


def test_get_procedures_sql_list_no_longer_takes_a_width_at_all():
    """The one site that disagreed — now the parameter is gone.

    3.1.0 collapsed this default from 1000 to 768, on the theory that the parameter would
    one day be wired in. Spec 226 measured what wiring it in would do (ADR-0005: IRIS
    reshapes the query vector and returns a plausible wrong score instead of raising) and
    deprecated the parameter. Spec 227 removed it in 4.0.0: a parameter that is accepted,
    warned about and then ignored is a worse contract than one that does not exist, because
    passing it reads as having configured something.
    """
    assert "embedding_dimension" not in inspect.signature(
        GraphSchema.get_procedures_sql_list
    ).parameters


def test_the_ddl_declares_the_constant():
    ddl = GraphSchema.get_base_schema_sql()

    assert ddl.count(f"VECTOR(DOUBLE, {DEFAULT_EMBEDDING_DIMENSION})") == 3
    assert "VECTOR(DOUBLE, 1000)" not in ddl


def test_the_procedure_leaves_to_vector_unlengthened():
    """Why the parameter could never have been wired in.

    `TO_VECTOR(:queryInput, DOUBLE)` carries no length on purpose (ADR-0005): with one,
    IRIS reshapes a mismatched query vector and returns a plausible wrong score instead
    of raising. Passing a width was never going to make the procedure stricter, which is
    why spec 227 removed the parameter instead of implementing it. If someone adds a
    length here, this fails and ADR-0005 has to be reopened with it.
    """
    sql = "\n".join(GraphSchema.get_procedures_sql_list())

    assert "TO_VECTOR(:queryInput, DOUBLE)" in sql
    assert "VECTOR(DOUBLE, 1000)" not in sql
    assert "TO_VECTOR(:queryInput, DOUBLE," not in sql


def test_engine_status_dataclass_uses_the_constant():
    from iris_vector_graph.status import EngineStatus

    assert (
        EngineStatus.__dataclass_fields__["embedding_dimension"].default
        == DEFAULT_EMBEDDING_DIMENSION
    )


def test_admin_schema_request_uses_the_constant():
    try:
        from iris_vector_graph.cypher_api import AdminSchemaRequest
    except Exception as exc:  # pragma: no cover - FastAPI not installed in every venv
        import pytest

        pytest.skip(f"cypher_api unavailable: {exc}")

    assert (
        AdminSchemaRequest.model_fields["embedding_dimension"].default
        == DEFAULT_EMBEDDING_DIMENSION
    )


_DIMENSION_LITERAL = re.compile(r"embedding[-_]dim(?:ension)?[^=\n]*[=:]\s*(\d+)")


def _docstring_lines(source: str) -> set[int]:
    """Every line number occupied by a docstring in ``source``.

    A docstring is a bare string expression, so this finds `ast.Expr` nodes wrapping a
    string constant — a module, class or function docstring — and nothing else. A string
    that is part of an expression (a dict key, an argument, an f-string in a call) is not
    matched, because those sit next to code that genuinely could declare a default.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover - the package has to parse for anything to run
        return set()
    lines: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            lines.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return lines


def _dimension_literal_offenders(source: str) -> list[tuple[int, str]]:
    """Lines of ``source`` that declare a dimension default other than the constant.

    Prose is not a default. A comment cannot carry one, and neither can a docstring: the
    `_diagnose_vector_write` docstring explains the mismatch it diagnoses by naming an
    engine "built with ``embedding_dimension=128``", which is a description of a caller's
    mistake, not a fifth default. Only code lines are scanned.
    """
    skip = _docstring_lines(source)
    offenders = []
    for lineno, line in enumerate(source.splitlines(), start=1):
        if lineno in skip or line.lstrip().startswith("#"):
            continue
        match = _DIMENSION_LITERAL.search(line)
        if match and int(match.group(1)) != DEFAULT_EMBEDDING_DIMENSION:
            offenders.append((lineno, line.strip()))
    return offenders


def test_the_scan_still_catches_a_real_fifth_default():
    source = "def f(embedding_dimension: int = 123):\n    return embedding_dimension\n"
    assert [lineno for lineno, _ in _dimension_literal_offenders(source)] == [1]


def test_the_scan_ignores_prose_in_a_docstring():
    """A docstring explaining a width mismatch is not a declaration of one."""
    source = (
        "def f():\n"
        '    """An engine built with ``embedding_dimension=128``\n'
        '    against a 768-wide column is refused.\n'
        '    """\n'
        "    return None\n"
    )
    assert _dimension_literal_offenders(source) == []


def test_the_scan_ignores_a_comment():
    source = "# embedding_dimension=0 now warns\nx = 1\n"
    assert _dimension_literal_offenders(source) == []


def test_the_scan_still_reads_a_dict_entry_beside_a_string_key():
    """A string key is not a docstring, so the code around it stays in scope."""
    source = 'DEFAULTS = {"embedding_dimension": 512}\n'
    assert [lineno for lineno, _ in _dimension_literal_offenders(source)] == [1]


def test_no_module_carries_its_own_dimension_literal():
    """A scan, so a fifth default cannot be added without this failing."""
    pkg = pathlib.Path(__file__).resolve().parents[2] / "iris_vector_graph"
    assert pkg.is_dir(), f"package not found next to tests (looked for {pkg})"

    offenders = []
    for path in sorted(pkg.rglob("*.py")):
        for lineno, line in _dimension_literal_offenders(path.read_text()):
            offenders.append(f"{path.relative_to(pkg)}:{lineno}: {line}")

    assert not offenders, "hardcoded embedding dimension defaults:\n" + "\n".join(offenders)
