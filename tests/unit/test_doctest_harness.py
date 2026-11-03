"""R040: the doc-test harness's parser and comparators (tests/docs/harness.py).

Written before the harness. The harness runs the code blocks in the reader docs and compares the answers the doc
states; these tests pin how it reads a document: which blocks it finds, what each marker means, where a `# →`
expectation belongs, and that a block it cannot classify is an error rather than a silent pass.

Every expected value here is worked out by hand from the markdown in the test, not taken from the harness.
"""

from __future__ import annotations

import sys
import textwrap

import pytest

from tests.docs import harness
from tests.docs.harness import parse_document


def _doc(text: str):
    return parse_document(textwrap.dedent(text))


def _kinds(doc):
    return [b.kind for b in doc.blocks]


# ── fenced-block extraction by language ─────────────────────────────────────


def test_blocks_are_found_in_order_with_language_and_line():
    doc = _doc(
        """\
        # Title

        ```python
        x = 1
        ```

        Some prose.

        ```sql
        SELECT 1
        ```

        ```bash
        echo hi
        ```
        """
    )
    assert [(b.lang, b.line, b.text) for b in doc.blocks] == [
        ("python", 3, "x = 1\n"),
        ("sql", 9, "SELECT 1\n"),
        ("bash", 13, "echo hi\n"),
    ]
    assert _kinds(doc) == ["python", "sql", "bash"]
    assert doc.errors == []


def test_language_aliases_map_to_one_kind():
    doc = _doc(
        """\
        ```py
        a = 1
        ```
        ```Python3
        b = 2
        ```
        ```sh
        ls
        ```
        ```shell
        ls
        ```
        ```cos
        write 1
        ```
        """
    )
    assert _kinds(doc) == ["python", "python", "bash", "bash", "objectscript"]


def test_indented_fence_in_a_list_item_is_dedented():
    doc = _doc(
        """\
        1. Step one:

           ```python
           y = 2
           if y:
               y += 1
           ```
        """
    )
    assert doc.blocks[0].text == "y = 2\nif y:\n    y += 1\n"


def test_longer_fence_contains_a_shorter_one():
    doc = _doc(
        """\
        ````text
        ```python
        not a block
        ```
        ````
        """
    )
    assert len(doc.blocks) == 1
    assert doc.blocks[0].lang == "text"
    assert "not a block" in doc.blocks[0].text


def test_unclosed_fence_is_an_error():
    doc = _doc(
        """\
        ```python
        x = 1
        """
    )
    assert any("never closed" in e.message for e in doc.errors)


def test_data_languages_are_shown_not_run():
    doc = _doc(
        """\
        ```json
        {"a": 1}
        ```
        ```text
        plain
        ```
        """
    )
    assert _kinds(doc) == ["data", "data"]
    assert all(not b.problems for b in doc.blocks)


def test_terminal_block_detected_by_prompt():
    doc = _doc(
        """\
        ```
        USER> write 1+1
        2
        ```
        ```objectscript
        IVG460DOC>write "a"
        a
        ```
        """
    )
    assert _kinds(doc) == ["terminal", "terminal"]


def test_objectscript_without_prompts_is_commands():
    doc = _doc(
        """\
        ```objectscript
        set x = 1
        write x
        ```
        """
    )
    assert _kinds(doc) == ["objectscript"]


# ── unclassifiable blocks are errors ────────────────────────────────────────


def test_unlabelled_block_without_prompts_is_unclassifiable():
    doc = _doc(
        """\
        ```
        some text with no language
        ```
        """
    )
    (block,) = doc.blocks
    assert block.kind == "unclassifiable"
    assert block.problems and "no language" in block.problems[0]


def test_unknown_language_is_unclassifiable():
    doc = _doc(
        """\
        ```cypher
        MATCH (n) RETURN n
        ```
        """
    )
    (block,) = doc.blocks
    assert block.kind == "unclassifiable"
    assert "cypher" in block.problems[0]


def test_objectscript_class_definition_cannot_run_as_commands():
    doc = _doc(
        """\
        ```objectscript
        Class Demo.Thing Extends %Persistent
        {
        }
        ```
        """
    )
    (block,) = doc.blocks
    assert block.kind == "unclassifiable"
    assert "class definition" in block.problems[0]


