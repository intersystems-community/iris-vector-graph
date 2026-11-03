"""Doc-test layer 2 (4.6.0 R043): what the docs name exists in the code.

`check_doc` returns one Finding per thing a reader would trip over:

  link        a relative markdown link whose target is missing
  unshipped   a link or path into something git does not ship (specs/, CLAUDE.md …; docs.toml `unshipped`)
  path        a repo path (`iris_vector_graph/…`, `docs/…`, `tests/…`) that does not exist
  class       a `Graph.KG.*` / `IVG.*` class not in iris_src/src, nor the class of a table IVG's DDL creates
  member      `Class.Member` or `##class(Class).Member` not defined in the class (or a Graph.KG/IVG superclass)
  method      `engine.<name>` / `IRISGraphEngine.<name>` that IRISGraphEngine does not have
  global      a `^Global` root no code in iris_src or iris_vector_graph names
  env         an `IVG_*` variable no code, script or CI file reads
Reader docs only (tests/docs/docs.toml [[doc]]):
  port        a port not in docker-compose.yml's host ports nor docs.toml `other_ports`
  container   a `docker exec|logs|cp|…` target not a docker-compose.yml container
  script      a `scripts/…` command that does not exist
  dev-script  a `scripts/…` command that is a maintainer script (docs.toml `dev_scripts`)

No IRIS needed: everything is read from the checkout.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

from tests.docs.harness import REPO, load_config

REPO_DIRS = (
    "iris_vector_graph", "iris_src", "scripts", "docs", "tests", "examples", "api", "sql", "docker", "deploy",
    "benchmarks", "config", "skills", "vendor", "src",
)
TOP_FILES = (
    "README.md", "CHANGELOG.md", "CONTRIBUTING.md", "CONTEXT.md", "PRE_RELEASE_CHECKLIST.md", "pyproject.toml",
    "docker-compose.yml", "module.xml", "module-core.xml", "LICENSE", "uv.lock",
)
#: Globals that belong to IRIS, not IVG: a doc may name them.
SYSTEM_GLOBALS = re.compile(r"^(%|odd|r(OBJ|INDEX|MAC|INC)|ROUTINE|ERRORS|SYS$|IRIS\.|ISC\.|mtemp|CacheTemp|SPOOL)")
CODE_SUFFIXES = {".py", ".cls", ".sh", ".mac", ".inc", ".yml", ".yaml", ".toml", ".cfg", ".ini", ".json", ".sql"}


@dataclass(frozen=True)
class Finding:
    doc: str
    kind: str
    item: str
    line: int = field(default=0, compare=False, hash=False)

    def key(self) -> tuple:
        return (self.doc, self.kind, self.item)


@dataclass
class Context:
    repo: Path
    classes: set
    packages: set
    class_files: dict
    engine_attrs: set
    globals_: set
    env_vars: set
    compose_ports: set
    compose_containers: set
    other_ports: set
    dev_scripts: list
    unshipped: list
    reader_docs: set
    submodules: list = field(default_factory=list)


def _iter_code(repo: Path, roots: tuple):
    for root in roots:
        base = repo / root
        if base.is_file():
            yield base
            continue
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if p.is_file() and p.suffix in CODE_SUFFIXES and "__pycache__" not in p.parts:
                yield p


def _engine_attrs() -> set:
    from iris_vector_graph.engine import IRISGraphEngine

    attrs = set(dir(IRISGraphEngine))
    src = Path(__import__("inspect").getsourcefile(IRISGraphEngine)).read_text()
    attrs |= set(re.findall(r"\bself\.([A-Za-z_]\w*)\s*(?::[^=\n]+)?=", src))
    return attrs


def _compose(repo: Path) -> tuple:
    text = (repo / "docker-compose.yml").read_text()
    ports = {int(m) for m in re.findall(r"""^\s*-\s*["']?(?:[\d.]+:)?(\d+):\d+""", text, re.M)}
    containers = set(re.findall(r"^\s*container_name:\s*([\w.-]+)", text, re.M))
    containers |= set(re.findall(r"^  ([\w-]+):\s*$", text.split("services:", 1)[1].split("\nvolumes:")[0], re.M))
    return ports, containers


