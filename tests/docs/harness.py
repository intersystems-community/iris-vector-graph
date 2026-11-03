"""Doc-test harness, layer 1 (4.6.0 R041): the reader docs' code runs, and the answers they state match.

Conventions (``specs/release-4.6.0/plan.md``, "Doc-test conventions"):

* Every fenced block runs unless it is preceded by ``<!-- doc-test: skip reason="…" -->``. Skips are counted by
  reason. A block the harness cannot classify, with no skip marker, is an error.
* ``python`` blocks run in order in one interpreter per document (a worker process, :func:`_worker_main`).
  ``sql`` blocks run through a DB-API cursor in that same process. ``objectscript`` and terminal blocks (lines
  starting ``USER>``) run through ``docker exec <container> iris session IRIS -U <NS>``, prompts stripped and the
  output compared. ``bash`` blocks are syntax-checked (``bash -n``) unless marked ``<!-- doc-test: run -->``.
* An expected answer is written by the doc's author: ``# → [['Bob']]`` on its own line after a top-level python
  expression, or a following block preceded by ``<!-- doc-test: output -->``. The harness compares; it never
  supplies the expected value.
* Every connection a document opens goes to the scratch namespace (``IVG460DOC``) on the test container, whatever
  host, port and namespace the document names: the docs say ``localhost:1972``, and on a workstation that port can
  be another project's IRIS. Each redirect is recorded as a note.

The document list is data: ``tests/docs/docs.toml``.
"""

from __future__ import annotations

import ast
import io
import json
import os
import queue
import re
import subprocess
import sys
import threading
import tokenize
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import tomllib
except ImportError:  # Python 3.10: pytest depends on tomli there
    import tomli as tomllib  # type: ignore[no-redef]

REPO = Path(__file__).resolve().parents[2]
DOCS_TOML = Path(__file__).with_name("docs.toml")
SCRATCH_PREFIX = "IVG460"

# ── configuration ──────────────────────────────────────────────────────────────


@dataclass
class DocEntry:
    path: str
    block_timeout: Optional[int] = None


@dataclass
class Config:
    namespace: str
    container: str
    port: int
    host: str
    username: str
    password: str
    block_timeout: int
    docs: list
    consistency: dict


def load_config(path: Path = DOCS_TOML) -> Config:
    data = tomllib.loads(Path(path).read_text())
    run = data.get("run", {})
    # IVG_DOCTEST_NAMESPACE runs the docs in another scratch namespace (a parallel worker's own).
    ns = os.environ.get("IVG_DOCTEST_NAMESPACE") or run.get("namespace", "IVG460DOC")
    if not ns.startswith(SCRATCH_PREFIX) or ns.upper() != ns:
        raise ValueError(
            f"{path} / IVG_DOCTEST_NAMESPACE: namespace {ns!r} refused: the harness empties the namespace it runs in, so it must be an "
            f"upper-case scratch namespace starting {SCRATCH_PREFIX}"
        )
    docs = [DocEntry(d["path"], d.get("block_timeout")) for d in data.get("doc", [])]
    return Config(
        namespace=ns,
        container=os.environ.get("IVG_TEST_CONTAINER", run.get("container", "ivg-iris-enterprise")),
        port=int(os.environ.get("IVG_PORT", run.get("port", 31972))),
        host="localhost",
        username=run.get("username", "_SYSTEM"),
        password=run.get("password", "SYS"),
        block_timeout=int(run.get("block_timeout", 300)),
        docs=docs,
        consistency=data.get("consistency", {}),
    )


# ── parsing ────────────────────────────────────────────────────────────────────

PYTHON_LANGS = {"python", "py", "python3"}
SQL_LANGS = {"sql"}
OBJECTSCRIPT_LANGS = {"objectscript", "cos", "cls", "iris"}
BASH_LANGS = {"bash", "sh", "shell", "zsh", "console", "shell-session", "shellsession"}
#: Shown to the reader, never run: example payloads, configuration, diagrams.
DATA_LANGS = {
    "json", "jsonl", "ndjson", "yaml", "yml", "toml", "ini", "cfg", "conf", "properties", "env",
    "turtle", "ttl", "nquads", "nq", "trig", "n3", "xml", "html", "csv", "tsv",
    "mermaid", "http", "diff", "dockerfile", "log", "text", "txt", "plaintext",
}
RUNNABLE_KINDS = {"python", "sql", "objectscript", "terminal", "bash"}

_FENCE_OPEN = re.compile(r"^(?P<indent>[ \t]*)(?P<fence>`{3,}|~{3,})[ \t]*(?P<info>[^`\s]*)")
_MARKER = re.compile(r"^<!--\s*doc-test:\s*(?P<body>.*?)\s*-->$")
_SKIP = re.compile(r'^skip(?:\s+reason="(?P<reason>[^"]*)")?$')
#: A terminal prompt: `USER>`, `%SYS>`, `IVG460DOC 2d0>` (an error frame).
_PROMPT = re.compile(r"^(?P<ns>[%A-Z][%A-Z0-9_-]*)(?:\s+\d+[a-z]\d+)?>\s?(?P<rest>.*)$")
_CLASS_DEF = re.compile(r"^(Class|Include|Import|ClassMethod|Method|Property|Parameter|ROUTINE)\b")


