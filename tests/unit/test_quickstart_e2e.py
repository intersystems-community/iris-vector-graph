"""The parts of `scripts/quickstart_e2e.py` that do not need Docker.

The harness runs what a new user runs: the repo's `docker-compose.yml`, a clean venv
with the built wheel, and the README / QUICKSTART code as published. 4.1.0 shipped
with all three of these broken and no test noticed (2026-09-30): the compose image's
entrypoint died, the wheel could not deploy its classes on a stock container
(`ERROR #5007 '/tmp/src/'`), and the README promised `[('Bob',)]` for `[['Bob']]`.
So the snippets are read from the docs, never copied into the test, and what the doc
says a line prints is what the harness asserts.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import quickstart_e2e as q

ROOT = Path(__file__).resolve().parents[2]


class TestTheReadmeSnippet:
    def test_it_is_read_from_the_readme(self):
        code = q.readme_snippet((ROOT / "README.md").read_text())
        assert "initialize_schema()" in code
        assert 'iris.connect("localhost", 1972' in code

    def test_the_readme_promises_what_the_engine_prints(self):
        """`IVGResult.rows` is a list of lists (measured on 4.1.0 and the 4.1.1 tree)."""
        code = q.readme_snippet((ROOT / "README.md").read_text())
        assert q.expected_print(code) == [["Bob"]]

    def test_a_missing_section_is_an_error_not_an_empty_program(self):
        with pytest.raises(ValueError, match="Run your first query"):
            q.readme_snippet("# README\n\n```python\nprint(1)\n```\n")

    def test_a_print_without_a_promise_is_an_error(self):
        with pytest.raises(ValueError, match="# <expected>"):
            q.expected_print("print(result)\n")

    def test_the_last_promise_wins(self):
        assert q.expected_print("print(1)  # 1\nprint(x)  # [['a']]\n") == [["a"]]


class TestRetarget:
    def test_host_and_port_are_rewritten(self):
        code = 'conn = iris.connect("localhost", 1972, "USER", "_SYSTEM", "SYS")\n'
        out = q.retarget(code, "10.0.0.5", 1972)
        assert out == 'conn = iris.connect("10.0.0.5", 1972, "USER", "_SYSTEM", "SYS")\n'

    def test_same_target_is_the_code_unchanged(self):
        code = 'iris.connect("localhost", 1972, "USER")'
        assert q.retarget(code, "localhost", 1972) == code

    def test_a_doc_that_no_longer_connects_there_is_an_error(self):
        """Otherwise the harness would run the doc against whatever it names."""
        with pytest.raises(ValueError, match="localhost"):
            q.retarget('iris.connect("db", 1972)', "10.0.0.5", 1972)


class TestTheDocProgram:
    DOC = (
        "# Q\n\n```bash\ndocker compose up -d\n```\n\n"
        "```python\nprint('a')\n```\n\nOutput:\n\n```\na\n```\n\n"
        "```python\nx = 1\n```\n\n"
        "```python\nprint(x)\nprint(x + 1)\n```\n\nOutput:\n\n```text\n1\n2\n```\n"
    )

    def test_python_blocks_only_in_order(self):
        assert q.python_blocks(self.DOC) == ["print('a')\n", "x = 1\n", "print(x)\nprint(x + 1)\n"]

    def test_the_output_block_after_a_python_block_is_its_promise(self):
        _, expected = q.doc_program(self.DOC)
        assert expected == {0: "a", 2: "1\n2"}

    def test_the_program_marks_where_each_block_ends(self):
        program, _ = q.doc_program(self.DOC)
        out = "a\n" + q.marker(0) + "\n" + q.marker(1) + "\n1\n2\n" + q.marker(2) + "\n"
        assert q.split_output(out) == {0: "a", 1: "", 2: "1\n2"}
        assert program.count("print(") == 3 + 3

    def test_noise_before_a_line_does_not_hide_a_marker(self):
        """A server write with no trailing newline lands on the marker's line."""
        out = "a" + q.marker(0) + "\n"
        assert q.split_output(out) == {0: "a"}

    def test_the_quickstart_guide_has_promises(self):
        _, expected = q.doc_program((ROOT / "docs/setup/QUICKSTART.md").read_text())
        assert len(expected) >= 3


class TestTheCleanInstallGate:
    def test_an_objectscript_error_line_is_caught(self):
        out = "ok\nDeleting class Graph.KG.Edge\nERROR #5351: Class 'Graph.KG.Edge' does not exist.\n"
        assert q.error_lines(out) == ["ERROR #5351: Class 'Graph.KG.Edge' does not exist."]

    def test_one_glued_to_the_next_line_is_caught(self):
        out = "ERROR #5007: Directory name '/tmp/src/' is invalidIVG setup: ..."
        assert q.error_lines(out) and "#5007" in q.error_lines(out)[0]

    def test_clean_output_passes(self):
        assert q.error_lines("IVG setup: Graph.KG.MCPService not deployed\n[['Bob']]\n") == []


class TestThePreviousRelease:
    def test_the_newest_release_at_or_below_the_wheel(self):
        assert q.previous_release(["4.0.1", "4.1.0", "3.2.0", "4.2.0"], "4.1.1") == "4.1.0"

    def test_an_unbumped_tree_upgrades_from_its_own_published_version(self):
        assert q.previous_release(["4.0.1", "4.1.0"], "4.1.0") == "4.1.0"

    def test_prereleases_are_not_releases(self):
        assert q.previous_release(["4.1.0", "4.1.1rc1"], "4.1.1") == "4.1.0"

    def test_versions_compare_as_numbers(self):
        assert q.previous_release(["4.9.0", "4.10.0"], "4.10.1") == "4.10.0"

    def test_nothing_published_below_is_an_error(self):
        with pytest.raises(ValueError):
            q.previous_release(["5.0.0"], "4.1.1")


    def test_the_wheel_replaces_a_release_of_the_same_version(self):
        # An unbumped tree builds 4.1.0 over installed 4.1.0: a plain `pip install`
        # says "already satisfied" and the upgrade stage tests the old code (seen live).
        cmd = q.install_over("py", Path("/d/iris_vector_graph-4.1.0-py3-none-any.whl"))
        assert "--force-reinstall" in cmd and "--no-deps" in cmd, cmd
        assert cmd[-1].endswith(".whl"), cmd


class TestTheIsolatedOverride:
    """Local runs cannot publish 1972 (another project's container holds it)."""

    def test_no_ports_and_its_own_name(self):
        text = q.isolated_override("ivg-quickstart-e2e")
        assert "ports: !reset []" in text
        assert "container_name: ivg-quickstart-e2e" in text
        assert "network_mode: bridge" in text


class TestTheWorkflow:
    """The harness runs in CI on every push and weekly, with a fresh pull."""

    WF = ROOT / ".github/workflows/quickstart.yml"

    def test_it_runs_on_push_pr_and_a_weekly_schedule(self):
        text = self.WF.read_text()
        for trigger in ("push:", "pull_request:", "schedule:", "- cron:", "workflow_dispatch:"):
            assert trigger in text, trigger

    def test_it_runs_the_harness_with_a_fresh_pull(self):
        text = self.WF.read_text()
        assert "python scripts/quickstart_e2e.py" in text
        assert "--no-pull" not in text and "--isolated" not in text