def test_skip_marker_excuses_an_unclassifiable_block():
    doc = _doc(
        """\
        <!-- doc-test: skip reason="shown inside the python example above" -->
        ```cypher
        MATCH (n) RETURN n
        ```
        """
    )
    (block,) = doc.blocks
    assert block.skip_reason == "shown inside the python example above"
    assert block.problems == []
    assert doc.errors == []


# ── markers ─────────────────────────────────────────────────────────────────


def test_skip_marker_with_reason_applies_to_the_next_block_only():
    doc = _doc(
        """\
        <!-- doc-test: skip reason="needs the Arno library" -->

        ```python
        engine.arno_status()
        ```

        ```python
        z = 3
        ```
        """
    )
    assert [b.skip_reason for b in doc.blocks] == ["needs the Arno library", None]
    assert doc.errors == []


def test_skip_marker_without_reason_is_an_error():
    doc = _doc(
        """\
        <!-- doc-test: skip -->
        ```python
        x = 1
        ```
        """
    )
    assert any("reason" in e.message for e in doc.errors)


def test_skip_marker_with_empty_reason_is_an_error():
    doc = _doc(
        """\
        <!-- doc-test: skip reason="" -->
        ```python
        x = 1
        ```
        """
    )
    assert any("reason" in e.message for e in doc.errors)


def test_unknown_marker_is_an_error():
    doc = _doc(
        """\
        <!-- doc-test: maybe -->
        ```python
        x = 1
        ```
        """
    )
    assert any("unknown doc-test marker" in e.message for e in doc.errors)


def test_marker_followed_by_prose_is_an_error():
    doc = _doc(
        """\
        <!-- doc-test: skip reason="x" -->
        Prose in between.

        ```python
        x = 1
        ```
        """
    )
    assert any("not directly before a fenced block" in e.message for e in doc.errors)
    assert doc.blocks[0].skip_reason is None


def test_two_markers_on_one_block_is_an_error():
    doc = _doc(
        """\
        <!-- doc-test: run -->
        <!-- doc-test: skip reason="x" -->
        ```bash
        echo hi
        ```
        """
    )
    assert any("one doc-test marker" in e.message for e in doc.errors)


def test_marker_inside_a_fenced_block_is_content():
    doc = _doc(
        """\
        ```text
        <!-- doc-test: skip reason="x" -->
        ```
        ```python
        x = 1
        ```
        """
    )
    assert [b.skip_reason for b in doc.blocks] == [None, None]
    assert doc.errors == []


def test_output_marker_attaches_expected_output_to_the_previous_runnable_block():
    doc = _doc(
        """\
        ```python
        print("Bob")
        ```

        <!-- doc-test: output -->
        ```text
        Bob
        ```
        """
    )
    code, out = doc.blocks
    assert out.kind == "output"
    assert code.expected_output == "Bob\n"
    assert doc.errors == []


def test_output_marker_after_a_data_block_is_an_error():
    doc = _doc(
        """\
        ```json
        {}
        ```
        <!-- doc-test: output -->
        ```text
        {}
        ```
        """
    )
    assert any("no runnable block" in e.message for e in doc.errors)


def test_output_marker_as_first_block_is_an_error():
    doc = _doc(
        """\
        <!-- doc-test: output -->
        ```text
        hello
        ```
        """
    )
    assert any("no runnable block" in e.message for e in doc.errors)


def test_run_marker_makes_a_bash_block_run():
    doc = _doc(
        """\
        <!-- doc-test: run -->
        ```bash
        echo hi
        ```
        ```bash
        echo checked only
        ```
        """
    )
    assert [b.run for b in doc.blocks] == [True, False]


def test_run_marker_on_a_non_bash_block_is_an_error():
    doc = _doc(
        """\
        <!-- doc-test: run -->
        ```python
        x = 1
        ```
        """
    )
    assert any("run marker" in e.message for e in doc.errors)


# ── `# →` expectations ──────────────────────────────────────────────────────


def test_arrow_expectation_on_the_line_after_an_expression():
    doc = _doc(
        """\
        ```python
        rows = [['Bob']]
        rows
        # → [['Bob']]
        ```
        """
    )
    (block,) = doc.blocks
    assert [(e.line, e.expected) for e in block.expectations] == [(2, "[['Bob']]")]
    assert block.problems == []


