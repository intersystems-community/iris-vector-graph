"""ADR-0015: the registry of temporal-only predicates has one owner and one answer.

Source-level, no container. The behaviour is in ``tests/e2e/test_temporal_structural_optout_e2e.py``.

* ``^IVG.TemporalStructural`` is written (Set/Kill) by ``Graph.KG.TemporalPolicy`` alone, and killed wholesale by
  ``Eraser.EraseAll``; ``EraseGraph`` keeps it (configuration, as ``fhir_graphs`` is).
* It is in the ``GraphStores`` inventory and in the snapshot's store plan and export list.
* Every place that decides "does this triple get a structural row?" asks ``TemporalPolicy``: the write path, the upgrade
  backfill, the purge's retirement, the bare-node retirement.
* ADR-0011 still holds: nothing new writes ``^KG("out"/"in"/"gen"/"degc")``.
* No new data-changing path swallows its error: a ``Catch`` in ``TemporalPolicy`` rethrows or returns the status, and the
  Python API has no ``except Exception`` that returns ``False`` / ``None``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
KG = REPO / "iris_src" / "src" / "Graph" / "KG"
POLICY = KG / "TemporalPolicy.cls"
GLOBAL = "^IVG.TemporalStructural"


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _code(text: str) -> str:
    out = []
    for line in text.splitlines():
        if line.lstrip().startswith(";") or line.lstrip().startswith("///"):
            continue
        out.append(re.sub(r"(?<!:)//.*$", "", line))
    return "\n".join(out)


def _method(text: str, name: str) -> str:
    start = text.index(f"ClassMethod {name}(")
    rest = text[start + 1 :]
    end = rest.find("\nClassMethod ")
    return rest if end == -1 else rest[:end]


def test_the_policy_class_exists_with_its_methods():
    text = _src(POLICY)
    for name in ("IsStructural", "Set", "Apply", "List", "ListAll", "DropRows", "BackfillRows"):
        assert f"ClassMethod {name}(" in text, f"TemporalPolicy.{name} is missing"


_WRITE = re.compile(r"(?i:\bset\b|\bs\b|\bkill\b|\bk\b|\bmerge\b)[^\n]*?" + re.escape(GLOBAL) + r"\s*\(?")


def test_only_the_policy_class_and_erase_all_write_the_registry():
    offenders = {}
    for path in sorted((REPO / "iris_src" / "src").rglob("*.cls")):
        rel = path.relative_to(REPO / "iris_src" / "src").as_posix()
        text = _code(_src(path))
        if GLOBAL not in text:
            continue
        hits = [m.group(0).strip() for m in _WRITE.finditer(text) if "$Data" not in m.group(0) and "$Get" not in m.group(0)]
        if hits and path.name not in ("TemporalPolicy.cls", "Eraser.cls"):
            offenders[rel] = hits
    assert not offenders, f"{GLOBAL} written outside TemporalPolicy / Eraser: {offenders}"


def test_erase_all_kills_the_registry_and_erase_graph_keeps_it():
    text = _src(KG / "Eraser.cls")
    erase_graph = text[text.index("ClassMethod EraseGraph") : text.index("ClassMethod EraseNodes")]
    erase_all = text[text.index("ClassMethod EraseAll") :]
    assert not re.search(r"(?i)kill\s+" + re.escape(GLOBAL), _code(erase_graph)), "EraseGraph must keep the registry"
    assert GLOBAL in erase_graph, "EraseGraph should say in its own source why it keeps the registry"
    assert re.search(r"Kill " + re.escape(GLOBAL) + r"\s*$", _code(erase_all), re.M)


def test_the_registry_is_in_the_inventory_and_the_snapshot_plan():
    assert f'Entry("{GLOBAL}", "global", "subscript 1", 1)' in _src(KG / "GraphStores.cls")
    from iris_vector_graph._engine import snapshot as snapshot_mod

    assert snapshot_mod.STORE_PLAN.get(GLOBAL) == "global"
    assert '("IVG.TemporalStructural", [[]])' in _src(REPO / "iris_vector_graph" / "_engine" / "snapshot.py")


@pytest.mark.parametrize(
    "cls,method",
    [
        ("TemporalIndex.cls", "WriteStores"),
        ("TemporalIndex.cls", "BackfillWalk"),
        ("TemporalIndex.cls", "RetireExpired"),
    ],
)
def test_every_decider_of_a_structural_row_asks_the_policy(cls, method):
    body = _code(_method(_src(KG / cls), method))
    assert "TemporalPolicy" in body, f"{cls}.{method} does not consult Graph.KG.TemporalPolicy"


def test_nobody_but_the_policy_reads_the_registry_directly():
    for path in sorted((REPO / "iris_src" / "src").rglob("*.cls")):
        if path.name in ("TemporalPolicy.cls", "Eraser.cls", "GraphStores.cls"):
            continue
        assert GLOBAL not in _code(_src(path)), f"{path.name} reads {GLOBAL} itself; ask TemporalPolicy.IsStructural"


def test_the_policy_class_writes_no_adjacency_store():
    """ADR-0011: Graph.KG.Adjacency alone writes out/in/gen/degc; the policy deletes rows and lets the index do it."""
    text = _code(_src(POLICY))
    assert not re.search(r'\^KG\(\s*"(?:out|in|gen|degc|deg|degp|degw)"', text)


def test_no_catch_in_the_policy_swallows_the_error():
    text = _code(_src(POLICY))
    for m in re.finditer(r"Catch\s+(\w+)\s*\{(.*?)\n\t\}", text, re.S):
        body = m.group(2)
        assert re.search(r"Throw\s+|AsStatus\(\)|\$\$\$ERROR|Quit\s+\"error:", body), (
            f"a Catch in TemporalPolicy neither rethrows nor reports: {body.strip()[:120]!r}"
        )


def test_the_python_api_does_not_swallow_errors_on_data_changing_paths():
    text = _src(REPO / "iris_vector_graph" / "_engine" / "temporal.py")
    for name in (
        "set_temporal_structural",
        "drop_temporal_structural_rows",
        "backfill_temporal_structural_rows",
    ):
        assert f"def {name}(" in text, f"{name} is missing"
        start = text.index(f"def {name}(")
        body = text[start : text.find("\n    def ", start + 1)]
        assert not re.search(r"except\s+Exception[^\n]*:\s*\n\s*return\s+(False|None)", body), (
            f"{name} swallows its error"
        )
    for name in ("get_temporal_structural", "list_temporal_structural_opt_outs"):
        assert f"def {name}(" in text


def test_the_old_python_backfill_honours_the_registry():
    text = _src(REPO / "iris_vector_graph" / "migrations" / "temporal_structural_rows.py")
    assert "list_temporal_structural_opt_outs" in text
