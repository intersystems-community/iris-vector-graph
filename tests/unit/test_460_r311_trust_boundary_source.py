"""4.6.0 R311 [SECURITY]: the class code that reads `^ArnoAccel("lib_path")` and `^IVG.CypherCompiled` trusts only what a
role with the database at RW cannot plant. tests/e2e/test_460_r311_trust_boundary_e2e.py runs it; this reads the source
for the paths the E2E file cannot reach (For's use of an "ok" entry, every load that names no path).
"""

from __future__ import annotations

import re
from pathlib import Path

KG = Path(__file__).resolve().parents[2] / "iris_src/src/Graph/KG"


def _method(path: Path, name: str) -> str:
    src = path.read_text()
    start = src.index(f"ClassMethod {name}(")
    end = src.find("\nClassMethod ", start + 1)
    return src[start : end if end > 0 else None]


def test_libpath_takes_the_recorded_path_only_when_trusted():
    body = _method(KG / "ArnoAccel.cls", "LibPath")
    assert "..TrustedPath(tRecorded)" in body


def test_no_load_passes_the_recorded_global_as_its_path():
    """A path given to Load is not checked (its caller holds %System_CallOut), so no class may hand it the global."""
    hits = [f"{p.name}:{i}" for p in KG.rglob("*.cls") for i, line in enumerate(p.read_text().splitlines(), 1)
            if re.search(r"Load\(\s*\^ArnoAccel\(", line)]
    assert hits == []


def test_an_ok_entry_answers_only_its_own_class():
    body = _method(KG / "Cypher" / "Gen.cls", "For")
    assert '$Select($List(tE, 1)=(..#PACKAGE_".P"_tH): $List(tE, 1), 1: "")' in body
    assert 'Quit:$List(tE,4)="ok" $List(tE, 1)' not in body


def test_build_compiles_only_lines_whose_digest_it_was_handed():
    gen = KG / "Cypher" / "Gen.cls"
    assert "Build(tH, tDigest)" in _method(gen, "For")
    build = _method(gen, "Build")
    assert build.startswith('ClassMethod Build(pH As %String, pDigest As %String = "")')
    check, compile_ = build.index("..Digest(.tL) '= pDigest"), build.index("LoadStream")
    assert check < compile_
    assert '$Order(^IVG.CypherCompiled(pH, "src"' not in build, "the stream is written from the checked copy"