def test_arrow_expectation_after_a_multiline_expression():
    doc = _doc(
        """\
        ```python
        sorted(
            [3, 1, 2]
        )
        # → [1, 2, 3]
        ```
        """
    )
    (block,) = doc.blocks
    assert [(e.line, e.expected) for e in block.expectations] == [(3, "[1, 2, 3]")]


def test_arrow_expectation_after_an_assignment_is_an_error():
    doc = _doc(
        """\
        ```python
        x = 5
        # → 5
        ```
        """
    )
    (block,) = doc.blocks
    assert block.problems and "expression" in block.problems[0]


def test_arrow_expectation_inside_a_loop_is_an_error():
    doc = _doc(
        """\
        ```python
        for i in range(2):
            i
            # → 0
        ```
        """
    )
    (block,) = doc.blocks
    assert block.problems and "expression" in block.problems[0]


def test_inline_arrow_expectation_is_an_error():
    doc = _doc(
        """\
        ```python
        1 + 1  # → 2
        ```
        """
    )
    (block,) = doc.blocks
    assert block.problems and "own line" in block.problems[0]


def test_arrow_in_a_string_is_not_an_expectation():
    doc = _doc(
        """\
        ```python
        print("a → b")
        ```
        """
    )
    (block,) = doc.blocks
    assert block.expectations == []
    assert block.problems == []


def test_python_syntax_error_is_a_block_problem():
    doc = _doc(
        """\
        ```python
        def broken(:
        ```
        """
    )
    (block,) = doc.blocks
    assert block.kind == "python"
    assert block.problems and "syntax" in block.problems[0].lower()


# ── comparators ─────────────────────────────────────────────────────────────


class _Row:
    """A DB-API row type that is a sequence but not a list (the IRIS driver's DataRow is one)."""

    def __init__(self, *v):
        self._v = list(v)

    def __iter__(self):
        return iter(self._v)

    def __len__(self):
        return len(self._v)

    def __getitem__(self, i):
        return self._v[i]


@pytest.mark.parametrize(
    "value, expected, ok",
    [
        ([["Bob"]], "[['Bob']]", True),
        ([("Bob",)], "[['Bob']]", True),  # tuples compare as lists
        ([_Row("Bob")], "[['Bob']]", True),  # DB-API rows compare as lists
        ([["Bob"]], "[['Alice']]", False),
        (4, "4", True),
        (4, "5", False),
        ({"a": (1, 2)}, "{'a': [1, 2]}", True),
        ("hello", "'hello'", True),
        ("hello", "hello", True),  # a bare word that is not a literal compares to str()
        (3.0, "3", True),
        (None, "None", True),
        (object, "<class 'object'>", True),  # repr when the text is not a literal
        ("1", "1", False),  # a string is not the number the doc wrote
    ],
)
def test_expectation_matches(value, expected, ok):
    assert harness.expectation_matches(value, expected) is ok


def test_output_comparison_ignores_trailing_space_and_blank_edges():
    assert harness.output_matches("\nBob  \nAlice\n\n", "Bob\nAlice\n")
    assert not harness.output_matches("Bob\n", "Alice\n")
    assert not harness.output_matches("Bob\nAlice\n", "Alice\nBob\n")


def test_sql_rows_render_one_per_line_pipe_separated_null_for_none():
    assert harness.render_rows([("a", 1, None), ("b", 2.5, "x")]) == "a | 1 | NULL\nb | 2.5 | x\n"


def test_sql_split_strips_trailing_semicolons():
    assert harness.split_sql("SELECT 1;\nSELECT 2;\n") == ["SELECT 1", "SELECT 2"]
    assert harness.split_sql("SELECT 'a;b'\n") == ["SELECT 'a;b'"]


# ── terminal sessions ───────────────────────────────────────────────────────


def test_terminal_block_splits_commands_from_expected_output():
    block = _doc(
        """\
        ```
        USER> set x = 2
        USER> write x*3
        6
        USER>write "a",!,"b"
        a
        b
        ```
        """
    ).blocks[0]
    commands, expected = harness.terminal_parts(block.text)
    assert commands == ["set x = 2", "write x*3", 'write "a",!,"b"']
    assert expected == "6\na\nb\n"


