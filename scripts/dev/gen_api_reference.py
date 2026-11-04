#!/usr/bin/env python3
"""Generate docs/API_REFERENCE.md from the public API's signatures and docstrings (4.6.0 R025).

    python scripts/dev/gen_api_reference.py           # write docs/API_REFERENCE.md
    python scripts/dev/gen_api_reference.py --check   # exit 1 if the committed file differs from a fresh generation

What it documents, read with ``inspect`` from the checkout (not an installed copy):

* every public method, classmethod, staticmethod and property of ``IRISGraphEngine``, grouped by the mixin that
  defines it (the class first in the MRO that has it), groups in the order of ``GROUPS``, methods alphabetical
  within a group; then the four sub-engines (``engine.graph`` …) and what each method forwards to;
* every public method of ``IRISGraphOperators``, alphabetical;
* the exception and warning classes a caller can catch: those the ``iris_vector_graph`` package exposes, those
  defined in ``EXCEPTION_MODULES``, and those nested in ``IRISGraphEngine``, alphabetical;
* every ``IVG_*`` variable the code reads (a string constant in ``iris_vector_graph/`` or ``api/``), alphabetical,
  described from ``ENV_VARS``.

Each entry shows the signature and the summary: the docstring's first paragraph. A method with no docstring is shown
with ``NO_SUMMARY``, which tests/unit/test_api_reference_fresh.py fails on. The generator stops (exit 2) on what it
cannot document honestly: a mixin with no entry in ``GROUPS``, a default it cannot render as Python, or an
``IVG_*`` variable read in the code with no entry in ``ENV_VARS`` (or an entry for one nothing reads).

The output is what markdownlint-cli2 and prettier leave unchanged, so the freshness check never fights them.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import enum
import importlib
import inspect
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))  # the checkout's package, whatever is installed

OUT = REPO / "docs" / "API_REFERENCE.md"
NO_SUMMARY = "No summary."
WIDTH = 100  # a signature longer than this is written one parameter per line

#: IRISGraphEngine's groups: defining class -> (heading, one line on what it holds). Order is the reference's order.
GROUPS = {
    "IRISGraphEngine": ("Construction and connection", "Building an engine and reaching its connection."),
    "SchemaMixin": ("Schema and setup", "Creating and upgrading the IVG Schema, readiness, graph statistics, "
                    "reification and inference."),
    "NodesEdgesMixin": ("Nodes and edges", "Creating, reading and deleting nodes and edges, one at a time and "
                        "in bulk."),
    "QueryMixin": ("Queries", "Cypher, AQL and two-hop counts."),
    "VectorMixin": ("Vector, text and index search", "Vector search, the BM25, IVF, PLAID and VecIndex indexes, "
                    "and the index registry."),
    "EmbeddingsMixin": ("Embeddings", "Writing and routing node and edge embeddings, and the embedding queue."),
    "AlgorithmsMixin": ("Graph algorithms", "Centrality, communities, ranking, walks and the `kg_*` operators."),
    "TemporalMixin": ("Temporal edges", "Event-time edges, window queries and pre-aggregated analytics."),
    "SnapshotMixin": ("Snapshots, import and export", "Snapshots, NDJSON, NetworkX, RDF and OBO."),
    "RdfExportMixin": ("RDF export", "Serialising the graph as RDF."),
    "ShaclMixin": ("SHACL validation", "Validating the graph against SHACL shapes."),
    "ProvMixin": ("Provenance", "PROV-O views of temporal edges."),
    "LedgerMixin": ("Revision ledger", "The graph revision ledger."),
    "FhirMixin": ("SQL table mappings", "Mapping SQL tables and their relationships into the graph."),
    "FhirGraphMixin": ("FHIR graph", "FHIR named graphs: building, syncing and querying them."),
    "AdminMixin": ("Administration", "Running queries, index visibility and diagnostic warnings."),
}

#: Modules whose exception and warning classes are documented besides the package's own names.
EXCEPTION_MODULES = (
    "iris_vector_graph.errors",
    "iris_vector_graph.exceptions",
    "iris_vector_graph.embedded",
    "iris_vector_graph.schema_name",
    "iris_vector_graph.sdk",
    "iris_vector_graph.cypher.native_shadow",
)

#: Every IVG_* variable the code reads: name -> (default, what it does). The generator fails on a variable missing here.
ENV_VARS = {
    "IVG_ALLOW_FHIR_WRITES": (
        "unset",
        "With `IVG_READ_ONLY=true`, `true` lets the HTTP server accept POST, PUT, PATCH and DELETE on the "
        "`/fhir-event` routes, which read-only mode otherwise refuses with 403.",
    ),
    "IVG_API_KEY": (
        "unset",
        "When set, every route of the HTTP servers requires this value in an `X-Api-Key` (or `Authorization: "
        "Bearer`) header (`/health` and the OpenAPI pages stay open), a Bolt client must send it as the "
        "`credentials` of `HELLO` or `LOGON` before it can run a query, and the `ivg` command-line client sends it "
        "(`--api-key`).",
    ),
    "IVG_ARNO_LIB": (
        "`/usr/irissys/mgr/libarno_callout.so`",
        "Path, on the IRIS server, of the Arno accelerator library loaded when the namespace has no recorded "
        "library path.",
    ),
    "IVG_ARNO_PROOF": (
        "`docs/dev/arno-reader-proof.json`",
        "Path of the Arno reader proof: the minimum library reader revision per algorithm at which the Rust reader "
        "may answer instead of the ObjectScript implementation.",
    ),
    "IVG_CYPHER_MAX_HOPS": (
        "`10`",
        "Largest variable-length hop count the HTTP server's Cypher route accepts; a non-positive or non-numeric "
        "value means the default.",
    ),
    "IVG_CYPHER_SHADOW": (
        "unset",
        "`1` runs each native Cypher read on the SQL path too and raises `NativeMismatch` where the answers differ "
        "(the engine's `cypher_shadow` attribute does the same for one engine). Doubles the work; for testing.",
    ),
    "IVG_DISABLE_ALGO_CACHE": (
        "unset",
        "`1` turns off the shared algorithm result cache (`^IVG.AlgoCache`); every call computes its answer.",
    ),
    "IVG_DISABLE_ARNO": (
        "unset",
        "`1` makes IVG act as if the Arno library were absent: algorithms run their ObjectScript or Python "
        "implementations.",
    ),
    "IVG_EDGESCAN": (
        "unset",
        "`1` makes a directed hop on the SQL path join the `Graph_KG.MatchEdges` scan of `^KG` instead of "
        "`rdf_edges` (the plan before 4.3.0; about 77 us an edge on a hub).",
    ),
    "IVG_EMBEDDED_PYTHON": (
        "unset (probe the server)",
        "`1` or `0` says whether the IRIS server can run `[ Language = python ]` methods, instead of probing it; "
        "class deploy skips the classes that need Embedded Python when it cannot.",
    ),
    "IVG_FHIR_ALLOWED_BASES": (
        "unset",
        "Comma-separated FHIR base URLs the HTTP server may fetch when a Cypher request names `fhir_base_url`, "
        "compared exactly (a trailing slash aside). Any other URL is refused; unset, every caller-supplied URL is.",
    ),
    "IVG_FORCE_CLASS_DEPLOY": (
        "unset",
        "`1` makes class deploy load and compile the packaged ObjectScript classes even when the namespace's "
        "deploy fingerprint says they are current.",
    ),
    "IVG_GRAPHQL_MAX_DEPTH": (
        "`10`",
        "Deepest GraphQL query the HTTP server accepts; a non-positive or non-numeric value means the default.",
    ),
    "IVG_IGNORE_NAMESPACE_CHECK": (
        "unset",
        "`1` silences the warning logged when `namespace=` names a namespace other than the connection's.",
    ),
    "IVG_NATIVE_CYPHER": (
        "`1`",
        "`0` sends every Cypher read to the SQL path; otherwise the native executor answers the reads it supports. "
        "`IRISGraphEngine(native_cypher=False)` does the same for one engine.",
    ),
    "IVG_NATIVE_PARALLEL_MIN": (
        "`1500`",
        "Estimated work (anchors times the sampled fan-out of the first two hops) at which a native read is split "
        "across worker processes; `0` always splits, a negative value never does.",
    ),
    "IVG_NATIVE_ROUTE_MS": (
        "`50`",
        "A query shape native answers in less than this many ms is also timed on the SQL path, and the faster path "
        "answers it from then on; `0` never times.",
    ),
    "IVG_PROBE_IDLE_SECONDS": (
        "`30`",
        "How long the engine's connection may sit idle before the next Cypher call probes it and reconnects a stale "
        "one; `0` probes on every call.",
    ),
    "IVG_READ_ONLY": (
        "unset",
        "`true` makes the HTTP servers refuse writes with 403: mutating Cypher on the Cypher routes, the Neo4j HTTP "
        "routes, Bolt and GraphQL, GraphQL mutations, every write method under `/admin/` except `/admin/explain`, "
        "and writes to `/fhir-event` (see `IVG_ALLOW_FHIR_WRITES`). The Python library is unaffected.",
    ),
    "IVG_REQUIRE_ARNO": (
        "unset",
        "`1` makes the Arno reader gate fail instead of skip when the library is absent (a pre-release step).",
    ),
    "IVG_SCHEMA_PREFIX": (
        "`Graph_KG`",
        "The IVG Schema (the SQL schema of IVG's tables) an engine uses when `schema_prefix=` is not given. A name "
        "that cannot be one raises `IVGSchemaError` when the engine is built.",
    ),
    "IVG_SNAPSHOT_DIR": (
        "the system temporary directory",
        "Where the HTTP server's `POST /admin/snapshot` writes; set it to a durable directory.",
    ),
    "IVG_STRICT_NAMESPACE": (
        "unset",
        "`1` also issues the namespace-mismatch message as a `NamespaceMismatchWarning`, so a run with warnings as "
        "errors fails on it.",
    ),
    "IVG_URL": (
        "`http://localhost:8200`",
        "The server the `ivg` command-line client talks to (`--url`).",
    ),
}
ENV_SCAN_ROOTS = ("iris_vector_graph", "api")
_ENV_NAME = re.compile(r"IVG_[A-Z0-9_]+\Z")


class GenerationError(Exception):
    """Something the generator cannot document honestly; the message says what to fix."""


# ── text ──────────────────────────────────────────────────────────────────────


def _own_doc(obj) -> str:
    """``obj``'s own docstring, cleaned; never one inherited from a base class."""
    doc = obj.__doc__ if isinstance(obj, type) else getattr(obj, "__doc__", None)
    if isinstance(obj, type) and "__doc__" not in vars(obj):
        doc = None
    return inspect.cleandoc(doc) if doc else ""