@dataclass
class Expectation:
    line: int  # block-relative (1-based) line the expression ends on
    expected: str


@dataclass
class Block:
    index: int
    line: int  # document line (1-based) of the opening fence
    lang: str
    text: str
    kind: str = "unclassifiable"
    skip_reason: Optional[str] = None
    run: bool = False
    expected_output: Optional[str] = None
    expectations: list = field(default_factory=list)
    problems: list = field(default_factory=list)


@dataclass
class ParseError:
    line: int
    message: str


@dataclass
class ParsedDoc:
    blocks: list
    errors: list


@dataclass
class _Marker:
    line: int
    kind: str  # skip | output | run
    reason: Optional[str] = None


def _first_line(text: str) -> str:
    for raw in text.splitlines():
        if raw.strip():
            return raw.strip()
    return ""


def classify(lang: str, text: str) -> tuple:
    """(kind, problem or None) for a block that carries no output marker."""
    first = _first_line(text)
    prompted = bool(_PROMPT.match(first))
    if lang in PYTHON_LANGS:
        return "python", None
    if lang in SQL_LANGS:
        return "sql", None
    if lang in BASH_LANGS:
        return "bash", None
    if lang in OBJECTSCRIPT_LANGS:
        if prompted:
            return "terminal", None
        if _CLASS_DEF.match(first):
            return "unclassifiable", (
                "an ObjectScript class definition cannot run as terminal commands; mark it "
                '<!-- doc-test: skip reason="…" -->'
            )
        return "objectscript", None
    if lang in ("", "terminal") or lang in DATA_LANGS:
        if prompted:
            return "terminal", None
    if lang in DATA_LANGS:
        return "data", None
    if lang == "":
        return "unclassifiable", (
            "a fenced block with no language and no terminal prompt: give it a language "
            '(python, sql, objectscript, bash, text, …) or mark it <!-- doc-test: skip reason="…" -->'
        )
    if lang == "terminal":
        return "unclassifiable", "a terminal block whose first line is not a prompt such as USER>"
    return "unclassifiable", (
        f'the harness does not run `{lang}` blocks; mark it <!-- doc-test: skip reason="…" --> '
        "or show it inside a block the harness runs"
    )


def _python_expectations(block: Block) -> None:
    text = block.text
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        block.problems.append(f"python syntax error at line {block.line + (e.lineno or 1)}: {e.msg}")
        return
    ends = {stmt.end_lineno: stmt for stmt in tree.body}
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError) as e:
        block.problems.append(f"python tokenize error in block at line {block.line}: {e}")
        return
    for tok in tokens:
        if tok.type != tokenize.COMMENT:
            continue
        body = tok.string.lstrip("#").strip()
        if not body.startswith("→"):
            continue
        lineno = tok.start[0]
        doc_line = block.line + lineno
        if tok.line[: tok.start[1]].strip():
            block.problems.append(
                f"line {doc_line}: put the `# →` expectation on its own line after the expression"
            )
            continue
        stmt = ends.get(lineno - 1)
        if not isinstance(stmt, ast.Expr):
            block.problems.append(
                f"line {doc_line}: `# →` must follow a top-level expression ending on the line before it"
            )
            continue
        block.expectations.append(Expectation(lineno - 1, body[1:].strip()))


def _parse_marker(body: str, line: int, errors: list) -> Optional[_Marker]:
    if body in ("output", "run"):
        return _Marker(line, body)
    m = _SKIP.match(body)
    if m:
        reason = m.group("reason")
        if not reason or not reason.strip():
            errors.append(ParseError(line, 'a skip marker needs a reason: <!-- doc-test: skip reason="…" -->'))
            return None
        return _Marker(line, "skip", reason.strip())
    errors.append(
        ParseError(line, f"unknown doc-test marker {body!r} (use skip reason=\"…\", output, or run)")
    )
    return None