def test_session_output_drops_banner_and_prompts():
    # The bytes `iris session` wrote for `write 1+1,!` / `set x=5` / `write x` / `halt` (captured 2026-11-03).
    raw = "\nNode: d16bb70c311d, Instance: IRIS\n\nIVG460DOC>\n2\n\nIVG460DOC>\n\nIVG460DOC>\n5\nIVG460DOC>\n"
    assert harness.session_output(raw) == "2\n5\n"


def test_session_error_is_detected():
    raw = "\nNode: x, Instance: IRIS\n\nIVG460DOC>\n\nWRITE y\n^\n<UNDEFINED> *y\nIVG460DOC 2d0>\n"
    assert harness.session_error(raw) == "<UNDEFINED> *y"
    assert harness.session_error("\nIVG460DOC>\n5\nIVG460DOC>\n") is None


def test_namespace_switch_is_refused():
    assert harness.switches_namespace('zn "USER"')
    assert harness.switches_namespace('set $namespace = "%SYS"')
    assert harness.switches_namespace("ZNSPACE \"X\"")
    assert not harness.switches_namespace('write $namespace')


# ── document list is data ───────────────────────────────────────────────────


def test_document_list_comes_from_docs_toml(monkeypatch):
    monkeypatch.delenv("IVG_DOCTEST_NAMESPACE", raising=False)
    config = harness.load_config()
    paths = [d.path for d in config.docs]
    assert "README.md" in paths
    assert len(paths) == len(set(paths))
    assert config.namespace == "IVG460DOC"


def test_namespace_outside_the_scratch_prefix_is_refused(tmp_path, monkeypatch):
    monkeypatch.delenv("IVG_DOCTEST_NAMESPACE", raising=False)
    bad = tmp_path / "docs.toml"
    bad.write_text('[run]\nnamespace = "USER"\n[[doc]]\npath = "README.md"\n')
    with pytest.raises(ValueError, match="IVG460"):
        harness.load_config(bad)


def test_ivg_doctest_namespace_overrides_the_scratch_namespace(monkeypatch):
    monkeypatch.setenv("IVG_DOCTEST_NAMESPACE", "IVG460DC1")
    assert harness.load_config().namespace == "IVG460DC1"


@pytest.mark.parametrize("ns", ["USER", "ivg460dc1", "IVGFIX"])
def test_ivg_doctest_namespace_outside_the_scratch_prefix_is_refused(monkeypatch, ns):
    monkeypatch.setenv("IVG_DOCTEST_NAMESPACE", ns)
    with pytest.raises(ValueError, match="IVG460"):
        harness.load_config()


# ── python blocks: one interpreter per document, no IRIS needed ──────────────


def test_python_blocks_share_one_interpreter_and_compare_answers(tmp_path):
    doc = _doc(
        """\
        ```python
        x = 2 + 2
        ```

        ```python
        x
        # → 4
        ```

        ```python
        x * 10
        # → 41
        ```

        ```python
        print("hi")
        ```

        <!-- doc-test: output -->
        ```text
        hi
        ```

        ```python
        print("wrong")
        ```

        <!-- doc-test: output -->
        ```text
        right
        ```

        ```python
        undefined_name
        ```
        """
    )
    results = harness.run_document(doc, workdir=tmp_path, connect=False, python=sys.executable)
    statuses = [(r.kind, r.status) for r in results if r.kind == "python"]
    assert statuses == [
        ("python", "ok"),
        ("python", "ok"),
        ("python", "failed"),
        ("python", "ok"),
        ("python", "failed"),
        ("python", "failed"),
    ]
    failed = [r for r in results if r.status == "failed"]
    assert "41" in failed[0].message and "40" in failed[0].message
    assert "right" in failed[1].message
    assert "NameError" in failed[2].message


def test_bash_blocks_are_syntax_checked_not_run(tmp_path):
    marker = tmp_path / "ran"
    doc = _doc(
        f"""\
        ```bash
        touch {marker}
        ```

        ```bash
        if then fi (
        ```
        """
    )
    results = harness.run_document(doc, workdir=tmp_path, connect=False, python=sys.executable)
    assert [r.status for r in results] == ["checked", "failed"]
    assert summarize_checked(results) == 1
    assert not marker.exists()


def summarize_checked(results):
    return harness.summarize(results).checked