_CODE_SPAN = re.compile(r"(`+)(.+?)\1")


def _markdown(text: str) -> str:
    """One docstring paragraph as one markdown line that markdownlint and prettier leave as it is."""
    text = " ".join(text.split())
    text = re.sub(r":(?:class|func|meth|attr|mod|data|exc|obj):`~?([^`]+)`", r"``\1``", text)  # Sphinx roles
    out, pos = [], 0
    for m in _CODE_SPAN.finditer(text):
        out.append(_escape(text[pos:m.start()]))
        body = m.group(2)
        ticks = "``" if "`" in body else "`"
        pad = " " if body.startswith("`") or body.endswith("`") else ""
        out.append(f"{ticks}{pad}{body}{pad}{ticks}")
        pos = m.end()
    out.append(_escape(text[pos:]))
    return "".join(out).strip()


def _escape(prose: str) -> str:
    """Escape what markdown would read as markup in docstring prose (outside code spans)."""
    prose = prose.replace("\\", "\\\\")
    prose = re.sub(r"([*<>\[\]|])", r"\\\1", prose)
    # An underscore inside a word (rdf_labels) is never emphasis, and prettier drops its escape.
    return re.sub(r"(?<![A-Za-z0-9])_|_(?![A-Za-z0-9])", r"\\_", prose)


