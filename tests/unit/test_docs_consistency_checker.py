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
    docker compose exec iris iris session IRIS
    docker exec -it iris iris session IRIS
    docker exec -it ivg-iris iris session IRIS
    docker exec -it iris_vector_graph iris session IRIS
    ```
    """
    # docker-compose.yml names no container (4.6.0 R022): `iris` is its service, and the old fixed name is gone.
    assert _found(ctx, text, reader=True) == [
        ("container", "iris_vector_graph"), ("container", "ivg-iris"), ("port", "31972"),
    ]
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


def test_a_path_the_wheel_force_includes_counts_as_present(ctx):
    # pyproject's [tool.hatch.build.targets.wheel.force-include] puts docs/dev/arno-reader-proof.json at
    # iris_vector_graph/_data/arno-reader-proof.json in the wheel (R104): the doc names where an installed IVG reads it.
    assert _found(ctx, "The wheel carries `iris_vector_graph/_data/arno-reader-proof.json`.\n") == []
    assert _found(ctx, "The wheel carries `iris_vector_graph/_data/no-such-file.json`.\n") == [
        ("path", "iris_vector_graph/_data/no-such-file.json")]


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


# ── spec 262 moved classes out of the wheel: where a doc may still name them ────────────────────────────────────

DEV_ONLY = "`Graph.KG.BenchFormat`, `##class(Graph.KG.Benchmark).Run()` and `Graph.KG.BenchFormat.Seed`.\n"
DEV_ONLY_FOUND = [("class", "Graph.KG.BenchFormat"), ("class", "Graph.KG.BenchFormat.Seed"),
                  ("class", "Graph.KG.Benchmark")]


@pytest.mark.parametrize("doc", ["docs/dev/EXAMPLE.md", "docs/migration/EXAMPLE.md", "CONTRIBUTING.md"])
def test_dev_only_classes_exist_for_contributor_docs(ctx, doc):
    """(a) iris_src/dev classes are real for the docs a contributor reads; their members are still checked."""
    assert _found(ctx, DEV_ONLY, path=doc) == []
    assert _found(ctx, "`Graph.KG.BenchFormat.NoSuchMethod`\n", path=doc) == [
        ("member", "Graph.KG.BenchFormat.NoSuchMethod")]


@pytest.mark.parametrize("doc,reader", [("docs/USER_GUIDE.md", True), ("docs/EXAMPLE.md", False),
                                        ("README.md", True)])
def test_a_reader_doc_naming_a_dev_only_class_is_flagged(ctx, doc, reader):
    assert _found(ctx, DEV_ONLY, path=doc, reader=reader) == DEV_ONLY_FOUND


def test_retired_classes_and_paths_are_known_in_migration_and_dev_notes(ctx):
    """(b) the legacy manifest's retired names (no replacement) are history in docs/migration and docs/dev/notes."""
    from dataclasses import replace

    no_dev = replace(ctx, dev_classes=set(), dev_packages=set(), dev_class_files={})
    retired = "`Graph.KG.Benchmark` and `iris_src/src/User.PageRankEmbedded.cls`.\n"
    for doc in ("docs/migration/EXAMPLE.md", "docs/dev/notes/EXAMPLE.md"):
        assert _found(no_dev, retired, path=doc) == [], doc
    assert _found(no_dev, retired, path="docs/dev/EXAMPLE.md") == [
        ("class", "Graph.KG.Benchmark"), ("path", "iris_src/src/User.PageRankEmbedded.cls")]
    assert _found(no_dev, retired, path="docs/EXAMPLE.md") == [
        ("class", "Graph.KG.Benchmark"), ("path", "iris_src/src/User.PageRankEmbedded.cls")]
    # Moved into the IVG Schema, not retired: in a migration guide its old file is a missing path.
    assert _found(no_dev, "`iris_src/src/User.funcSTRSPLIT.cls`\n", path="docs/migration/EXAMPLE.md") == [
        ("path", "iris_src/src/User.funcSTRSPLIT.cls")]


HISTORY = "`iris_src/src/User.funcSTRSPLIT.cls`, `iris_src/src/User.Exec.cls`, `iris_src/src/User.NeverWas.cls`.\n"


@pytest.mark.parametrize("tags", ["checkout", "no git"])
def test_paths_from_a_past_release_are_history_in_dev_notes_only(ctx, tags):
    """(c) a path a past release shipped is history in docs/dev/notes, with or without git: with no git (ci-parity's
    copy) or no tags (CI's shallow checkout) the legacy manifest says which files 3.2.0 to 4.5.1 shipped."""
    from dataclasses import replace

    c = ctx if tags == "checkout" else replace(ctx, release_tags=())
    assert _found(c, HISTORY, path="docs/dev/notes/EXAMPLE.md") == [("path", "iris_src/src/User.NeverWas.cls")]
    for doc in ("docs/dev/EXAMPLE.md", "docs/EXAMPLE.md"):
        assert ("path", "iris_src/src/User.funcSTRSPLIT.cls") in _found(c, HISTORY, path=doc), doc