def parse_document(text: str) -> ParsedDoc:
    lines = text.split("\n")
    blocks: list = []
    errors: list = []
    pending: Optional[_Marker] = None
    conflicted = False
    last_runnable: Optional[Block] = None
    i = 0
    while i < len(lines):
        line = lines[i]
        m = _FENCE_OPEN.match(line)
        if m:
            indent = len(m.group("indent"))
            fence = m.group("fence")
            close = re.compile(r"^[ \t]*" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}[ \t]*$")
            j = i + 1
            while j < len(lines) and not close.match(lines[j]):
                j += 1
            if j >= len(lines):
                errors.append(ParseError(i + 1, "the fence opened here is never closed"))
                break
            body = []
            for raw in lines[i + 1 : j]:
                strip = min(indent, len(raw) - len(raw.lstrip(" \t")))
                body.append(raw[strip:])
            block = Block(
                index=len(blocks),
                line=i + 1,
                lang=m.group("info").lower(),
                text=("\n".join(body) + "\n") if body else "",
            )
            marker = None if conflicted else pending
            if marker and marker.kind == "output":
                block.kind = "output"
                if last_runnable is None:
                    errors.append(
                        ParseError(marker.line, "an output block must follow a block the harness runs; "
                                   "it follows no runnable block")
                    )
                else:
                    last_runnable.expected_output = block.text
            else:
                block.kind, problem = classify(block.lang, block.text)
                if problem:
                    block.problems.append(problem)
                if block.kind == "python":
                    _python_expectations(block)
                if marker and marker.kind == "run":
                    if block.kind != "bash":
                        errors.append(ParseError(marker.line, "a run marker applies only to a bash block"))
                    else:
                        block.run = True
                if marker and marker.kind == "skip":
                    block.skip_reason = marker.reason
                    block.problems = []
                    block.expectations = []
                runs = block.kind in RUNNABLE_KINDS and block.skip_reason is None
                if block.kind == "bash" and not block.run:
                    runs = False  # checked, not run: there is no output to compare
                last_runnable = block if runs else None
            blocks.append(block)
            pending, conflicted = None, False
            i = j + 1
            continue
        stripped = line.strip()
        mm = _MARKER.match(stripped)
        if mm:
            marker = _parse_marker(mm.group("body"), i + 1, errors)
            if pending is not None or conflicted:
                errors.append(ParseError(i + 1, "a block takes one doc-test marker; this is the second"))
                conflicted = True
            elif marker is not None:
                pending = marker
        elif stripped.startswith("<!--") and "doc-test" in stripped:
            errors.append(ParseError(i + 1, f"malformed doc-test marker: {stripped}"))
        elif stripped == "" or (stripped.startswith("<!--") and stripped.endswith("-->")):
            pass  # blank lines and other HTML comments may sit between a marker and its block
        else:
            if pending is not None:
                errors.append(
                    ParseError(pending.line, "this doc-test marker is not directly before a fenced block")
                )
            pending, conflicted = None, False
        i += 1
    if pending is not None:
        errors.append(ParseError(pending.line, "this doc-test marker is not directly before a fenced block"))
    return ParsedDoc(blocks, errors)


# ── comparators ────────────────────────────────────────────────────────────────


def _normalize(value):
    if value is None or isinstance(value, (str, bytes, int, float, complex)):
        return value
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return value
    if isinstance(value, (list, tuple)) or (hasattr(value, "__iter__") and hasattr(value, "__len__")):
        return [_normalize(v) for v in value]
    return value


def expectation_matches(value, expected: str) -> bool:
    """Whether `value` is the answer the doc wrote.

    A Python literal is compared by value, with tuples and DB-API rows read as lists (`[('Bob',)]` matches
    `[['Bob']]`). Text that is not a literal is compared with ``repr(value)`` and then ``str(value)``.
    """
    try:
        literal = ast.literal_eval(expected)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return expected in (repr(value), str(value))
    try:
        return bool(_normalize(value) == _normalize(literal))
    except Exception as e:  # an exotic __eq__: report as a mismatch with the reason, never as a match
        raise AssertionError(f"cannot compare {value!r} with {expected}: {e}") from e


def _lines(text: str, drop_blank: bool) -> list:
    out = [ln.rstrip() for ln in text.replace("\r\n", "\n").split("\n")]
    if drop_blank:
        return [ln for ln in out if ln.strip()]
    while out and not out[0].strip():
        out.pop(0)
    while out and not out[-1].strip():
        out.pop()
    return out


def output_matches(actual: str, expected: str, drop_blank: bool = False) -> bool:
    return _lines(actual, drop_blank) == _lines(expected, drop_blank)


def render_rows(rows) -> str:
    """SQL rows as the doc shows them: one row per line, values separated by ` | `, NULL for None."""
    return "".join(" | ".join("NULL" if v is None else str(v) for v in row) + "\n" for row in rows)


def split_sql(text: str) -> list:
    """Statements split on `;` outside single quotes, trailing `;` stripped (IRIS rejects it)."""
    statements, buf, quoted = [], [], False
    for ch in text:
        if ch == "'":
            quoted = not quoted
        if ch == ";" and not quoted:
            statements.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    statements.append("".join(buf))
    return [s.strip() for s in statements if s.strip()]


# ── terminal sessions ──────────────────────────────────────────────────────────

_ERROR_LINE = re.compile(r"^\s*(<[A-Z][A-Z0-9 ]*>.*|ERROR #\d+.*)$")
_NS_SWITCH = re.compile(
    r"(?i)(?:^|[\s:])(?:zn|znspace)\s+[\"@]|set\s+\$namespace\s*=|\$namespace\s*=\s*[\"@]"
)


def terminal_parts(text: str) -> tuple:
    """(commands, expected output) of a terminal transcript: prompt lines are commands, the rest is output."""
    commands, expected = [], []
    for raw in text.splitlines():
        m = _PROMPT.match(raw.strip())
        if m:
            commands.append(m.group("rest").strip())
        elif raw.strip():
            expected.append(raw.rstrip())
    return commands, "".join(ln + "\n" for ln in expected)