def summary(obj) -> str:
    doc = _own_doc(obj)
    first = re.split(r"\n\s*\n", doc.strip(), maxsplit=1)[0] if doc.strip() else ""
    return _markdown(first) if first else NO_SUMMARY


# ── signatures ────────────────────────────────────────────────────────────────


class _Expr:
    """A default rendered as source text."""

    def __init__(self, text: str):
        self.text = text

    def __repr__(self) -> str:
        return self.text


def _default_text(fn, value) -> str:
    text = repr(value)
    if " at 0x" not in text:
        try:
            ast.parse(text, mode="eval")
            return text
        except SyntaxError:
            pass
    for name, obj in sorted(getattr(fn, "__globals__", {}).items()):
        if obj is value:
            return name
    if isinstance(value, enum.Enum):
        return f"{type(value).__name__}.{value.name}"
    raise GenerationError(f"{fn.__module__}.{fn.__qualname__}: cannot render the default {text} as Python")


def _params(fn, drop_first: bool) -> tuple:
    sig = inspect.signature(fn)
    params = list(sig.parameters.values())
    if drop_first and params and params[0].name in ("self", "cls"):
        params = params[1:]
    parts, kinds = [], inspect.Parameter
    pos_only_done, kw_marker_done = False, False
    for i, p in enumerate(params):
        if p.default is not kinds.empty:
            p = p.replace(default=_Expr(_default_text(fn, p.default)))
        if p.kind == kinds.POSITIONAL_ONLY:
            parts.append(str(p))
            last_pos_only = i + 1 == len(params) or params[i + 1].kind != kinds.POSITIONAL_ONLY
            if last_pos_only and not pos_only_done:
                parts.append("/")
                pos_only_done = True
            continue
        if p.kind == kinds.VAR_POSITIONAL:
            kw_marker_done = True
        elif p.kind == kinds.KEYWORD_ONLY and not kw_marker_done:
            parts.append("*")
            kw_marker_done = True
        parts.append(str(p))
    ret = sig.return_annotation
    ret_text = "" if ret is inspect.Signature.empty else f" -> {inspect.formatannotation(ret)}"
    return parts, ret_text


