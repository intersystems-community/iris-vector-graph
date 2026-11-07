"""What setup creates, read from the code and the committed inventory, for tests that count it.

A literal count goes stale with the next routine: test_460_r050_dry_run_plan_e2e asserted 51 routines when setup had 55.
A test that needs a count derives it here.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INVENTORY = json.loads((REPO / "docs" / "inventory.json").read_text())
SOURCES = REPO / "iris_src" / "src"


def setup_routine_names(schema: str = "Graph_KG") -> list:
    """The routines setup's DDL creates in `schema`, as the catalog spells them, in setup's order."""
    from iris_vector_graph.routine_record import routine_name
    from iris_vector_graph.schema import GraphSchema

    return [n for n in map(routine_name, GraphSchema.get_procedures_sql_list(table_schema=schema)) if n]


def inventory_tables() -> set:
    """The tables setup creates, from docs/inventory.json."""
    return {n for e in INVENTORY["entries"] if e["kind"] == "table" and e["present"] == "setup" for n in e["names"]}


def shipped_classes() -> set:
    """Every class under iris_src/src."""
    return {".".join(p.relative_to(SOURCES).with_suffix("").parts) for p in SOURCES.rglob("*.cls")}
