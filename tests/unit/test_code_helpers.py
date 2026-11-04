"""R128: tests/unit/_code.py reads compiled code, on every interpreter the CI matrix runs."""

from __future__ import annotations

import functools
import sys
import textwrap

from tests.unit import _code


def _sample(row, cur, n):
    """Docstring: mentions forbidden_name and SELECT nothing."""
    first = cur.execute("SELECT a, b " "FROM t WHERE x = ?", [row.get("node_id") or row.get("id")])

    def inner():
        return deploy_step()  # noqa: F821

    later = [helper_call(v) for v in (1, 2)]  # noqa: F821
    cur.execute(f"SELECT TOP {n} c FROM u")
    return first, inner, later, "last_marker"


def test_names_cover_nested_code():
    names = _code.names(_sample)
    assert {"execute", "get", "deploy_step", "helper_call", "row", "cur"} <= names
    assert "forbidden_name" not in names


def test_strings_join_adjacent_literals_and_skip_the_docstring():
    strings = _code.strings(_sample)
    assert "SELECT a, b FROM t WHERE x = ?" in strings
    assert "SELECT TOP " in strings and " c FROM u" in strings
    assert "node_id" in strings and "id" in strings
    assert not any("forbidden_name" in s for s in strings)


def _sets():
    skip = {"edge_id", "val_key"}
    return skip, ("a", "b")


def test_strings_open_literal_sets_and_tuples():
    assert {"edge_id", "val_key", "a", "b"} <= set(_code.strings(_sets))


def test_const_args_reads_a_call_s_literal_arguments():
    assert ("node_id",) in _code.const_args(_sample, "get") and ("id",) in _code.const_args(_sample, "get")
    assert ("SELECT a, b FROM t WHERE x = ?",) in _code.const_args(_sample, "execute")


def test_tokens_keep_instruction_order_and_inline_nested_code():
    t = _code.tokens(_sample)
    assert t.index("SELECT a, b FROM t WHERE x = ?") < t.index("deploy_step") < t.index("helper_call")
    assert t.index("helper_call") < t.index("SELECT TOP ") < t.index("last_marker")


def test_code_of_follows_wrappers_and_properties():
    @functools.wraps(_sample)
    def wrapper(*a):
        return _sample(*a)

    assert "deploy_step" in _code.names(wrapper)

    class K:
        @property
        def p(self):
            return self.target_attr

        @staticmethod
        def s():
            return static_target()  # noqa: F821

    assert "target_attr" in _code.names(K.__dict__["p"])
    assert "static_target" in _code.names(K.__dict__["s"])


def test_a_module_is_read_once_and_its_docstring_skipped(tmp_path, monkeypatch):
    mod = tmp_path / "r128_sample_mod.py"
    mod.write_text(textwrap.dedent('''
        """Module docstring naming ZStrip."""
        KEY = "real" "_value"
        def f():
            return other_name
    '''))
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        assert "real_value" in _code.module_strings("r128_sample_mod")
        assert not any("ZStrip" in s for s in _code.module_strings("r128_sample_mod"))
        assert "other_name" in _code.module_names("r128_sample_mod")
        mod.write_text("X = 'changed'\n")  # edited mid-run: the first read is what counts
        assert "real_value" in _code.module_strings("r128_sample_mod")
    finally:
        _code.module_code.cache_clear()
        sys.modules.pop("r128_sample_mod", None)


def test_no_test_reads_a_function_s_source_with_getsource():
    """R128: `inspect.getsource` reads today's file at the loaded code's line numbers; use tests/unit/_code.py."""
    import ast
    from pathlib import Path

    tests = Path(__file__).resolve().parents[1]
    calls = []
    for path in sorted(tests.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), str(path))):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", None)) == "getsource":
                calls.append(f"{path.relative_to(tests)}:{node.lineno}")
    assert calls == []