def signature_block(name: str, raw) -> str:
    """The python block for one member: decorators, then ``def name(...)`` without ``self``."""
    if isinstance(raw, property):
        fn = raw.fget
        _, ret = _params(fn, drop_first=True)
        return f"@property\ndef {name}(){ret}"
    deco = ""
    if isinstance(raw, staticmethod):
        fn, deco, drop = raw.__func__, "@staticmethod\n", False
    elif isinstance(raw, classmethod):
        fn, deco, drop = raw.__func__, "@classmethod\n", True
    else:
        fn, drop = raw, True
    parts, ret = _params(fn, drop_first=drop)
    one_line = f"def {name}({', '.join(parts)}){ret}"
    if len(one_line) <= WIDTH or not parts:
        return deco + one_line
    body = "".join(f"    {p},\n" for p in parts)
    return f"{deco}def {name}(\n{body}){ret}"


# ── collection ────────────────────────────────────────────────────────────────


def public_members(cls) -> list:
    """(name, raw attribute, defining class) for each public method/classmethod/staticmethod/property, by name."""
    out = []
    for name in sorted(dir(cls)):
        if name.startswith("_"):
            continue
        raw = inspect.getattr_static(cls, name)
        if not (isinstance(raw, (staticmethod, classmethod, property)) or inspect.isfunction(raw)):
            continue
        owner = next(k for k in cls.__mro__ if name in vars(k))
        out.append((name, raw, owner))
    return out


def _doc_target(raw):
    if isinstance(raw, (staticmethod, classmethod)):
        return raw.__func__
    # A property itself: its doc is the getter's unless `property(doc=...)` gave it one (`conn`).
    return raw


def _rel(obj) -> str:
    return Path(inspect.getsourcefile(obj)).resolve().relative_to(REPO).as_posix()


def _entry(name: str, raw, prefix: str = "") -> list:
    return [f"#### `{prefix}{name}`", "", "```python", signature_block(name, raw), "```", "", summary(_doc_target(raw)), ""]


