"""Deploy IVG's ObjectScript classes from the installed package, over the connection.

The ``.cls`` sources ship inside the wheel (``iris_vector_graph/iris_src/src``) and
travel to the server as ``%Stream.GlobalCharacter`` streams loaded with
``%SYSTEM.OBJ.LoadStream``, so nothing has to be copied onto the server's
filesystem first. (``%Compiler.UDL.TextServices.SetTextFromString`` is not used:
it rejects ``[ SqlSchemaName = ... ]`` class headers and some argumented QUITs
that ``%SYSTEM.OBJ.Load`` accepts.) Only classes the server can compile are
compiled: a class with ``[ Language = python ]`` methods needs embedded Python, a
class naming ``%AI.*`` needs those classes installed, and a class that names a
skipped class is skipped with it. A fingerprint of the compiled source set is kept
in :data:`MARKER`, and an unchanged set is not reinstalled.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: ``(global, subscript)`` holding the fingerprint of the last source set compiled.
MARKER = ("^IVG.Deploy", "fingerprint")

#: A class that every deploy compiles; its absence means the marker is stale.
SENTINEL_CLASS = "Graph.KG.PageRank"

#: Characters per stream ``Write``; keeps each call well under the string limit.
STREAM_CHUNK = 16000

_PY_METHOD = re.compile(r"\[[^\]\n]*\bLanguage\s*=\s*python\b", re.IGNORECASE)
_AI_CLASS = re.compile(r"(?<![\w.])%AI(?:\.\w+)+")
_FUNCTIONAL_INDEX = re.compile(r"^Class\s+\S+\s+Extends\s+\(?[^\n]*%(?:Library\.)?FunctionalIndex", re.M)


def packaged_class_dir() -> Optional[Path]:
    """The directory of ``.cls`` sources: the wheel's copy, else the source tree's."""
    pkg = Path(__file__).resolve().parent.parent
    for candidate in (pkg / "iris_src" / "src", pkg.parent / "iris_src" / "src"):
        if (candidate / "Graph" / "KG").is_dir():
            return candidate
    return None


def read_class_sources(class_dir: Path) -> Dict[str, str]:
    """``{class name: UDL text}`` for every ``.cls`` under ``class_dir``."""
    sources = {}
    for path in sorted(class_dir.rglob("*.cls")):
        name = ".".join(path.relative_to(class_dir).with_suffix("").parts)
        sources[name] = path.read_text(encoding="utf-8")
    return sources


def uses_embedded_python(source: str) -> bool:
    return bool(_PY_METHOD.search(source))


def _names(name: str) -> re.Pattern:
    return re.compile(r"(?<![\w.%])" + re.escape(name) + r"(?![\w])")


def select_classes(
    sources: Dict[str, str],
    embedded_python: bool,
    class_exists: Callable[[str], bool],
) -> Tuple[List[str], Dict[str, str]]:
    """Split ``sources`` into the classes to compile and ``{skipped: reason}``."""
    skipped: Dict[str, str] = {}
    for name, text in sources.items():
        if not embedded_python and uses_embedded_python(text):
            skipped[name] = "needs embedded Python, which this server does not have"
            continue
        missing = sorted({c for c in _AI_CLASS.findall(text) if not class_exists(c)})
        if missing:
            skipped[name] = f"needs {', '.join(missing)}, not installed on this server"

    changed = True
    while changed:
        changed = False
        for name, text in sources.items():
            if name in skipped:
                continue
            for dep in list(skipped):
                if _names(dep).search(text):
                    skipped[name] = f"depends on {dep}, which is skipped"
                    changed = True
                    break

    selected = [n for n in sources if n not in skipped]
    return selected, skipped


def fingerprint(sources: Dict[str, str]) -> str:
    h = hashlib.sha256()
    for name in sorted(sources):
        h.update(name.encode())
        h.update(b"\0")
        h.update(sources[name].encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def embedded_python_available(iris_obj) -> bool:
    """Whether the server can run ``[ Language = python ]``; ``IVG_EMBEDDED_PYTHON`` overrides."""
    override = os.environ.get("IVG_EMBEDDED_PYTHON", "").strip()
    if override in ("0", "1"):
        return override == "1"
    try:
        iris_obj.classMethodObject("%SYS.Python", "Import", "sys")
        return True
    except Exception as exc:
        logger.info("Embedded Python unavailable (%s); skipping Python classes", exc)
        return False


def class_exists(iris_obj, name: str) -> bool:
    try:
        return bool(int(iris_obj.classMethodValue("%Dictionary.CompiledClass", "%ExistsId", name)))
    except Exception:
        return False


@dataclass
class DeployResult:
    deployed: bool = False
    unchanged: bool = False
    compiled: List[str] = field(default_factory=list)
    skipped: Dict[str, str] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)


def _ok(iris_obj, status) -> bool:
    try:
        return bool(int(iris_obj.classMethodValue("%SYSTEM.Status", "IsOK", status)))
    except Exception:
        return False


def _error_text(iris_obj, status) -> str:
    try:
        return str(iris_obj.classMethodValue("%SYSTEM.Status", "GetErrorText", status) or "")
    except Exception:
        return str(status)


def upload_and_compile(
    iris_obj, namespace: str, sources: Dict[str, str], force: bool = False
) -> DeployResult:
    """Load ``sources`` into ``namespace`` and compile them as one list.

    Skipped when :data:`MARKER` already holds this source set's fingerprint and
    :data:`SENTINEL_CLASS` is compiled, unless ``force``. The marker is written only
    after a clean compile, so a failed deploy is retried next time.
    """
    result = DeployResult(compiled=sorted(sources))
    fp = fingerprint(sources)
    if not force:
        try:
            current = iris_obj.get(*MARKER)
        except Exception:
            current = None
        if current == fp and class_exists(iris_obj, SENTINEL_CLASS):
            result.unchanged = True
            return result

    for name in sorted(sources):
        text = sources[name]
        stream = iris_obj.classMethodObject("%Stream.GlobalCharacter", "%New")
        for i in range(0, len(text), STREAM_CHUNK):
            stream.invoke("Write", text[i : i + STREAM_CHUNK])
        stream.invoke("Rewind")
        # Load only; compiling one class at a time would fail on forward references.
        status = iris_obj.classMethodValue("%SYSTEM.OBJ", "LoadStream", stream, "-d")
        if not _ok(iris_obj, status):
            result.errors.append(f"{name}: {_error_text(iris_obj, status)}")

    # A functional-index type has to exist before a class indexing with it compiles
    # (7e13672: `Graph.KG.Edge` failed every LoadDir because it sorted first).
    first = sorted(n for n in sources if _FUNCTIONAL_INDEX.search(sources[n]))
    rest = sorted(n for n in sources if n not in first)
    for batch in (first, rest):
        if not batch:
            continue
        spec = ",".join(f"{n}.cls" for n in batch)
        # A method generator may read another class of the batch (%AI.ToolSet's
        # %Discover reads Graph.KG.MCPTools); CompileList compiles every class it
        # can, so a second pass finds those compiled. Only its errors count.
        for _ in range(2):
            status = iris_obj.classMethodValue("%SYSTEM.OBJ", "CompileList", spec, "ck-d")
            if _ok(iris_obj, status):
                break
        else:
            result.errors.append(_error_text(iris_obj, status))

    result.deployed = True
    if not result.errors:
        iris_obj.set(fp, *MARKER)
    return result


def deploy_packaged_classes(conn, force: Optional[bool] = None) -> Optional[DeployResult]:
    """Deploy the packaged classes into ``conn``'s namespace; None when none ship.

    ``force`` defaults to ``IVG_FORCE_CLASS_DEPLOY=1``.
    """
    class_dir = packaged_class_dir()
    if class_dir is None:
        return None
    if force is None:
        force = os.environ.get("IVG_FORCE_CLASS_DEPLOY", "") == "1"

    import iris as _iris_pkg

    raw = conn._connection if hasattr(conn, "_connection") else conn
    iris_obj = _iris_pkg.createIRIS(raw)
    namespace = str(iris_obj.classMethodValue("%SYSTEM.SYS", "NameSpace"))

    sources = read_class_sources(class_dir)
    selected, skipped = select_classes(
        sources,
        embedded_python=embedded_python_available(iris_obj),
        class_exists=lambda n: class_exists(iris_obj, n),
    )
    result = upload_and_compile(iris_obj, namespace, {n: sources[n] for n in selected}, force)
    result.skipped = skipped
    for name, reason in sorted(skipped.items()):
        logger.info("ObjectScript class %s not deployed: %s", name, reason)
    for err in result.errors:
        logger.warning("ObjectScript deploy: %s", err)
    return result