def objectscript_commands(text: str) -> list:
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def session_output(raw: str, namespace: str = "IVG460DOC") -> str:
    """What `iris session` printed, without its banner, prompts and the blank lines it adds."""
    prompt = re.compile(r"(?m)^" + re.escape(namespace) + r"(?:\s+\d+[a-z]\d+)?>")
    first = prompt.search(raw)
    body = raw[first.end():] if first else raw
    body = prompt.sub("", body)
    return "".join(ln + "\n" for ln in _lines(body, drop_blank=True))


def session_error(raw: str, namespace: str = "IVG460DOC") -> Optional[str]:
    for ln in raw.splitlines():
        if _ERROR_LINE.match(ln):
            return ln.strip()
    if re.search(r"(?m)^" + re.escape(namespace) + r"\s+\d+[a-z]\d+>", raw):
        return "the session ended in an error frame"
    return None


def switches_namespace(text: str) -> bool:
    return bool(_NS_SWITCH.search(text))


#: Commands a doc-test run never issues: they create, stop or remove containers (layer 3 runs installs and
#: `docker compose`), or open a session in a namespace other than the scratch one.
_REFUSED = re.compile(
    r"docker[ -]compose|docker\s+(?:run|stop|restart|rm|kill|pause|start)\b|\s-U\s*(?!IVG460)\S"
)
_VALUE_FLAGS = {"-e", "--env", "-u", "--user", "-w", "--workdir", "--env-file"}


def _docker_targets(script: str) -> list:
    """Containers a script's `docker exec|logs|inspect|cp` commands address."""
    targets = []
    for line in script.splitlines():
        words = line.split()
        for i, w in enumerate(words[:-1]):
            if w != "docker" or words[i + 1] not in ("exec", "logs", "inspect", "cp"):
                continue
            rest = words[i + 2:]
            j = 0
            while j < len(rest) and rest[j].startswith("-"):
                j += 2 if rest[j] in _VALUE_FLAGS else 1
            if words[i + 1] == "cp":
                targets += [a.split(":")[0] for a in rest[j:j + 2] if ":" in a]
            elif j < len(rest):
                targets.append(rest[j])
    return targets


def bash_refusal(script: str, container: str = "ivg-iris-enterprise") -> Optional[str]:
    """Why a run-marked bash block must not run, or None. Only the configured test container may be addressed."""
    m = _REFUSED.search(script)
    if m:
        return (f"refused to run: `{m.group(0).strip()}` (layer 1 runs only in the scratch namespace; installs "
                "and docker compose run in layer 3)")
    for target in _docker_targets(script):
        if target != container:
            return f"refused to run: the block addresses container `{target}`, not the test container {container}"
    return None


def _bash_script(text: str) -> tuple:
    """(script, expected output): `$ ` prompt lines are commands and the others output, when prompts are used."""
    lines = text.splitlines()
    if any(re.match(r"^\s*\$ ", ln) for ln in lines):
        cmds = [re.sub(r"^\s*\$ ", "", ln) for ln in lines if re.match(r"^\s*\$ ", ln)]
        out = [ln for ln in lines if not re.match(r"^\s*\$ ", ln) and ln.strip()]
        return "\n".join(cmds) + "\n", "".join(ln + "\n" for ln in out)
    return text, ""


# ── running ────────────────────────────────────────────────────────────────────


@dataclass
class BlockResult:
    index: int
    line: int
    kind: str
    status: str  # ok | checked | failed | skipped | unclassifiable | data | output
    message: str = ""


@dataclass
class Summary:
    found: int = 0
    ok: int = 0
    checked: int = 0
    failed: int = 0
    unclassifiable: int = 0
    skipped: int = 0
    data: int = 0
    skips: dict = field(default_factory=dict)
    failures: list = field(default_factory=list)  # (line, first error line)


def summarize(results) -> Summary:
    s = Summary(found=len(results))
    for r in results:
        if r.status == "ok":
            s.ok += 1
        elif r.status == "checked":
            s.checked += 1
        elif r.status == "failed":
            s.failed += 1
            s.failures.append((r.line, r.message.splitlines()[0] if r.message else ""))
        elif r.status == "unclassifiable":
            s.unclassifiable += 1
            s.failures.append((r.line, r.message))
        elif r.status == "skipped":
            s.skipped += 1
            s.skips[r.message] = s.skips.get(r.message, 0) + 1
        elif r.status == "data":
            s.data += 1
    return s


_PROTO = "\x00DOCTEST "