def test_release_tags_are_the_releases_the_manifest_covers(ctx):
    """(c) in a checkout with tags, a file is history when one of v3.2.0 … v4.5.1 has it (`git cat-file -e`)."""
    from dataclasses import replace

    from tests.docs.consistency import _release_tags

    tags = _release_tags(ctx.repo)
    if not tags:
        pytest.skip("no git or no release tags here (ci-parity's copy, CI's shallow checkout): manifest only")
    assert tags[0] == "v3.2.0" and tags[-1] == "v4.5.1", tags  # the manifest reaches back to 3.2.0 (4.6.0 R119)
    # Deleted in 4.6.0 and not an IRIS class, so only the tag knows it.
    assert _found(ctx, "`docs/python/PYTHON_SDK.md`\n", path="docs/dev/notes/EXAMPLE.md") == []
    assert _found(replace(ctx, release_tags=()), "`docs/python/PYTHON_SDK.md`\n", path="docs/dev/notes/EXAMPLE.md") == [
        ("path", "docs/python/PYTHON_SDK.md")]


def test_no_release_tags_outside_a_git_checkout(tmp_path):
    from tests.docs.consistency import _release_tags

    assert _release_tags(tmp_path) == ()


# ── 4.6.0 R045: names the checker could not see, and names a doc declares it cannot check ───────────────────────


def test_a_process_private_global_is_not_a_class(ctx):
    """`^||IVG.LedgerStage` is a process-private global (gone when the process ends), not the class IVG.LedgerStage."""
    assert _found(ctx, "Chunks go to `^||IVG.LedgerStage` and `^||IVG.NoSuchScratch(k)`.\n") == []


def test_an_attribute_the_engine_reads_with_getattr_exists(ctx):
    """`engine.cypher_shadow = True` turns shadow mode on: _engine/query.py reads it with getattr(self, ...)."""
    assert _found(ctx, "Set `engine.cypher_shadow = True` or `engine.no_such_switch = True`.\n") == [
        ("method", "engine.no_such_switch")]


def test_the_class_iris_generates_for_a_ddl_procedure_exists(ctx):
    """CREATE PROCEDURE Graph_KG.kg_RRF_FUSE makes IRIS generate class Graph.KG.prockgRRFFUSE (measured: the
    namespace's %Dictionary lists prockgKNNVEC, prockgRRFFUSE and prockgTXT). A function makes no such class."""
    assert _found(ctx, "`Graph.KG.prockgRRFFUSE`, `Graph.KG.prockgKNNVEC`, `Graph.KG.procJSONVALUE`.\n") == [
        ("class", "Graph.KG.procJSONVALUE")]


UNCHECKED = """\
<!-- doc-check: unchecked names="^IVGSpike, engine.grant_access" reason="the spike's scratch global; a proposal" -->

The spike wrote `^IVGSpike(1)` and proposes `engine.grant_access(role)`; `^NOSUCHGLOBAL` is not listed.
"""


def test_a_doc_may_list_names_it_cannot_check_with_a_reason(ctx):
    assert _found(ctx, UNCHECKED, path="docs/dev/notes/EXAMPLE.md") == [("global", "^NOSUCHGLOBAL")]


@pytest.mark.parametrize("marker,problem", [
    ('<!-- doc-check: unchecked names="^IVGSpike" -->', "reason"),
    ('<!-- doc-check: unchecked names="^IVGSpike" reason="" -->', "reason"),
    ('<!-- doc-check: unchecked reason="why" -->', "names"),
    ('<!-- doc-check: ignore names="^IVGSpike" reason="why" -->', "unknown"),
])
def test_a_malformed_unchecked_marker_is_a_finding(ctx, marker, problem):
    found = _found(ctx, f"{marker}\n\n`^IVGSpike`\n", path="docs/dev/notes/EXAMPLE.md")
    markers = [item for kind, item in found if kind == "marker"]
    assert len(markers) == 1 and problem in markers[0], found
    assert ("global", "^IVGSpike") in found  # a malformed marker excuses nothing


def test_a_listed_name_the_checker_finds_is_stale(ctx):
    """A listed name that exists, or that the doc no longer names, is reported so the list cannot outlive its use."""
    text = '<!-- doc-check: unchecked names="^KG, ^IVGSpike, ^NeverNamed" reason="why" -->\n\n`^KG` and `^IVGSpike`.\n'
    assert _found(ctx, text, path="docs/dev/notes/EXAMPLE.md") == [
        ("marker", "stale unchecked name: ^KG"), ("marker", "stale unchecked name: ^NeverNamed")]


def test_a_reader_doc_may_not_list_unchecked_names(ctx):
    """What a reader is told to use must exist: in a reader doc (docs.toml [[doc]]) the marker is refused."""
    found = _found(ctx, UNCHECKED, path="docs/USER_GUIDE.md", reader=True)
    assert ("marker", "doc-check: unchecked is not allowed in a reader doc") in found
    assert ("global", "^IVGSpike") in found and ("method", "engine.grant_access") in found


def test_an_unchecked_name_never_excuses_a_broken_link(ctx):
    text = '<!-- doc-check: unchecked names="GONE.md" reason="why" -->\n\n[gone](GONE.md)\n'
    found = _found(ctx, text, path="docs/dev/notes/EXAMPLE.md")
    assert ("link", "GONE.md") in found and ("marker", "stale unchecked name: GONE.md") in found
