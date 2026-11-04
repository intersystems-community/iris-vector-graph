"""4.6.0 R024: docs/API_REFERENCE.md is generated from the code and is not stale.

The reference is `scripts/dev/gen_api_reference.py`'s output, committed. These tests fail when:

* the committed file differs from a fresh generation (`--check` exits non-zero): a signature or docstring
  changed and nobody regenerated it;
* a public method of `IRISGraphEngine` or `IRISGraphOperators` is missing from the file, or is shown with
  parameters other than the ones `inspect.signature` reads from the code;
* a public method's summary (the first paragraph of its docstring) is empty or a placeholder.

The signature check parses the file's `def` lines with `ast`, independently of how the generator renders
them, so a default the generator cannot render as Python (`<object object at 0x…>`) also fails here.
No IRIS needed.
"""

from __future__ import annotations

import ast
import inspect
import re
import subprocess
import sys
from pathlib import Path

import pytest

from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.operators import IRISGraphOperators

REPO = Path(__file__).resolve().parents[2]
GENERATOR = REPO / "scripts" / "dev" / "gen_api_reference.py"
REFERENCE = REPO / "docs" / "API_REFERENCE.md"
CLASSES = (IRISGraphEngine, IRISGraphOperators)

#: What a summary must not be. The generator writes NO_SUMMARY where a docstring is missing.
NO_SUMMARY = "No summary."
PLACEHOLDER = re.compile(r"^\W*(todo|tbd|fixme|xxx|placeholder|no summary|undocumented|docstring|\.\.\.)\b",
                         re.IGNORECASE)


def _public_members(cls) -> list:
    """(name, raw attribute) for every public method, classmethod, staticmethod and property of ``cls``."""
    out = []
    for name in sorted(dir(cls)):
        if name.startswith("_"):
            continue
        raw = inspect.getattr_static(cls, name)
        if isinstance(raw, (staticmethod, classmethod, property)) or inspect.isfunction(raw):
            out.append((name, raw))
    return out


def _callable(raw):
    if isinstance(raw, (staticmethod, classmethod)):
        return raw.__func__
    if isinstance(raw, property):
        return raw.fget
    return raw


def _summary(raw) -> str:
    doc = inspect.getdoc(_callable(raw)) or ""
    return doc.strip().split("\n\n")[0].strip()


def _sections(text: str, cls) -> dict:
    """Method name -> the text of its `#### ` entry, within ``cls``'s `## ` part of the reference."""
    start = re.search(rf"^## `{cls.__name__}`$", text, re.MULTILINE)
    assert start, f"the reference has no section for {cls.__name__}"
    end = re.search(r"^## ", text[start.end():], re.MULTILINE)
    body = text[start.start(): start.end() + end.start()] if end else text[start.start():]
    entries = {}
    for m in re.finditer(r"^#### `([A-Za-z_][A-Za-z0-9_]*)`\n(.*?)(?=^#{1,4} |\Z)", body, re.MULTILINE | re.DOTALL):
        entries[m.group(1)] = m.group(2)
    return entries


def _documented_params(entry: str, name: str) -> list:
    """Parameter names of the `def` in the entry's python block, parsed with ast."""
    m = re.search(r"```python\n(.*?)```", entry, re.DOTALL)
    assert m, f"{name}: no python signature block"
    block = m.group(1)
    tree = ast.parse(block + "    ...\n" if block.rstrip().endswith(":") else block)
    (fn,) = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    assert fn.name == name, f"{name}: the signature block defines {fn.name}"
    a = fn.args
    return [p.arg for p in a.posonlyargs + a.args] + ([a.vararg.arg] if a.vararg else []) + \
        [p.arg for p in a.kwonlyargs] + ([a.kwarg.arg] if a.kwarg else [])


def _code_params(raw) -> list:
    sig = inspect.signature(_callable(raw))
    names = list(sig.parameters)
    if not isinstance(raw, staticmethod) and names and names[0] in ("self", "cls"):
        names = names[1:]
    return names


@pytest.fixture(scope="module")
def reference() -> str:
    assert REFERENCE.is_file(), f"{REFERENCE.relative_to(REPO)} is missing: run {GENERATOR.relative_to(REPO)}"
    return REFERENCE.read_text()


def test_committed_reference_equals_a_fresh_generation():
    r = subprocess.run([sys.executable, str(GENERATOR), "--check"], cwd=REPO, capture_output=True, text=True)
    assert r.returncode == 0, (
        f"docs/API_REFERENCE.md is stale (exit {r.returncode}); run `python scripts/dev/gen_api_reference.py`.\n"
        f"{r.stdout[-3000:]}\n{r.stderr[-3000:]}")


@pytest.mark.parametrize("cls", CLASSES, ids=lambda c: c.__name__)
def test_every_public_method_appears_with_its_signature(cls, reference):
    entries = _sections(reference, cls)
    missing, wrong = [], []
    for name, raw in _public_members(cls):
        if name not in entries:
            missing.append(name)
            continue
        if isinstance(raw, property):
            assert "property" in entries[name], f"{cls.__name__}.{name}: a property not marked as one"
            continue
        documented = _documented_params(entries[name], name)
        if documented != _code_params(raw):
            wrong.append(f"{name}: documented {documented}, code {_code_params(raw)}")
    assert not missing, f"{cls.__name__}: not in the reference: {missing}"
    assert not wrong, f"{cls.__name__}: signatures differ:\n" + "\n".join(wrong)


@pytest.mark.parametrize("cls", CLASSES, ids=lambda c: c.__name__)
def test_no_public_method_has_an_empty_or_placeholder_summary(cls, reference):
    bad = []
    for name, raw in _public_members(cls):
        s = _summary(raw)
        if not s or PLACEHOLDER.search(s) or s.rstrip(".").lower() == name.replace("_", " ").lower():
            bad.append(f"{name}: {s!r}")
    assert not bad, f"{cls.__name__}: empty or placeholder summaries:\n" + "\n".join(bad)
    assert NO_SUMMARY not in reference, "the reference shows a method with no summary"


def test_reference_lists_the_public_exceptions_and_env_vars(reference):
    """The exceptions a caller catches and the IVG_* variables the code reads have entries (spot checks with
    known answers: the schema error spec 262 added, and the variable that names the schema)."""
    assert re.search(r"^### `IVGSchemaError`", reference, re.MULTILINE)
    assert re.search(r"^### `EmbeddedUnavailableError`", reference, re.MULTILINE)
    m = re.search(r"^### `IVG_SCHEMA_PREFIX`\n(.*?)(?=^#{1,4} |\Z)", reference, re.MULTILINE | re.DOTALL)
    assert m and "Graph_KG" in m.group(1), "IVG_SCHEMA_PREFIX's entry does not say its default, Graph_KG"
