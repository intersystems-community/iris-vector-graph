"""R043: the checker behind tests/docs/test_docs_consistent.py, on small hand-written docs.

Each case names something that exists in the repo and something that does not; the expected findings are the
ones a reader would hit, worked out by hand.
"""

from __future__ import annotations

import textwrap

import pytest

from tests.docs.consistency import build_context, check_doc


@pytest.fixture(scope="module")
def ctx():
    return build_context()


def _found(ctx, text, path="docs/EXAMPLE.md", reader=False):
    return sorted((f.kind, f.item) for f in check_doc(path, textwrap.dedent(text), ctx, reader=reader))


def test_relative_links_resolve_from_the_doc_directory(ctx):
    text = """\
    See [the guide](USER_GUIDE.md), [the readme](../README.md#install), [web](https://example.com),
    [anchor](#top) and [gone](NO_SUCH_PAGE.md).
    """
    assert _found(ctx, text) == [("link", "NO_SUCH_PAGE.md")]


def test_link_into_an_unshipped_directory_is_reported(ctx):
    # The prefix comes from docs.toml so this file names no spec path (test_ci_sees_what_tests_read scans it).
    target = f"../{ctx.unshipped[0]}some-spec/plan.md"
    assert _found(ctx, f"[spec]({target})\n") == [("unshipped", target)]


def test_links_inside_code_are_not_links(ctx):
    text = """\
    `[x](nowhere.md)`

    ```python
    s = "[y](nowhere.md)"
    ```
    """
    assert _found(ctx, text) == []


def test_repo_paths_in_inline_code_must_exist(ctx):
    text = "Read `iris_vector_graph/engine.py` and `iris_vector_graph/no_such_module.py`.\n"
    assert _found(ctx, text) == [("path", "iris_vector_graph/no_such_module.py")]


def test_classes_from_iris_src(ctx):
    text = """\
    `Graph.KG.Traversal` exists; `Graph.KG.NoSuchClass` does not; the package `Graph.KG.Cypher` exists;
    `IVG.CypherEngine` exists. `Graph.KG.*` is a wildcard. `Graph.KG.nodes` is the class of a table.
    """
    assert _found(ctx, text) == [("class", "Graph.KG.NoSuchClass")]


def test_class_member_must_be_defined(ctx):
    text = "`##class(Graph.KG.Eraser).NoSuchMethod()` and `Graph.KG.Traversal.cls`.\n"
    assert _found(ctx, text) == [("member", "Graph.KG.Eraser.NoSuchMethod")]


def test_global_root_after_a_caret_is_not_a_class(ctx):
    text = "`^IVG.AlgoCache` is not a class.\n"
    assert ("class", "IVG.AlgoCache") not in _found(ctx, text)


def test_engine_methods_from_irisgraphengine(ctx):
    text = """\
    ```python
    engine.create_node("a")
    engine.cypher
    engine.no_such_method()
    IRISGraphEngine.from_connect("localhost")
    ```
    The module is `iris_vector_graph/engine.py`.
    """
    assert _found(ctx, text) == [("method", "engine.no_such_method")]


def test_global_roots_must_appear_in_the_code(ctx):
    text = "`^KG(\"out\")`, `^NKG`, `^NOSUCHGLOBAL(1)`, `^||ppg`, and 2^31 is a number.\n"
    assert _found(ctx, text) == [("global", "^NOSUCHGLOBAL")]


def test_env_vars_must_appear_in_the_code(ctx):
    text = "Set `IVG_PORT` or `IVG_NO_SUCH_SETTING`.\n"
    assert _found(ctx, text) == [("env", "IVG_NO_SUCH_SETTING")]


def test_reader_doc_ports_and_containers_match_compose(ctx):
    text = """\
    ```python
    conn = iris.connect("localhost", 1972, "USER", "_SYSTEM", "SYS")
    conn = iris.connect("localhost", 31972, "USER", "_SYSTEM", "SYS")
    ```
    Open http://localhost:52773/csp/sys/UtilHome.csp.

    ```bash
    docker exec -it iris_vector_graph iris session IRIS
    docker exec -it ivg-iris iris session IRIS
    ```
    """
    assert _found(ctx, text, reader=True) == [("container", "ivg-iris"), ("port", "31972")]
    assert _found(ctx, text, reader=False) == []


def test_reader_doc_scripts_exist_and_are_not_dev_scripts(ctx):
    text = """\
    ```bash
    python scripts/quickstart_e2e.py
    python scripts/no_such_script.py
    scripts/ci-parity.sh
    ```
    """
    assert _found(ctx, text, reader=True) == [
        ("dev-script", "scripts/ci-parity.sh"),
        ("script", "scripts/no_such_script.py"),
    ]


def test_class_storage_globals_and_global_families(ctx):
    text = "`^Graph.KG.LedgerRevisionD`, `^IVG.*`, `^Graph.KG.NoSuchClassD`.\n"
    assert _found(ctx, text) == [("global", "^Graph.KG.NoSuchClassD")]


def test_a_global_named_without_its_caret_is_not_a_class(ctx):
    assert _found(ctx, 'native.set("off", "IVG.CypherGen", "mode")\n') == []


def test_paths_into_a_submodule_count_as_present(ctx):
    # tests/tck/features links into vendor/opencypher, which only a full checkout fetches.
    assert _found(ctx, "Features live in `tests/tck/features` and `vendor/opencypher/tck/features`.\n") == []


def test_compose_host_ports_written_as_variables_count_by_their_default(tmp_path):
    """4.6.0 R022: `"${IVG_IRIS_PORT:-1972}:1972"` publishes 1972 unless the reader sets the variable."""
    from tests.docs.consistency import _compose

    (tmp_path / "docker-compose.yml").write_text(textwrap.dedent("""\
        services:
          iris:
            image: x
            ports:
              - "${IVG_IRIS_PORT:-1972}:1972"
              - '${IVG_WEB_PORT:-52773}:52773' # portal
              - "8080:80"
              - "127.0.0.1:9000:9000"
        volumes:
          d: {}
        """))
    ports, containers = _compose(tmp_path)
    assert ports == {1972, 52773, 8080, 9000}
    assert containers == {"iris"}