class _PythonWorker:
    """One interpreter per document: a subprocess that runs python and sql blocks in order."""

    def __init__(self, python: str, workdir: Path, settings: dict):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("IVG_", "IRIS_"))}
        env["PYTHONPATH"] = str(REPO)
        env["DOCTEST_WORKER_SETTINGS"] = json.dumps(settings)
        env["PYTHONUNBUFFERED"] = "1"
        if settings.get("connect"):
            env.update({
                "IVG_IRIS_HOST": settings["host"], "IVG_IRIS_PORT": str(settings["port"]),
                "IVG_IRIS_NAMESPACE": settings["namespace"], "IRIS_HOST": settings["host"],
                "IRIS_PORT": str(settings["port"]), "IRIS_NAMESPACE": settings["namespace"],
                "IRIS_USERNAME": settings["username"], "IRIS_PASSWORD": settings["password"],
            })
        self.proc = subprocess.Popen(
            [python, "-u", "-m", "tests.docs.harness", "--worker"],
            cwd=workdir, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        self.lines: queue.Queue = queue.Queue()
        self.dead: Optional[str] = None
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in self.proc.stdout:
            if line.startswith(_PROTO):
                self.lines.put(line[len(_PROTO):])
        self.lines.put(None)

    def request(self, req: dict, timeout: int) -> dict:
        if self.dead:
            return {"ok": False, "error": f"not run: {self.dead}"}
        try:
            self.proc.stdin.write(json.dumps(req) + "\n")
            self.proc.stdin.flush()
            line = self.lines.get(timeout=timeout)
        except queue.Empty:
            self.dead = f"the document's interpreter was killed after a block at line {req['line']} ran {timeout}s"
            self.proc.kill()
            return {"ok": False, "error": f"timed out after {timeout}s"}
        except (BrokenPipeError, OSError) as e:
            self.dead = f"the document's interpreter exited ({e})"
            return {"ok": False, "error": self.dead}
        if line is None:
            self.dead = f"the document's interpreter exited (code {self.proc.wait()})"
            return {"ok": False, "error": self.dead}
        return json.loads(line)

    def close(self):
        if self.proc.poll() is None:
            try:
                self.proc.stdin.close()
                self.proc.wait(timeout=30)
            except (OSError, subprocess.TimeoutExpired):
                self.proc.kill()


def _link_repo(workdir: Path) -> None:
    """The doc's code runs in a scratch directory: files it writes land there, files it reads (`examples/…`)
    resolve through links to the checkout."""
    workdir.mkdir(parents=True, exist_ok=True)
    for entry in REPO.iterdir():
        if entry.name.startswith("."):
            continue
        target = workdir / entry.name
        if not target.exists() and not target.is_symlink():
            target.symlink_to(entry)


def run_iris_session(commands: list, config: Config, timeout: int) -> str:
    script = "\n".join(commands) + "\nhalt\n"
    out = subprocess.run(
        ["docker", "exec", "-i", config.container, "iris", "session", "IRIS", "-U", config.namespace],
        input=script, capture_output=True, text=True, timeout=timeout,
    )
    return out.stdout + out.stderr


def _check_bash(script: str) -> Optional[str]:
    out = subprocess.run(["bash", "-n"], input=script, capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        return (out.stderr.strip().splitlines() or ["bash -n failed"])[0]
    return None


def run_document(
    doc: ParsedDoc,
    *,
    workdir: Path,
    connect: bool = True,
    python: str = sys.executable,
    config: Optional[Config] = None,
    label: str = "doc",
    block_timeout: Optional[int] = None,
    notes: Optional[list] = None,
) -> list:
    """Run every block of a parsed document; one BlockResult per block, in order."""
    config = config or load_config()
    timeout = block_timeout or config.block_timeout
    workdir = Path(workdir)
    _link_repo(workdir)
    settings = {
        "connect": connect, "host": config.host, "port": config.port, "namespace": config.namespace,
        "username": config.username, "password": config.password, "label": label,
    }
    worker: Optional[_PythonWorker] = None
    results = []
    try:
        for b in doc.blocks:
            def done(status, message=""):
                results.append(BlockResult(b.index, b.line, b.kind, status, message))

            if b.kind == "output":
                done("output")
                continue
            if b.skip_reason is not None:
                done("skipped", b.skip_reason)
                continue
            if b.kind == "unclassifiable":
                done("unclassifiable", b.problems[0])
                continue
            if b.kind == "data":
                done("data")
                continue
            if b.problems:
                done("failed", "; ".join(b.problems))
                continue
            if b.kind in ("python", "sql"):
                if worker is None:
                    worker = _PythonWorker(python, workdir, settings)
                req = {"op": b.kind, "code": b.text, "line": b.line,
                       "expectations": [[e.line, e.expected] for e in b.expectations],
                       "expected_output": b.expected_output}
                resp = worker.request(req, timeout)
                if notes is not None:
                    notes.extend(resp.get("notes", []))
                done("ok" if resp.get("ok") else "failed", resp.get("error", ""))
                continue
            if b.kind in ("objectscript", "terminal"):
                if not connect:
                    done("failed", "this run has no IRIS")
                    continue
                if switches_namespace(b.text):
                    done("failed", f"refused to run: the block switches namespace (the harness runs only in "
                                   f"{config.namespace}); mark it skip with a reason")
                    continue
                if b.kind == "terminal":
                    commands, expected = terminal_parts(b.text)
                    if b.expected_output is not None:
                        expected = b.expected_output
                else:
                    commands, expected = objectscript_commands(b.text), b.expected_output
                try:
                    raw = run_iris_session(commands, config, timeout)
                except subprocess.TimeoutExpired:
                    done("failed", f"iris session timed out after {timeout}s")
                    continue
                err = session_error(raw, config.namespace)
                actual = session_output(raw, config.namespace)
                if err and not (expected and err in expected):
                    done("failed", f"line {b.line}: {err}")
                elif expected is not None and not output_matches(actual, expected, drop_blank=True):
                    done("failed", f"line {b.line}: output differs; expected {expected!r}, got {actual!r}")
                else:
                    done("ok")
                continue
            if b.kind == "bash":
                script, prompt_output = _bash_script(b.text)
                problem = _check_bash(script)
                if problem:
                    done("failed", f"line {b.line}: {problem}")
                    continue
                if not b.run:
                    done("checked")  # syntax only: counted apart from blocks that ran
                    continue
                refusal = bash_refusal(script, config.container)
                if refusal:
                    done("failed", refusal)
                    continue
                env = dict(os.environ, IVG_IRIS_HOST=config.host, IVG_IRIS_PORT=str(config.port),
                           IVG_IRIS_NAMESPACE=config.namespace)
                try:
                    out = subprocess.run(["bash", "-e"], input=script, cwd=workdir, env=env,
                                         capture_output=True, text=True, timeout=timeout)
                except subprocess.TimeoutExpired:
                    done("failed", f"timed out after {timeout}s")
                    continue
                expected = b.expected_output if b.expected_output is not None else (prompt_output or None)
                if out.returncode != 0:
                    tail = (out.stderr.strip().splitlines() or [f"exit {out.returncode}"])[-1]
                    done("failed", f"line {b.line}: exit {out.returncode}: {tail}")
                elif expected is not None and not output_matches(out.stdout, expected):
                    done("failed", f"line {b.line}: output differs; expected {expected!r}, got {out.stdout!r}")
                else:
                    done("ok")
                continue
            done("failed", f"no runner for kind {b.kind}")
    finally:
        if worker is not None:
            worker.close()
    return results


# ── the scratch namespace: emptied before each document ────────────────────────

_BASELINE_DIR = "ivg-doctest"  # under the instance's mgr directory


def _baseline_files(ns: str) -> tuple:
    d = f'$zu(12)_"{_BASELINE_DIR}/"'
    return d, f'{d}_"{ns}-baseline.xml"'


def _global_list_cmd() -> str:
    return ('set l="",rs=##class(%ResultSet).%New("%SYS.GlobalQuery:NameSpaceList") '
            'do rs.Execute($namespace,"*",0) for  quit:\'rs.Next()  set l=l_$lb(rs.Get("Name"))')


def _graph_counts(config: Config) -> tuple:
    import iris

    conn = iris.connect(config.host, config.port, config.namespace, config.username, config.password)
    try:
        cur = conn.cursor()
        counts = []
        for table in ("Graph_KG.nodes", "Graph_KG.rdf_edges"):
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            counts.append(int(cur.fetchone()[0]))
        return tuple(counts)
    finally:
        conn.close()


def snapshot_namespace(config: Config) -> None:
    """Record the namespace as it is now as the state each document starts from. Refuses unless it holds no graph."""
    nodes, edges = _graph_counts(config)
    if nodes or edges:
        raise RuntimeError(
            f"{config.namespace} holds {nodes} nodes and {edges} edges and has no doc-test baseline; recreate it "
            f"(scripts/dev/make_scratch_namespace.py) before the first run"
        )
    d, f = _baseline_files(config.namespace)
    raw = run_iris_session([
        f"set dir={d},file={f}",
        "do ##class(%File).CreateDirectoryChain(dir)",
        "kill ^IVGDocTestBaseline do $system.OBJ.GetClassList(.c,\"/system=0/percent=0/mapped=0\") "
        "merge ^IVGDocTestBaseline(\"classes\")=c",
        _global_list_cmd(),
        'set items="" for i=1:1:$ll(l) set items=items_$s(items="":"",1:",")_$lg(l,i)_".GBL"',
        'set sc=$system.OBJ.Export(items,file,"-d") write $s(sc:"SNAPSHOT-OK",1:"SNAPSHOT-FAIL "_'
        '$system.Status.GetErrorText(sc)),!',
    ], config, 300)
    if "SNAPSHOT-OK" not in raw:
        raise RuntimeError(f"snapshot of {config.namespace} failed: {session_output(raw, config.namespace)}")


def reset_namespace(config: Config) -> list:
    """Kill every non-system global in the scratch namespace, reload the baseline, drop classes a doc compiled.

    The namespace has its own database (scripts/dev/make_scratch_namespace.py), so its globals are this harness's
    alone. Returns the classes dropped. Checks the result: the graph tables are empty afterwards."""
    if not config.namespace.startswith(SCRATCH_PREFIX):
        raise ValueError(f"refusing to empty {config.namespace}")
    d, f = _baseline_files(config.namespace)
    probe = run_iris_session([f'write $s(##class(%File).Exists({f}):"HAVE-BASELINE",1:"NO-BASELINE"),!'], config, 60)
    if "NO-BASELINE" in probe:
        snapshot_namespace(config)
    raw = run_iris_session([
        f"set file={f}",
        _global_list_cmd(),
        'for i=1:1:$ll(l) kill @("^"_$lg(l,i))',
        'set sc=$system.OBJ.Load(file,"-d") if \'sc write "RESET-FAIL ",$system.Status.GetErrorText(sc),! halt',
        'do $system.OBJ.GetClassList(.c,"/system=0/percent=0/mapped=0") set k="" for  set k=$o(c(k)) quit:k=""  '
        'if \'$d(^IVGDocTestBaseline("classes",k)) set sc=$system.OBJ.Delete(k,"-d") write "DROPPED ",k,!',
        'write "RESET-OK",!',
    ], config, 600)
    if "RESET-OK" not in raw:
        raise RuntimeError(f"reset of {config.namespace} failed: {session_output(raw, config.namespace)[-800:]}")
    nodes, edges = _graph_counts(config)
    if nodes or edges:
        raise RuntimeError(f"{config.namespace} still holds {nodes} nodes, {edges} edges after the reset")
    return re.findall(r"DROPPED (\S+)", raw)


# ── the worker process ─────────────────────────────────────────────────────────


def _format_exception(exc: BaseException, label: str) -> str:
    import traceback

    line = None
    for frame in traceback.extract_tb(exc.__traceback__):
        if frame.filename == label:
            line = frame.lineno
    if isinstance(exc, SyntaxError) and exc.filename == label:
        line = exc.lineno
    head = f"line {line}: " if line else ""
    return f"{head}{type(exc).__name__}: {exc}".splitlines()[0] if str(exc) else f"{head}{type(exc).__name__}"


def _install_connection_redirect(settings: dict, notes: list):
    """Every connection the document opens goes to the scratch namespace; returns a connect() for sql blocks."""
    import iris

    host, port, ns = settings["host"], settings["port"], settings["namespace"]
    keys = ("hostname", "port", "namespace", "username", "password")

    def wrap(original, name):
        def connect(*args, **kwargs):
            if not settings["connect"]:
                raise RuntimeError("this doc-test run has no IRIS")
            given = dict(kwargs)
            pos = list(args)
            if pos and isinstance(pos[0], str) and ("/" in pos[0] or ":" in pos[0]) and len(pos) <= 3:
                given["connectionstr"] = pos.pop(0)
                for k, v in zip(("username", "password"), pos):
                    given[k] = v
            else:
                for k, v in zip(keys, pos):
                    given[k] = v
            requested = given.get("connectionstr") or (
                f"{given.get('hostname')}:{given.get('port')}/{given.get('namespace')}")
            extra = {k: v for k, v in given.items() if k not in keys and k != "connectionstr"}
            notes.append(f"{name}({requested}) redirected to {host}:{port}/{ns}")
            return original(hostname=host, port=port, namespace=ns,
                            username=given.get("username", settings["username"]),
                            password=given.get("password", settings["password"]), **extra)
        return connect

    raw_connect = iris.connect
    iris.connect = wrap(raw_connect, "iris.connect")
    if hasattr(iris, "createConnection"):
        iris.createConnection = wrap(iris.createConnection, "iris.createConnection")
    try:
        import iris.dbapi as dbapi

        dbapi.connect = wrap(dbapi.connect, "iris.dbapi.connect")
    except ImportError:
        pass

    def sql_connect():
        if not settings["connect"]:
            raise RuntimeError("this doc-test run has no IRIS")
        return raw_connect(hostname=host, port=port, namespace=ns,
                           username=settings["username"], password=settings["password"])
    return sql_connect


def _run_python(req: dict, g: dict, label: str) -> dict:
    first = req["line"]
    expectations = {first + ln: exp for ln, exp in req.get("expectations", [])}
    out, err = io.StringIO(), io.StringIO()
    mismatches = []
    try:
        tree = ast.parse(req["code"], filename=label)
        ast.increment_lineno(tree, first)
        with redirect_stdout(out), redirect_stderr(err):
            for stmt in tree.body:
                if isinstance(stmt, ast.Expr) and stmt.end_lineno in expectations:
                    expected = expectations[stmt.end_lineno]
                    value = eval(compile(ast.Expression(stmt.value), label, "eval"), g)
                    try:
                        ok = expectation_matches(value, expected)
                    except AssertionError as e:
                        ok, expected = False, f"{expected} ({e})"
                    if not ok:
                        mismatches.append(f"line {stmt.end_lineno + 1}: expected {expected}, got {value!r}")
                else:
                    exec(compile(ast.Module([stmt], type_ignores=[]), label, "exec"), g)
    except BaseException as e:  # the doc's code failed: report it, the next block still runs
        if isinstance(e, KeyboardInterrupt):
            raise
        return {"ok": False, "error": _format_exception(e, label), "stdout": out.getvalue()}
    if mismatches:
        return {"ok": False, "error": "; ".join(mismatches), "stdout": out.getvalue()}
    expected_output = req.get("expected_output")
    if expected_output is not None and not output_matches(out.getvalue(), expected_output):
        return {"ok": False, "stdout": out.getvalue(),
                "error": f"line {first}: output differs; expected {expected_output!r}, got {out.getvalue()!r}"}
    return {"ok": True, "stdout": out.getvalue()}


def _run_sql(req: dict, state: dict, sql_connect) -> dict:
    try:
        if state.get("conn") is None:
            state["conn"] = sql_connect()
        cur = state["conn"].cursor()
        rows = None
        for statement in split_sql(req["code"]):
            cur.execute(statement)
            rows = cur.fetchall() if cur.description else None
        state["conn"].commit()
    except Exception as e:
        return {"ok": False, "error": f"line {req['line']}: {type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}"}
    expected = req.get("expected_output")
    actual = render_rows(rows or [])
    if expected is not None and not output_matches(actual, expected):
        return {"ok": False, "error": f"line {req['line']}: rows differ; expected {expected!r}, got {actual!r}"}
    return {"ok": True}


def _worker_main() -> int:
    settings = json.loads(os.environ["DOCTEST_WORKER_SETTINGS"])
    label = settings.get("label", "doc")
    proto = sys.stdout
    notes: list = []
    sql_connect = _install_connection_redirect(settings, notes)
    g = {"__name__": "__main__", "__builtins__": __builtins__}
    state: dict = {}
    for line in sys.stdin:
        req = json.loads(line)
        resp = _run_python(req, g, label) if req["op"] == "python" else _run_sql(req, state, sql_connect)
        resp["notes"], notes[:] = list(notes), []
        proto.write(_PROTO + json.dumps(resp, default=str) + "\n")
        proto.flush()
    return 0


# ── the R042 known-failures ratchet ────────────────────────────────────────────

RUN_KNOWN = Path(__file__).with_name("run_known_failures.txt")
_OBJ_ID = re.compile(r"\bid='?\d+'?")
_HEX = re.compile(r"\b0x[0-9a-fA-F]+\b")


def normalize_failure(message: str, workdir: Optional[str] = None, namespace: Optional[str] = None) -> str:
    """A failure's first line with what differs between identical runs (object ids, addresses, the run's
    scratch directory, the scratch namespace) replaced, so a rerun keys the same failure the same way."""
    first = message.splitlines()[0].strip() if message else ""
    if workdir:  # the document's scratch directory, then the run's directory above it
        first = first.replace(str(workdir), "<workdir>").replace(str(Path(workdir).parent), "<run>")
    if namespace:  # IVG_DOCTEST_NAMESPACE may name another scratch namespace
        first = re.sub(r"\b" + re.escape(namespace) + r"\b", "<namespace>", first)
    return _HEX.sub("0x...", _OBJ_ID.sub("id=...", first))


def failure_keys(doc: str, results, workdir: Optional[str] = None, namespace: Optional[str] = None) -> set:
    """(doc, block index, normalized first error line) for each block that failed or could not be classified."""
    return {(doc, r.index, normalize_failure(r.message, workdir, namespace))
            for r in results if r.status in ("failed", "unclassifiable")}


def ratchet(doc: str, found: set, known: set) -> tuple:
    """(new, gone) for one document: failures not on the list, and listed failures that no longer occur."""
    listed = {k for k in known if k[0] == doc}
    return sorted(found - listed), sorted(listed - found)


def load_known(path: Path = RUN_KNOWN) -> set:
    """`doc | block index | first error line` per line; `#` comments and blank lines ignored."""
    entries = set()
    if not path.exists():
        return entries
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        doc, index, message = line.split(" | ", 2)
        entries.add((doc.strip(), int(index), message.strip()))
    return entries


def format_known(keys) -> str:
    return "".join(f"{d} | {i} | {m}\n" for d, i, m in sorted(keys))


# ── report ─────────────────────────────────────────────────────────────────────


def render_report(report: dict) -> str:
    """Markdown table from the JSON test_docs_run.py writes (IVG_DOCTEST_JSON)."""
    rows = ["| Doc | Blocks | Ran OK | Bash checked | Failed | Unclassifiable | Skipped | Data/output | First error |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
    for path, d in report.items():
        s = d["summary"]
        first = (d.get("parse_errors") or [None])[0] or (s["failures"][0] if s["failures"] else None)
        msg = first[1] if first else ""
        first_txt = msg if msg.startswith("line ") else (f"line {first[0]}: {msg}" if first else "")
        first_txt = first_txt.split(";")[0][:120].replace("|", "\\|").replace("`", "'")
        first_txt = f"`{first_txt}`" if first_txt else ""
        rows.append(f"| `{path}` | {s['found']} | {s['ok']} | {s.get('checked', 0)} | {s['failed']} | "
                    f"{s['unclassifiable']} | "
                    f"{s['skipped']} | {s['data'] + d.get('outputs', 0)} | {first_txt} |")
    return "\n".join(rows) + "\n"


if __name__ == "__main__":
    if sys.argv[1:] == ["--worker"]:
        sys.exit(_worker_main())
    if len(sys.argv) == 3 and sys.argv[1] == "render":
        print(render_report(json.loads(Path(sys.argv[2]).read_text())), end="")
        sys.exit(0)
    print("usage: python -m tests.docs.harness --worker | render <report.json>", file=sys.stderr)
    sys.exit(2)