def test_skipped_and_unclassifiable_blocks_are_reported(tmp_path):
    doc = _doc(
        """\
        <!-- doc-test: skip reason="needs Arno" -->
        ```python
        raise SystemExit(1)
        ```

        ```cypher
        MATCH (n) RETURN n
        ```
        """
    )
    results = harness.run_document(doc, workdir=tmp_path, connect=False, python=sys.executable)
    assert [(r.status, r.message) for r in results][0] == ("skipped", "needs Arno")
    assert results[1].status == "unclassifiable"
    summary = harness.summarize(results)
    assert summary.skips == {"needs Arno": 1}
    assert summary.unclassifiable == 1


# ── run-marked bash: only the test container, never a lifecycle command ─────


@pytest.mark.parametrize(
    "script, refused",
    [
        ("docker exec -i ivg-iris-enterprise iris session IRIS -U IVG460DOC\n", False),
        ("echo hi\n", False),
        ("docker exec -it some-other-iris iris session IRIS\n", True),
        ("docker exec -e X=1 other bash\n", True),
        ("docker cp lib.so ivg-iris-enterprise:/usr/irissys/mgr/\n", False),
        ("docker cp lib.so ivg-iris:/usr/irissys/mgr/\n", True),
        ("docker compose up -d\n", True),
        ("docker restart ivg-iris-enterprise\n", True),
        ("docker exec ivg-iris-enterprise iris session IRIS -U USER\n", True),
    ],
)
def test_bash_refusal(script, refused):
    assert (harness.bash_refusal(script, "ivg-iris-enterprise") is not None) is refused


# ── R042 ratchet: known failures keyed by doc, block index, first error line ─


def test_failure_message_is_normalized_so_reruns_key_the_same():
    msg = (
        "line 279: RuntimeError: GraphMode.Apply('') failed: <MagicMock name='mock.Apply()' id='4475272416'>"
        " at 0x10a3f9e50\nsecond line"
    )
    assert harness.normalize_failure(msg) == (
        "line 279: RuntimeError: GraphMode.Apply('') failed: <MagicMock name='mock.Apply()' id=...> at 0x..."
    )
    assert harness.normalize_failure("No such file: '/w/run1/x.ttl'", workdir="/w/run1") == (
        "No such file: '<workdir>/x.ttl'"
    )
    # A doc that writes beside its scratch directory names the run's directory, which also changes per run.
    assert harness.normalize_failure("No such file: '/w/run1/x.ttl'", workdir="/w/run1/README.md") == (
        "No such file: '<run>/x.ttl'"
    )


def test_the_scratch_namespace_is_normalized_so_any_scratch_namespace_keys_the_same():
    """IVG_DOCTEST_NAMESPACE runs the docs elsewhere; a failure that names the namespace keys the same there."""
    msg = "line 251: FHIRGraphError: Register: ERROR #5001: namespace IVG460DC1 has no FHIR repository"
    expected = "line 251: FHIRGraphError: Register: ERROR #5001: namespace <namespace> has no FHIR repository"
    assert harness.normalize_failure(msg, namespace="IVG460DC1") == expected
    assert harness.normalize_failure(msg.replace("IVG460DC1", "IVG460DOC"), namespace="IVG460DOC") == expected
    # a longer name that only starts with the namespace is not it
    assert harness.normalize_failure("table IVG460DC1X.t", namespace="IVG460DC1") == "table IVG460DC1X.t"
    results = [harness.BlockResult(7, 250, "python", "failed", msg)]
    assert harness.failure_keys("README.md", results, namespace="IVG460DC1") == {("README.md", 7, expected)}


def test_failure_keys_come_from_failed_and_unclassifiable_blocks():
    results = [
        harness.BlockResult(0, 3, "python", "ok"),
        harness.BlockResult(1, 9, "python", "failed", "line 10: NameError: name 'engine' is not defined"),
        harness.BlockResult(2, 14, "unclassifiable", "unclassifiable", "the harness does not run `cypher` blocks"),
        harness.BlockResult(3, 20, "bash", "checked"),
    ]
    assert harness.failure_keys("docs/X.md", results) == {
        ("docs/X.md", 1, "line 10: NameError: name 'engine' is not defined"),
        ("docs/X.md", 2, "the harness does not run `cypher` blocks"),
    }