def _sub_engines(engine_cls) -> list:
    """(attribute, class, [(method, forwarded-to)]) for the sub-engines IRISGraphEngine.__init__ attaches."""
    module = sys.modules[engine_cls.__module__]
    src = inspect.getsource(engine_cls.__init__)
    out = []
    for attr, cls_name in re.findall(r"self\.(\w+) = (_\w+SubEngine)\(self\)", src):
        cls = getattr(module, cls_name)
        methods = []
        for name, fn in vars(cls).items():
            if name.startswith("_") or not inspect.isfunction(fn):
                continue
            m = re.search(r"return self\._e\.((?:_store\.)?\w+)\(", inspect.getsource(fn))
            if not m:
                raise GenerationError(f"{cls_name}.{name}: cannot tell what it forwards to")
            methods.append((name, m.group(1)))
        out.append((attr, cls, sorted(methods)))
    if not out:
        raise GenerationError("found no sub-engines in IRISGraphEngine.__init__")
    return out


def _exceptions(engine_cls) -> list:
    """(name, class, import line) for each public exception or warning class, by name."""
    pkg = importlib.import_module("iris_vector_graph")
    found = {}

    def consider(name, obj, where):
        if name.startswith("_") or not (isinstance(obj, type) and issubclass(obj, BaseException)):
            return
        if obj.__module__.split(".")[0] != "iris_vector_graph":
            return  # a builtin or third-party class re-exported by name
        found.setdefault(name, (obj, where))

    for name, obj in sorted(vars(pkg).items()):
        consider(name, obj, f"from iris_vector_graph import {name}")
    for mod_name in EXCEPTION_MODULES:
        mod = importlib.import_module(mod_name)
        for name, obj in sorted(vars(mod).items()):
            if isinstance(obj, type) and obj.__module__ == mod_name:
                consider(name, obj, f"from {mod_name} import {name}")
    for name, obj in sorted(vars_all(engine_cls).items()):
        consider(name, obj, f"IRISGraphEngine.{name}")
    return [(n, c, w) for n, (c, w) in sorted(found.items())]


def vars_all(cls) -> dict:
    out = {}
    for k in reversed(cls.__mro__):
        out.update(vars(k))
    return out


def env_vars_read() -> dict:
    """IVG_* name -> sorted repo paths of the files whose code holds it as a string constant."""
    found: dict = {}
    for root in ENV_SCAN_ROOTS:
        for path in sorted((REPO / root).rglob("*.py")):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and _ENV_NAME.match(node.value):
                    found.setdefault(node.value, set()).add(path.relative_to(REPO).as_posix())
    return {k: sorted(v) for k, v in sorted(found.items())}


# ── the document ──────────────────────────────────────────────────────────────


def _slug(heading: str) -> str:
    """GitHub's anchor for a heading."""
    s = heading.strip().lower().replace("`", "")
    s = re.sub(r"[^\w\- ]", "", s)
    return s.replace(" ", "-")


