"""TCK side-effect measurement (spec 229): graph-state snapshots and their deltas.

The openCypher TCK defines a query's side effects as the difference between two
graph states, each made of four sets (tck-api ``SideEffectOps``):

* node ids                      -> ``+nodes`` / ``-nodes``
* relationship ids              -> ``+relationships`` / ``-relationships``
* distinct label names in use   -> ``+labels`` / ``-labels``
* ``(entity, key, value)``      -> ``+properties`` / ``-properties``

``+labels`` therefore counts label *names newly present in the graph*, not label
assignments: ``CREATE (:L), (:L)`` is ``+labels 1``, and a label already carried by
another node is not new. An overwritten property is one triple out and one in,
which is the corpus's ``+properties 1, -properties 1`` for ``SET n.name = ...``.

IVG mapping (plan.md "The column mappings"):

======================  ==================================================
node ids                ``Graph_KG.nodes (graph_id, node_id)``
relationship ids        ``Graph_KG.rdf_edges.edge_id``
label names             ``DISTINCT Graph_KG.rdf_labels.label``, minus the
                        harness's ``TCK_*`` isolation labels
node property triples   one ``Graph_KG.rdf_props`` row per property
edge property triples   one key of the ``rdf_edges.qualifiers`` JSON object
======================  ==================================================

``rdf_reifications`` has no TCK column; any change to it is reported as an
unexpected write. With ``IVG_TCK_KG_CHECK=1`` the ``^KG("out")`` adjacency entry
count is compared to the ``rdf_edges`` delta as well (off by default: plan.md risk 4).

Snapshots are sets, not counts, and the harness diffs two of them taken on the
scenario's own connection around one query, so rows other scenarios left behind
cancel out (FR-009) and a create-plus-delete in one query shows on both sides.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

#: Every TCK side-effect column, in the order the corpus writes them.
SIDE_EFFECT_COLUMNS: tuple[str, ...] = (
    "+nodes",
    "-nodes",
    "+relationships",
    "-relationships",
    "+labels",
    "-labels",
    "+properties",
    "-properties",
)

#: Where each column is measured, for failure messages.
COLUMN_SOURCE: dict[str, str] = {
    "+nodes": "Graph_KG.nodes",
    "-nodes": "Graph_KG.nodes",
    "+relationships": "Graph_KG.rdf_edges",
    "-relationships": "Graph_KG.rdf_edges",
    "+labels": "Graph_KG.rdf_labels (distinct names)",
    "-labels": "Graph_KG.rdf_labels (distinct names)",
    "+properties": "Graph_KG.rdf_props + rdf_edges.qualifiers",
    "-properties": "Graph_KG.rdf_props + rdf_edges.qualifiers",
}

ISOLATION_LABEL_PREFIX = "TCK_"


class SideEffectsAssumptionError(AssertionError):
    """The rows no longer map one-to-one onto TCK property triples."""


def _value_key(v: Any) -> str:
    """A property value as a comparable token; type-tagged so 1 and '1' differ."""
    return f"{type(v).__name__}:{v!r}"


@dataclass(frozen=True)
class SideEffects:
    """One graph-state snapshot. ``delta_to`` of two snapshots is the side effects."""

    nodes: frozenset = field(default_factory=frozenset)
    relationships: frozenset = field(default_factory=frozenset)
    labels: frozenset = field(default_factory=frozenset)
    properties: frozenset = field(default_factory=frozenset)
    reifications: int = 0
    kg_out_entries: Optional[int] = None
    edge_keys: frozenset = field(default_factory=frozenset)

    # -- construction ------------------------------------------------------

    @classmethod
    def from_rows(
        cls,
        *,
        nodes: Iterable[tuple],
        labels: Iterable[tuple],
        props: Iterable[tuple],
        edges: Iterable[tuple],
        reifications: int = 0,
        kg_out_entries: Optional[int] = None,
    ) -> "SideEffects":
        """Build a snapshot from table rows.

        nodes:  ``(graph_id, node_id)``
        labels: ``(graph_id, s, label)``
        props:  ``(graph_id, s, key, val)``
        edges:  ``(edge_id, qualifiers)`` or ``(edge_id, qualifiers, graph_id, s, p, o_id)``
        """
        node_set = frozenset(("n", g or "", n) for g, n in nodes)
        label_set = frozenset(
            lbl for _g, _s, lbl in labels if not str(lbl).startswith(ISOLATION_LABEL_PREFIX)
        )
        prop_set: set = set()
        seen_node_keys: set = set()
        for g, s, k, v in props:
            ident = ("n", g or "", s)
            if (ident, k) in seen_node_keys:
                raise SideEffectsAssumptionError(
                    f"rdf_props holds two rows for {s!r}.{k}: the one-row-per-property "
                    f"mapping behind +properties/-properties no longer holds"
                )
            seen_node_keys.add((ident, k))
            if v is None:
                continue  # a NULL-valued row is no property (IVG does not write these)
            prop_set.add((ident, k, _value_key(v)))
        rel_set = set()
        edge_keys = set()
        for row in edges:
            edge_id, quals = row[0], row[1]
            ident = ("r", edge_id)
            rel_set.add(ident)
            if len(row) >= 6:
                edge_keys.add(tuple(row[2:6]))
            for k, v in _qualifier_items(edge_id, quals):
                if v is None:
                    continue
                prop_set.add((ident, k, _value_key(v)))
        return cls(
            nodes=node_set,
            relationships=frozenset(rel_set),
            labels=label_set,
            properties=frozenset(prop_set),
            reifications=int(reifications or 0),
            kg_out_entries=kg_out_entries,
            edge_keys=frozenset(edge_keys),
        )

    @classmethod
    def capture(cls, conn, schema: str = "Graph_KG") -> "SideEffects":
        """Snapshot the IVG tables on ``conn`` (the scenario's own connection)."""
        cur = conn.cursor()
        try:
            def rows(sql):
                cur.execute(sql)
                return [tuple(r) for r in cur.fetchall()]

            nodes = rows(f"SELECT graph_id, node_id FROM {schema}.nodes")
            labels = rows(f"SELECT graph_id, s, label FROM {schema}.rdf_labels")
            props = rows(f"SELECT graph_id, s, %EXACT(key), val FROM {schema}.rdf_props")
            edges = rows(f"SELECT edge_id, qualifiers, graph_id, s, p, o_id FROM {schema}.rdf_edges")
            reif = rows(f"SELECT COUNT(*) FROM {schema}.rdf_reifications")
        finally:
            try:
                cur.close()
            except Exception:
                pass
        kg = _kg_out_entries(conn) if os.environ.get("IVG_TCK_KG_CHECK") == "1" else None
        return cls.from_rows(
            nodes=nodes,
            labels=labels,
            props=props,
            edges=edges,
            reifications=reif[0][0] if reif else 0,
            kg_out_entries=kg,
        )

    # -- comparison --------------------------------------------------------

    def delta_to(self, later: "SideEffects") -> dict[str, int]:
        """Per-column side effects of going from ``self`` to ``later``."""
        return {
            "+nodes": len(later.nodes - self.nodes),
            "-nodes": len(self.nodes - later.nodes),
            "+relationships": len(later.relationships - self.relationships),
            "-relationships": len(self.relationships - later.relationships),
            "+labels": len(later.labels - self.labels),
            "-labels": len(self.labels - later.labels),
            "+properties": len(later.properties - self.properties),
            "-properties": len(self.properties - later.properties),
        }

    def unexpected_to(self, later: "SideEffects") -> dict[str, int]:
        """Writes no TCK column accounts for: reifications, and (flagged) ``^KG`` drift."""
        out: dict[str, int] = {}
        if later.reifications != self.reifications:
            out["rdf_reifications"] = later.reifications - self.reifications
        if self.kg_out_entries is not None and later.kg_out_entries is not None:
            kg_delta = later.kg_out_entries - self.kg_out_entries
            spo_delta = len(later.edge_keys) - len(self.edge_keys)
            if kg_delta != spo_delta:
                out['^KG("out") vs rdf_edges'] = kg_delta - spo_delta
        return out

    @staticmethod
    def unmapped(columns: Iterable[str]) -> list[str]:
        """The columns this mapping does not measure, in the order given."""
        return [c for c in columns if c not in SIDE_EFFECT_COLUMNS]


def _qualifier_items(edge_id, quals) -> list[tuple[str, Any]]:
    if quals is None or quals == "":
        return []
    if isinstance(quals, dict):
        obj = quals
    else:
        try:
            obj = json.loads(quals)
        except (TypeError, ValueError):
            raise SideEffectsAssumptionError(
                f"rdf_edges.qualifiers of edge {edge_id} is not JSON: {quals!r}"
            ) from None
    if not isinstance(obj, dict):
        raise SideEffectsAssumptionError(
            f"rdf_edges.qualifiers of edge {edge_id} is not a JSON object: {quals!r}; "
            f"one key per relationship property no longer holds"
        )
    return list(obj.items())


def _kg_out_entries(conn) -> Optional[int]:
    """Count ``^KG("out", g, s, p, o)`` entries (only under IVG_TCK_KG_CHECK=1)."""
    try:
        import iris

        irisobj = iris.createIRIS(conn)
        n = 0
        for g, _ in irisobj.iterator("^KG", "out").items():
            for s, _ in irisobj.iterator("^KG", "out", g).items():
                for p, _ in irisobj.iterator("^KG", "out", g, s).items():
                    for _o, _ in irisobj.iterator("^KG", "out", g, s, p).items():
                        n += 1
        return n
    except Exception:
        return None


def parse_side_effects_table(headings: list, rows: list) -> dict[str, int]:
    """A ``| +nodes | 1 |`` table (behave reads the first row as headings)."""
    pairs = [list(headings)] + [list(r) for r in rows]
    out: dict[str, int] = {}
    for pair in pairs:
        if len(pair) < 2:
            raise ValueError(f"side-effect row needs a column and a count: {pair!r}")
        col, val = str(pair[0]).strip(), str(pair[1]).strip()
        try:
            out[col] = int(val)
        except ValueError:
            raise ValueError(f"side-effect count for {col} is not an integer: {val!r}") from None
    return out


def compare_side_effects(
    delta: Optional[dict[str, int]],
    expected: dict[str, int],
    unexpected: Optional[dict[str, int]] = None,
    capture_error: Optional[str] = None,
) -> Optional[str]:
    """None when ``delta`` matches ``expected`` (unlisted columns expected 0), else why not."""
    unmapped = SideEffects.unmapped(expected)
    if unmapped:
        return f"unmapped side-effect column(s): {', '.join(unmapped)}"
    if delta is None:
        why = f" ({capture_error})" if capture_error else ""
        return f"no side-effect snapshot was taken for the last query{why}"
    problems = []
    for col in SIDE_EFFECT_COLUMNS:
        exp = expected.get(col, 0)
        obs = delta.get(col, 0)
        if exp != obs:
            problems.append(f"{col}: expected {exp}, observed {obs} [{COLUMN_SOURCE[col]}]")
    for what, n in (unexpected or {}).items():
        problems.append(f"unexpected write to {what}: {n:+d}")
    if problems:
        return "side effects differ: " + "; ".join(problems)
    return None