def test_ratchet_reports_new_and_fixed_entries_for_one_doc():
    known = {("a.md", 1, "x"), ("a.md", 2, "y"), ("b.md", 1, "z")}
    found = {("a.md", 2, "y"), ("a.md", 5, "w")}
    new, gone = harness.ratchet("a.md", found, known)
    assert new == [("a.md", 5, "w")]
    assert gone == [("a.md", 1, "x")]


def test_run_known_failures_round_trip(tmp_path):
    path = tmp_path / "known.txt"
    keys = {("README.md", 7, "line 185: BadSyntax: a | b"), ("docs/X.md", 0, "boom")}
    path.write_text("# comment\n\n" + harness.format_known(keys))
    assert harness.load_known(path) == keys


# ── R066: the reset leaves every ^IVG setting at its install default ─────────


def _ivg_globals_named(paths) -> set:
    import re

    names = set()
    for p in paths:
        for m in re.finditer(r"(?<!\|)\^(IVG\.[A-Za-z]+)", p.read_text(errors="replace")):
            names.add(m.group(1))
    return names


def test_every_ivg_global_the_code_or_a_doc_names_has_a_reset_rule(monkeypatch):
    monkeypatch.delenv("IVG_DOCTEST_NAMESPACE", raising=False)
    repo = harness.REPO
    sources = list((repo / "iris_src" / "src").rglob("*.cls")) + list((repo / "iris_vector_graph").rglob("*.py"))
    sources += [repo / d.path for d in harness.load_config().docs if (repo / d.path).exists()]
    named = _ivg_globals_named(sources)
    rules = harness.IVG_GLOBALS
    assert named - set(rules) == set(), "a ^IVG global with no reset rule in harness.IVG_GLOBALS"
    assert set(rules) - named == set(), "a reset rule for a ^IVG global nothing names any more"
    cleared = {k for k, (kind, _) in rules.items() if kind in ("setting", "derived")}
    # Read from the code: Graph.KG.Ledger (state, strict mode, limits), Graph.KG.GraphMode (multigraph per graph),
    # Graph.KG.TemporalPolicy (temporal-only predicates), Graph.KG.Cypher.Gen (namespace plan mode, test hooks),
    # Graph.KG.FHIRGraph (injected fault).
    assert cleared >= {"IVG.Ledger", "IVG.GraphMode", "IVG.TemporalStructural", "IVG.CypherGen", "IVG.FHIRGraphFault"}
    # Only ever named in a Lock: Adjacency's writers, BuildNKG, the FHIR graph sync.
    assert {k for k, (kind, _) in rules.items() if kind == "lock"} == {"IVG.AdjW", "IVG.NKGBuild", "IVG.FHIRGraph"}
    for name in cleared:
        assert rules[name][1], f"{name} is cleared by the reset but has no command"


# ── R072: the reset never kills ^NKG raw ─────────────────────────────────────
# The live proof (version rises, ^NKG empty, Arno caches gone) is test_doctest_reset_live.py; this is the CI guard
# on the commands the reset sends, since CI has no container.


def test_the_reset_kills_nkg_only_through_drop_nkg_after_the_reload(monkeypatch):
    sent = []

    def fake_session(commands, config, timeout):
        sent.append(list(commands))
        return "HAVE-BASELINE RESET-OK LEFT END"

    monkeypatch.setattr(harness, "run_iris_session", fake_session)
    monkeypatch.setattr(harness, "_graph_counts", lambda config: (0, 0))
    cfg = harness.Config(namespace="IVG460R072", container="c", port=1, host="h", username="u", password="p",
                         block_timeout=1, docs=[], consistency={})
    harness.reset_namespace(cfg)
    reset = next(c for c in sent if any("OBJ.Load" in line for line in c))
    kill_all = next(line for line in reset if "kill" in line and '@("^"_$lg(l,i))' in line)
    assert "kill:$lg(l,i)'=\"NKG\"" in kill_all, kill_all
    load = next(i for i, line in enumerate(reset) if "OBJ.Load" in line)
    drop = next(i for i, line in enumerate(reset) if "Graph.KG.GraphIndex).DropNKG()" in line)
    assert drop > load, reset
    assert not [line for line in reset if "kill ^NKG" in line.lower().replace("kill:", "kill ")], reset