def generate() -> str:
    from iris_vector_graph.engine import IRISGraphEngine
    from iris_vector_graph.operators import IRISGraphOperators

    members = public_members(IRISGraphEngine)
    unknown = sorted({owner.__name__ for _, _, owner in members} - set(GROUPS))
    if unknown:
        raise GenerationError(f"IRISGraphEngine methods come from classes with no GROUPS entry: {unknown}")

    read = env_vars_read()
    undescribed = sorted(set(read) - set(ENV_VARS))
    unread = sorted(set(ENV_VARS) - set(read))
    if undescribed or unread:
        raise GenerationError(
            f"ENV_VARS is out of date: read in the code but not described: {undescribed}; "
            f"described but read nowhere: {unread}")

    lines = [
        "<!-- markdownlint-disable MD013 -->",
        "",
        "# API reference",
        "",
        "<!-- Generated by scripts/dev/gen_api_reference.py. Do not edit: change the docstring, then regenerate. -->",
        "",
        "This file is generated from the code's signatures and docstrings: edit the docstring, then run "
        "`python scripts/dev/gen_api_reference.py`. `tests/unit/test_api_reference_fresh.py` fails when this file "
        "is stale.",
        "",
        "It lists the public Python API: every public method of `IRISGraphEngine` and `IRISGraphOperators` with "
        "its signature and the first paragraph of its docstring, the exceptions a caller can catch, and the "
        "`IVG_*` environment variables the code reads. The whole docstring (for example "
        "`help(IRISGraphEngine.execute_cypher)`) describes the parameters; the [User Guide](USER_GUIDE.md) says how "
        "to do a task.",
        "",
        "Within each group, entries are in alphabetical order.",
        "",
        "## Contents",
        "",
        "- [`IRISGraphEngine`](#irisgraphengine)",
    ]
    groups = [(cls_name, heading) for cls_name, (heading, _) in GROUPS.items()
              if any(owner.__name__ == cls_name for _, _, owner in members)]
    lines += [f"  - [{heading}](#{_slug(heading)})" for _, heading in groups]
    lines += [
        "  - [Sub-engines](#sub-engines)",
        "- [`IRISGraphOperators`](#irisgraphoperators)",
        "- [Exceptions and warnings](#exceptions-and-warnings)",
        "- [Environment variables](#environment-variables)",
        "",
        "## `IRISGraphEngine`",
        "",
        "`from iris_vector_graph import IRISGraphEngine`",
        "",
        summary(IRISGraphEngine),
        "",
        "Its methods are grouped by the mixin class that defines them.",
        "",
    ]
    for cls_name, heading in groups:
        owner = next(o for _, _, o in members if o.__name__ == cls_name)
        lines += [f"### {heading}", "", f"{GROUPS[cls_name][1]} Defined in `{cls_name}` (`{_rel(owner)}`).", ""]
        for name, raw, o in members:
            if o is owner:
                lines += _entry(name, raw)

    lines += [
        "### Sub-engines",
        "",
        "Four attributes of an engine group some of its methods by subject. Each method forwards its arguments "
        "unchanged to the engine method shown (or, marked `_store.`, to the engine's store).",
        "",
    ]
    for attr, cls, methods in _sub_engines(IRISGraphEngine):
        lines += [f"#### `engine.{attr}`", "", summary(cls), ""]
        lines += [f"- `{name}` calls `{target}`" for name, target in methods]
        lines.append("")

    lines += [
        "## `IRISGraphOperators`",
        "",
        "`from iris_vector_graph.operators import IRISGraphOperators`",
        "",
        summary(IRISGraphOperators),
        "",
        f"Defined in `{_rel(IRISGraphOperators)}`.",
        "",
        "### Methods",
        "",
    ]
    for name, raw, _ in public_members(IRISGraphOperators):
        # Qualified: most share a name with an engine method, and a repeated heading fails markdownlint MD024.
        lines += _entry(name, raw, prefix="IRISGraphOperators.")

    lines += [
        "## Exceptions and warnings",
        "",
        "The classes a caller can catch, with the import that reaches each and its base classes.",
        "",
    ]
    for name, cls, where in _exceptions(IRISGraphEngine):
        bases = ", ".join(f"`{b.__name__}`" for b in cls.__bases__)
        lines += [f"### `{name}`", "", f"`{where}`; subclass of {bases}.", "", summary(cls), ""]

    lines += [
        "## Environment variables",
        "",
        "Every `IVG_*` variable the code in `iris_vector_graph/` and `api/` reads.",
        "",
    ]
    for name, files in read.items():
        default, what = ENV_VARS[name]
        where = ", ".join(f"`{f}`" for f in files)
        lines += [f"### `{name}`", "", f"Default: {default}. Read in {where}.", "", what, ""]

    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if the committed file differs from a generation")
    args = ap.parse_args(argv)
    try:
        text = generate()
    except GenerationError as exc:
        print(f"gen_api_reference: {exc}", file=sys.stderr)
        return 2
    if args.check:
        current = OUT.read_text() if OUT.exists() else ""
        if current == text:
            print(f"{OUT.relative_to(REPO)} is current")
            return 0
        diff = difflib.unified_diff(current.splitlines(), text.splitlines(), "committed", "generated", lineterm="", n=1)
        print("\n".join(list(diff)[:200]))
        print(f"{OUT.relative_to(REPO)} is stale: run python scripts/dev/gen_api_reference.py", file=sys.stderr)
        return 1
    OUT.write_text(text)
    print(f"wrote {OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
