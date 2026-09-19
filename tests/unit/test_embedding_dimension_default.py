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

import inspect

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


def test_get_procedures_sql_list_no_longer_defaults_to_1000():
    """The one site that disagreed. A procedure declared at 1000 against a 768
    column fails at insert time, far from the call that chose the number."""
    assert (
        _default_of(GraphSchema.get_procedures_sql_list, "embedding_dimension")
        == DEFAULT_EMBEDDING_DIMENSION
    )


def test_the_ddl_declares_the_constant():
    ddl = GraphSchema.get_base_schema_sql()

    assert ddl.count(f"VECTOR(DOUBLE, {DEFAULT_EMBEDDING_DIMENSION})") == 3
    assert "VECTOR(DOUBLE, 1000)" not in ddl


def test_get_procedures_sql_list_does_not_actually_use_its_dimension():
    """Pins what the parameter really does, which is nothing.

    The docstring used to claim it fed a DECLARE clause in `kg_KNN_VEC`; the
    generated procedure says `TO_VECTOR(:queryInput, DOUBLE)` with no length.
    That is why the 1000/768 disagreement never reached SQL. If someone wires
    the width in, this test fails and the docstring has to be corrected with it.
    """
    at_default = "\n".join(GraphSchema.get_procedures_sql_list())
    at_384 = "\n".join(GraphSchema.get_procedures_sql_list(embedding_dimension=384))

    assert at_default == at_384
    assert "TO_VECTOR(:queryInput, DOUBLE)" in at_default
    assert "VECTOR(DOUBLE, 1000)" not in at_default


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


def test_no_module_carries_its_own_dimension_literal():
    """A scan, so a fifth default cannot be added without this failing."""
    import pathlib
    import re

    pkg = pathlib.Path(__file__).resolve().parents[2] / "iris_vector_graph"
    assert pkg.is_dir(), f"package not found next to tests (looked for {pkg})"

    pattern = re.compile(r"embedding[-_]dim(?:ension)?[^=\n]*[=:]\s*(\d+)")
    offenders = []
    for path in sorted(pkg.rglob("*.py")):
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            match = pattern.search(line)
            if match and int(match.group(1)) != DEFAULT_EMBEDDING_DIMENSION:
                offenders.append(f"{path.relative_to(pkg)}:{lineno}: {line.strip()}")

    assert not offenders, "hardcoded embedding dimension defaults:\n" + "\n".join(offenders)