def _submodules(repo: Path) -> list:
    """Submodule paths (.gitmodules). CI and ci-parity do not fetch them, a full checkout has them: a path into one
    is taken as present so the result does not depend on which kind of checkout runs the test."""
    gm = repo / ".gitmodules"
    return re.findall(r"(?m)^\s*path\s*=\s*(\S+)", gm.read_text()) if gm.exists() else []


def _in_submodule(ctx: "Context", rel: str) -> bool:
    import os

    candidates = [rel]
    p = ctx.repo / rel
    if p.is_symlink():
        candidates.append(os.path.normpath(os.path.join(os.path.dirname(rel), os.readlink(p))))
    return any(c == s or c.startswith(s.rstrip("/") + "/") for c in candidates for s in ctx.submodules)


def build_context(repo: Path = REPO) -> Context:
    config = load_config()
    cons = config.consistency
    src = repo / "iris_src" / "src"
    classes, packages, class_files = set(), set(), {}
    for p in src.rglob("*.cls"):
        rel = p.relative_to(src)
        # Both layouts: Graph/KG/Foo.cls and User.Foo.cls at the top.
        name = ".".join(rel.with_suffix("").parts)
        classes.add(name)
        class_files[name] = p
        parts = name.split(".")
        for i in range(1, len(parts)):
            packages.add(".".join(parts[:i]))
    # Classes IVG's DDL creates: table Graph_KG.rdf_edges is class Graph.KG.rdfedges.
    for p in _iter_code(repo, ("iris_vector_graph", "sql")):
        for table in re.findall(r"(?i)CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?Graph_KG\.(\w+)", p.read_text(errors="replace")):
            classes.add("Graph.KG." + table.replace("_", ""))
    globals_ = set()
    for p in _iter_code(repo, ("iris_src", "iris_vector_graph", "scripts", "sql")):
        for g in re.findall(r"\^(?!\|\|)([A-Za-z%][\w.%]*)", p.read_text(errors="replace")):
            globals_.add(g.rstrip("."))
    # Env vars: any IVG_* the shipped code, scripts and CI name; in tests/ only the ones read from the
    # environment (a test may name a variable that does not exist), plus those the doc harness supplies.
    env_vars = set()
    for p in _iter_code(repo, ("iris_vector_graph", "iris_src", "scripts", "api", "docker", "deploy",
                               "docker-compose.yml", ".github", "pyproject.toml", "benchmarks", "examples",
                               "tests/docs/harness.py")):
        env_vars |= set(_ENV.findall(p.read_text(errors="replace")))
    for p in _iter_code(repo, ("tests",)):
        env_vars |= set(_ENV_READ.findall(p.read_text(errors="replace")))
    ports, containers = _compose(repo)
    return Context(
        repo=repo, classes=classes, packages=packages, class_files=class_files, engine_attrs=_engine_attrs(),
        globals_=globals_, env_vars=env_vars, compose_ports=ports, compose_containers=containers,
        other_ports={int(x) for x in cons.get("other_ports", [])},
        dev_scripts=list(cons.get("dev_scripts", [])), unshipped=list(cons.get("unshipped", [])),
        reader_docs={d.path for d in config.docs},
        submodules=_submodules(repo),
    )


def docs_to_check(repo: Path = REPO, ctx: Context = None) -> list:
    """README.md, CONTRIBUTING.md and docs/**/*.md that git ships."""
    unshipped = ctx.unshipped if ctx else []
    paths = [p for p in ("README.md", "CONTRIBUTING.md") if (repo / p).exists()]
    paths += sorted(str(p.relative_to(repo)) for p in (repo / "docs").rglob("*.md"))
    return [p for p in paths if not any(p == u or p.startswith(u) for u in unshipped)]


# ── extraction ─────────────────────────────────────────────────────────────────

_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_INLINE = re.compile(r"(`+)(.+?)\1")
_LINK = re.compile(r"!?\[(?:[^\]\\]|\\.)*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_HREF = re.compile(r"""(?:href|src)=["']([^"']+)["']""")
_REPO_PATH = re.compile(
    r"(?<![\w/.~$-])((?:\./)?(?:" + "|".join(REPO_DIRS) + r")/[\w./*{}-]*[\w*/])"
)
_CLASS = re.compile(r"(?<![\w^.%])((?:Graph\.KG|IVG)(?:\.[A-Za-z%][\w%]*)+)")
_HASHCLASS = re.compile(r"##class\(\s*((?:Graph\.KG|IVG)(?:\.[\w%]+)+)\s*\)\.(%?\w+)")
_ENGINE = re.compile(r"(?<![\w./])(engine|IRISGraphEngine)\.([A-Za-z_]\w*)")
_GLOBAL = re.compile(r"(?:(?<=[\s`(\"',=])|^)\^(?!\|\|)([A-Za-z%][\w.%]*)")
_ENV = re.compile(r"\bIVG_[A-Z0-9_]*[A-Z0-9]\b")
_ENV_READ = re.compile(r"""(?:environ(?:\.get|\.setdefault|\.pop)?\s*[\[(]\s*|getenv\(\s*|envvar=)["'](IVG_\w+)["']""")
_PORTS = (
    re.compile(r"(?:localhost|127\.0\.0\.1|0\.0\.0\.0):(\d{2,5})\b"),
    re.compile(r"(?i)\bport\s*[=:]\s*(\d{3,5})\b"),
    re.compile(r"(?i)\bport\s+(\d{3,5})\b"),
    re.compile(r"""connect\(\s*["'][^"']*["']\s*,\s*(\d{3,5})\b"""),
    re.compile(r"-p\s+(\d{3,5}):\d+"),
)
_SCRIPT = re.compile(r"(?<![\w/.-])(scripts/[\w./-]*[\w])")


def _segments(text: str):
    """(line number, text, in a fenced block) for each line."""
    fence = None
    for n, line in enumerate(text.split("\n"), 1):
        m = _FENCE.match(line)
        if m:
            mark = m.group(1)
            if fence is None:
                fence = mark
                continue
            if mark[0] == fence[0] and len(mark) >= len(fence) and not line.strip()[len(mark):].strip():
                fence = None
                continue
        yield n, line, fence is not None


def _resolve(doc: str, target: str, repo: Path) -> Path:
    if target.startswith("/"):
        return repo / target.lstrip("/")
    return (repo / doc).parent / target


def _is_unshipped(rel: str, unshipped: list) -> bool:
    return any(rel == u or rel.startswith(u) for u in unshipped)


def _rel(p: Path, repo: Path):
    try:
        return str(p.resolve().relative_to(repo.resolve()))
    except ValueError:
        return None


def _class_defines(ctx: Context, cls: str, member: str, seen=None) -> bool:
    if member.startswith("%") or member in ("cls", "int", "mac"):
        return True  # inherited from a % class, or a file name
    path = ctx.class_files.get(cls)
    if path is None:
        return cls in ctx.classes  # a DDL-made class: its members are columns, not checked
    text = path.read_text(errors="replace")
    if re.search(r"(?m)^(?:ClassMethod|Method|Property|Query|Parameter|Index|Relationship|Trigger|XData|Projection|"
                 r"ForeignKey)\s+" + re.escape(member) + r"\b", text):
        return True
    seen = seen or set()
    seen.add(cls)
    m = re.search(r"(?m)^Class\s+[\w.%]+\s+Extends\s+\(?([^){\n]+)", text)
    for sup in (m.group(1).split(",") if m else []):
        sup = sup.strip()
        if sup and sup not in seen and sup in ctx.class_files and _class_defines(ctx, sup, member, seen):
            return True
    return False


def _check_class(ctx: Context, name: str):
    """None when `name` is a class, a package, or Class.Member with the member defined; else (kind, item)."""
    name = name.rstrip(".")
    if name in ctx.classes or name in ctx.packages:
        return None
    parts = name.split(".")
    for i in range(len(parts) - 1, 1, -1):
        cls = ".".join(parts[:i])
        if cls in ctx.classes:
            if _class_defines(ctx, cls, parts[i]):
                return None
            return ("member", f"{cls}.{parts[i]}")
    return ("class", name)


def check_doc(doc: str, text: str, ctx: Context, reader: bool = False) -> list:
    repo = ctx.repo
    found: dict = {}

    def add(kind, item, line):
        f = Finding(doc, kind, item, line)
        found.setdefault(f.key(), f)

    for n, line, in_code in _segments(text):
        inline = [m.group(2) for m in _INLINE.finditer(line)] if not in_code else []
        prose = _INLINE.sub("", line) if not in_code else ""

        # links: prose only
        for m in list(_LINK.finditer(prose)) + list(_HREF.finditer(prose)):
            target = m.group(1)
            if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith("#"):
                continue
            path_part = unquote(target.split("#")[0].split("?")[0])
            if not path_part:
                continue
            resolved = _resolve(doc, path_part, repo)
            rel = _rel(resolved, repo)
            if rel is not None and _is_unshipped(rel, ctx.unshipped):
                add("unshipped", target, n)
            elif not resolved.exists() and not (rel is not None and _in_submodule(ctx, rel)):
                add("link", target, n)

        # repo paths: inline code in prose, and anywhere in code blocks
        candidates = []
        for chunk in (inline if not in_code else [line]):
            candidates += [m.group(1) for m in _REPO_PATH.finditer(chunk)]
        if not in_code:
            candidates += [c for c in inline if c in TOP_FILES]
        for cand in candidates:
            cand = re.sub(r"(::.*|:\d+(-\d+)?)$", "", cand).rstrip(".")
            if reader and cand.startswith("scripts/"):
                continue  # the script checks below cover it
            if "{" in cand:
                continue  # a template (`docs/{name}.md`)
            rel = cand[2:] if cand.startswith("./") else cand
            if _is_unshipped(rel, ctx.unshipped):
                add("unshipped", cand, n)
                continue
            hit = (any(repo.glob(rel)) if "*" in rel else (repo / rel).exists()) or (
                (repo / doc).parent.joinpath(rel).exists())
            if not hit and not _in_submodule(ctx, rel.rstrip("/")):
                add("path", cand, n)

        # classes and members
        for m in _HASHCLASS.finditer(line):
            cls, member = m.group(1), m.group(2)
            problem = _check_class(ctx, cls)
            if problem:
                add(*problem, n)
            elif not _class_defines(ctx, cls, member):
                add("member", f"{cls}.{member}", n)
        for m in _CLASS.finditer(line):
            if m.group(1).rstrip(".") in ctx.globals_:
                continue  # a global named the Native API way, without the caret
            problem = _check_class(ctx, m.group(1))
            if problem:
                add(*problem, n)

        # engine methods
        for m in _ENGINE.finditer(line):
            name = m.group(2)
            if name == "py" or name in ctx.engine_attrs:
                continue
            add("method", f"{m.group(1)}.{name}", n)

        # globals
        for m in _GLOBAL.finditer(line):
            root = m.group(1).split("(")[0]
            if line[m.end():m.end() + 1] == "*":  # ^IVG.* : a family of globals
                if any(g.startswith(root) for g in ctx.globals_):
                    continue
            root = root.rstrip(".")
            if SYSTEM_GLOBALS.match(root) or root in ctx.globals_:
                continue
            storage = re.match(r"^(.+?)(D|I|S|Stream)$", root)
            if storage and storage.group(1) in ctx.classes:
                continue  # a persistent class's data/index/stream global
            add("global", f"^{root}", n)

        # env vars
        for var in _ENV.findall(line):
            if var not in ctx.env_vars:
                add("env", var, n)

        if not reader:
            continue
        for rx in _PORTS:
            for port in rx.findall(line):
                port = int(port)
                if port not in ctx.compose_ports and port not in ctx.other_ports:
                    add("port", str(port), n)
        from tests.docs.harness import _docker_targets

        for target in _docker_targets(line):
            if target not in ctx.compose_containers:
                add("container", target, n)
        for script in _SCRIPT.findall(line):
            if not (repo / script).exists():
                add("script", script, n)
            elif any(script == d or script.startswith(d) for d in ctx.dev_scripts):
                add("dev-script", script, n)
    return list(found.values())


# ── the known-failures baseline ────────────────────────────────────────────────

BASELINE = Path(__file__).with_name("consistency_known_failures.txt")


def load_baseline(path: Path = BASELINE) -> set:
    """(doc, kind, item) per line `doc | kind | item`; `#` comments and blank lines ignored."""
    entries = set()
    if not path.exists():
        return entries
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        doc, kind, item = (part.strip() for part in line.split(" | ", 2))
        entries.add((doc, kind, item))
    return entries


def format_baseline(findings) -> str:
    return "".join(f"{f.doc} | {f.kind} | {f.item}\n" for f in sorted(findings, key=lambda f: f.key()))
