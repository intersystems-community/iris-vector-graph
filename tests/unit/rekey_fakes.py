"""A catalog-and-rows fake for the `nodes` / `rdf_labels` / `rdf_props` re-key.

Not a test file — shared by `test_rekey_resume_dedupe_legacy_keys.py`. It models what
the three re-key bugs found on a real 3.2.0-era install depend on, and refuses what
IRIS refuses, with IRIS's own SQLCODEs (each measured on `ivg-iris-enterprise`):

- constraints as ``(table, name) -> type, ordered columns, referenced key``, so a
  test can ask "is there still a key over ``node_id`` alone?" of the catalog rather
  than of the statement text;
- ``DROP CONSTRAINT`` of a missing key (-315) or of a key a foreign key still
  references (-317); a second primary key (-307); a name already taken (-311 for a
  foreign key, the -400 index-name conflict for a unique); a unique or primary key
  over rows that are not unique (-125);
- ``ADD COLUMN`` of a column that exists (-306) and ``NOT NULL`` over a NULL (-305);
- rows with a ``%ID``, so a dedupe that keeps "the lowest %ID" is observable;
- ``fail_once_on``: raise on the first statement containing the needle, once — a
  killed process, so the re-run is a fresh one.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

SCHEMA = "Graph_KG"


class Killed(RuntimeError):
    pass


def _unq(name: str) -> str:
    return name.strip().strip('"')


def _cols(text: str) -> List[str]:
    return [_unq(c) for c in text.split(",")]


class RekeyDB:
    def __init__(
        self,
        *,
        nodes: Dict[str, List[str]],
        labels: List[Tuple[str, str]] = (),
        props: List[Tuple[str, str, Optional[str]]] = (),
        node_keys: Optional[Dict[str, Tuple[str, List[str]]]] = None,
        idkeys: Tuple[str, ...] = (),
        extra_fks: Optional[Dict[Tuple[str, str], Tuple[List[str], str]]] = None,
        props_val_type: str = "varchar",
        fail_once_on: Optional[str] = None,
    ):
        self.columns: Dict[str, Dict[str, str]] = {
            "nodes": {"node_id": "NO", "graph_id": "NO", "created_at": "YES"},
            "rdf_labels": {"s": "NO", "label": "NO"},
            "rdf_props": {"s": "NO", "key": "NO", "val": "YES"},
            "rdf_edges": {"edge_id": "NO", "s": "NO", "p": "NO", "o_id": "NO", "graph_id": "YES"},
        }
        self.types = {("rdf_props", "val"): props_val_type}
        legacy = node_keys if node_keys is not None else {"NODES_PKEY1": ("PRIMARY KEY", ["node_id"])}
        #: (table, name) -> {"type", "cols", "ref": (table, name) | None}
        self.constraints: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for name, (kind, cols) in legacy.items():
            self.constraints[("nodes", name)] = {"type": kind, "cols": list(cols), "ref": None}
        target = next(
            (n for n, (_k, c) in legacy.items() if c == ["node_id"]), None
        )
        for table, name, cols in (
            ("rdf_labels", "fk_labels_node", ["s"]),
            ("rdf_edges", "fk_edges_source", ["s"]),
            ("rdf_edges", "fk_edges_dest", ["o_id"]),
        ):
            if target is not None:
                self.constraints[(table, name)] = {
                    "type": "FOREIGN KEY",
                    "cols": cols,
                    "ref": ("nodes", target),
                }
        for (table, name), (cols, ref) in dict(extra_fks or {}).items():
            self.constraints[(table, name)] = {
                "type": "FOREIGN KEY",
                "cols": list(cols),
                "ref": ("nodes", ref),
            }
        self.idkeys = set(idkeys)

        self.node_rows: List[Dict[str, Any]] = [
            {"node_id": n, "graph_id": g} for n, graphs in nodes.items() for g in graphs
        ]
        self._next_id = 0
        self.rows: Dict[str, List[Dict[str, Any]]] = {"rdf_labels": [], "rdf_props": []}
        for s, label in labels:
            self.rows["rdf_labels"].append({"%ID": self._id(), "s": s, "label": label})
        for s, key, val in props:
            self.rows["rdf_props"].append({"%ID": self._id(), "s": s, "key": key, "val": val})

        self.fail_once_on = fail_once_on
        self.statements: List[str] = []

    # ----------------------------------------------------------------- helpers

    def _id(self) -> int:
        self._next_id += 1
        return self._next_id

    def cursor(self) -> "RekeyCursor":
        return RekeyCursor(self)

    def commit(self) -> None:
        pass

    def keys_of(self, table: str) -> Dict[str, Tuple[str, List[str]]]:
        return {
            name: (c["type"], list(c["cols"]))
            for (t, name), c in self.constraints.items()
            if t == table
        }

    def node_id_only_keys(self) -> List[str]:
        return sorted(
            name
            for name, (kind, cols) in self.keys_of("nodes").items()
            if kind in ("PRIMARY KEY", "UNIQUE") and cols == ["node_id"]
        )

    def can_insert_node(self, node_id: str, graph_id: str) -> bool:
        """What IRIS answers to `INSERT INTO nodes`: -119 if any unique key collides."""
        candidate = {"node_id": node_id, "graph_id": graph_id}
        for kind, cols in self.keys_of("nodes").values():
            if kind not in ("PRIMARY KEY", "UNIQUE"):
                continue
            key = tuple(candidate[c] for c in cols)
            if any(tuple(r[c] for c in cols) == key for r in self.node_rows):
                return False
        return True

    def insert_node(self, node_id: str, graph_id: str) -> None:
        if not self.can_insert_node(node_id, graph_id):
            raise RuntimeError("[SQLCODE: <-119>:<UNIQUE or PRIMARY KEY constraint failed>]")
        self.node_rows.append({"node_id": node_id, "graph_id": graph_id})

    def graphs_of(self, node_id: str) -> set:
        return {r["graph_id"] for r in self.node_rows if r["node_id"] == node_id}

    def writes(self) -> List[str]:
        return [
            s for s in self.statements
            if s.upper().startswith(("ALTER", "UPDATE", "DELETE", "INSERT", "DROP", "CREATE"))
        ]

    def is_fully_rekeyed(self) -> List[str]:
        """What is still undone, as a list of complaints; empty means 4.0.0-keyed."""
        wrong = []
        for table, pk, tail in (("rdf_labels", "pk_labels", "label"), ("rdf_props", "pk_props", "key")):
            if self.columns[table].get("graph_id") != "NO":
                wrong.append(f"{table}.graph_id is not NOT NULL")
            if self.keys_of(table).get(pk) != ("PRIMARY KEY", ["graph_id", "s", tail]):
                wrong.append(f"{table}.{pk} is {self.keys_of(table).get(pk)}")
        for table, fk, first in (
            ("rdf_labels", "fk_labels_node", "s"),
            ("rdf_edges", "fk_edges_source", "s"),
            ("rdf_edges", "fk_edges_dest", "o_id"),
        ):
            c = self.constraints.get((table, fk))
            if not c or c["cols"] != ["graph_id", first] or c["ref"] != ("nodes", "uq_nodes_graph_node"):
                wrong.append(f"{table}.{fk} is {c}")
        if self.keys_of("nodes").get("uq_nodes_graph_node") != ("UNIQUE", ["graph_id", "node_id"]):
            wrong.append("nodes has no uq_nodes_graph_node")
        if self.keys_of("nodes").get("pk_nodes_graph") != ("PRIMARY KEY", ["node_id", "graph_id"]):
            wrong.append(f"nodes primary key is {self.keys_of('nodes')}")
        if self.node_id_only_keys():
            wrong.append(f"nodes still keys node_id alone: {self.node_id_only_keys()}")
        return wrong


class RekeyCursor:
    def __init__(self, db: RekeyDB):
        self.db = db
        self._rows: List[Tuple[Any, ...]] = []
        self.rowcount = -1

    def close(self) -> None:
        pass

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    # ------------------------------------------------------------------ dispatch

    def execute(self, sql, params=None):
        flat = " ".join(str(sql).split())
        args = list(params or [])
        db = self.db
        db.statements.append(flat)
        self._rows, self.rowcount = [], -1
        if db.fail_once_on and db.fail_once_on.lower() in flat.lower():
            db.fail_once_on = None
            raise Killed("[SQLCODE: <-99>] the migration process was killed")
        upper = flat.upper()
        if upper.startswith("SELECT"):
            self._rows = self._select(flat, upper, args)
        elif upper.startswith("ALTER TABLE"):
            self._alter(flat, upper)
        elif upper.startswith("UPDATE"):
            self._backfill(flat)
        elif upper.startswith("DELETE"):
            self._dedupe(flat)
        else:
            raise AssertionError(f"the re-key fake does not model: {flat}")
        return self

    # ------------------------------------------------------------------- reading

    def _select(self, flat, upper, args):
        db = self.db
        if "INFORMATION_SCHEMA.COLUMNS" in upper:
            table, column = str(args[1]), str(args[2])
            declared = db.columns.get(table, {})
            if upper.startswith("SELECT COUNT(*)"):
                return [(1 if column in declared else 0,)]
            if "IS_NULLABLE" in upper:
                return [(declared[column],)] if column in declared else []
            if "DATA_TYPE" in upper:
                if column not in declared:
                    return []
                return [(db.types.get((table, column), "varchar"),)]
        if "INFORMATION_SCHEMA.KEY_COLUMN_USAGE" in upper:
            out = []
            for (table, name), c in db.constraints.items():
                for i, col in enumerate(c["cols"], start=1):
                    ref = c["ref"] or (None, None)
                    out.append((table, name, c["type"], col, i, ref[0], ref[1]))
            return out
        if "INFORMATION_SCHEMA.TABLES" in upper and "CLASSNAME" in upper:
            return [(f"Graph.KG.{args[1]}",)]
        if "%DICTIONARY.COMPILEDINDEX" in upper:
            return [(n,) for n in sorted(db.idkeys)]
        if "COUNT(DISTINCT N.GRAPH_ID)" in upper:
            table = re.search(r"FROM Graph_KG\.(\w+) c", flat).group(1)
            rows = db.rows[table]
            if "c.graph_id IS NULL" in flat:
                rows = [r for r in rows if r.get("graph_id") is None]
            return [(r["s"],) for r in rows if len(db.graphs_of(r["s"])) != 1]
        m = re.fullmatch(r"SELECT COUNT\(\*\) FROM Graph_KG\.(\w+)", flat)
        if m:
            return [(len(db.rows[m.group(1)]),)]
        if "HAVING COUNT(*) > 1" in upper:
            table = re.search(r"FROM Graph_KG\.(\w+) g", flat).group(1)
            groups: Dict[tuple, int] = {}
            for r in db.rows[table]:
                key = self._natural(table, r, True)
                groups[key] = groups.get(key, 0) + 1
            return [(k[1],) for k, n in groups.items() if n > 1]
        if "%ID" in upper and "EXISTS" in upper:
            table = re.search(r"FROM Graph_KG\.(\w+) d", flat).group(1)
            return self._dup_select(table, flat)
        raise AssertionError(f"the re-key fake does not model: {flat}")

    def _natural(self, table: str, row: Dict[str, Any], with_graph: bool) -> tuple:
        tail = "label" if table == "rdf_labels" else "key"
        key = (row["s"], row[tail])
        return ((row.get("graph_id"),) + key) if with_graph else key

    def _dup_select(self, table: str, flat: str):
        rows = self.db.rows[table]
        with_graph = "k.graph_id" in flat
        compares_val = "k.val = d.val" in flat or "k.val <> d.val" in flat
        conflict = "<>" in flat and "k.%ID <> d.%ID" in flat
        out = []
        for d in rows:
            twins = [
                k for k in rows
                if k is not d
                and self._natural(table, k, with_graph) == self._natural(table, d, with_graph)
            ]
            if conflict:
                if compares_val:
                    hit = any(k["val"] != d["val"] for k in twins)
                else:
                    hit = bool(twins)
                if hit:
                    out.append((d["s"], d["key"]))
            else:
                lower = [k for k in twins if k["%ID"] < d["%ID"]]
                if table == "rdf_props":
                    lower = [k for k in lower if k["val"] == d["val"]]
                if lower:
                    out.append((d["%ID"],))
        return out

    # ------------------------------------------------------------------- writing

    def _alter(self, flat, upper):
        db = self.db
        table = re.match(r"ALTER TABLE Graph_KG\.(\w+)", flat).group(1)
        m = re.search(r"ADD COLUMN (\w+)", flat)
        if m:
            if m.group(1) in db.columns[table]:
                raise RuntimeError(
                    "[SQLCODE: <-306>:<Column with this name already exists>] "
                    f"[%msg: <Column of name '{m.group(1)}' already exists>]"
                )
            db.columns[table][m.group(1)] = "YES"
            for r in db.rows.get(table, []):
                r[m.group(1)] = None
            return
        m = re.search(r"ALTER COLUMN (\w+) NOT NULL", flat)
        if m:
            if any(r.get(m.group(1)) is None for r in db.rows.get(table, [])):
                raise RuntimeError(
                    "[SQLCODE: <-305>:<Attempt to make field required when the table has "
                    "one or more rows where the column value is NULL>]"
                )
            db.columns[table][m.group(1)] = "NO"
            return
        if re.search(r"ALTER COLUMN \w+ SET DEFAULT", flat):
            return
        m = re.search(r"DROP CONSTRAINT (\w+)", flat)
        if m:
            name = m.group(1)
            if (table, name) not in db.constraints:
                raise RuntimeError(
                    "[SQLCODE: <-315>:<Constraint or key not found>] "
                    f"[%msg: <Constraint '{name}' not found>]"
                )
            if name in db.idkeys:
                raise RuntimeError("[SQLCODE: <-400>] cannot drop an IDKEY primary key")
            holders = [
                n for (t, n), c in db.constraints.items() if c["ref"] == (table, name)
            ]
            if holders:
                raise RuntimeError(
                    "[SQLCODE: <-317>:<Cannot DROP Constraint - One or more foreign key "
                    f"constraints reference this Unique constraint>] {holders}"
                )
            del db.constraints[(table, name)]
            return
        m = re.search(
            r"ADD CONSTRAINT (\w+) (PRIMARY KEY|UNIQUE|FOREIGN KEY) \(([^)]*)\)"
            r"(?: REFERENCES Graph_KG\.(\w+) \(([^)]*)\))?",
            flat,
        )
        if m:
            name, kind, cols = m.group(1), m.group(2), _cols(m.group(3))
            if (table, name) in db.constraints:
                if kind == "FOREIGN KEY":
                    raise RuntimeError(
                        "[SQLCODE: <-311>:<Foreign key with same name already defined "
                        f"for this table>] [%msg: <Foreign Key named '{name}' already exists>]"
                    )
                raise RuntimeError(
                    "[SQLCODE: <-400>:<Fatal error occurred>] [%msg: <ERROR #5067: Index "
                    f"name conflict: {name.replace('_', '').lower()}>]"
                )
            if kind == "PRIMARY KEY" and any(
                c["type"] == "PRIMARY KEY" for (t, _n), c in db.constraints.items() if t == table
            ):
                raise RuntimeError(
                    "[SQLCODE: <-307>:<Primary key already defined for this table>]"
                )
            ref = None
            if kind == "FOREIGN KEY":
                ref_cols = _cols(m.group(5))
                target = next(
                    (
                        n for n, (k, c) in db.keys_of(m.group(4)).items()
                        if k in ("PRIMARY KEY", "UNIQUE") and c == ref_cols
                    ),
                    None,
                )
                if target is None:
                    raise RuntimeError("[SQLCODE: <-314>] no unique key over the referenced columns")
                ref = (m.group(4), target)
            else:
                rows = db.node_rows if table == "nodes" else db.rows.get(table, [])
                keys = [tuple(r.get(c) for c in cols) for r in rows]
                if len(keys) != len(set(keys)):
                    raise RuntimeError(
                        "[SQLCODE: <-125>:<UNIQUE or PRIMARY KEY Constraint failed "
                        "uniqueness check upon creation of the constraint>]"
                    )
            db.constraints[(table, name)] = {"type": kind, "cols": cols, "ref": ref}
            return
        raise AssertionError(f"the re-key fake does not model: {flat}")

    def _backfill(self, flat):
        table = re.match(r"UPDATE Graph_KG\.(\w+)", flat).group(1)
        n = 0
        for r in self.db.rows[table]:
            if "c.graph_id IS NULL" in flat and r.get("graph_id") is not None:
                continue
            graphs = self.db.graphs_of(r["s"])
            if len(graphs) == 1:
                r["graph_id"] = next(iter(graphs))
                n += 1
        self.rowcount = n

    def _dedupe(self, flat):
        """`DELETE ... WHERE %ID NOT IN (SELECT MIN(g.%ID) ... GROUP BY ...)`, and for
        props `AND %ID IN (<rows with a lower-%ID twin of equal val>)`."""
        m = re.match(r"DELETE FROM Graph_KG\.(\w+) WHERE %ID NOT IN \(SELECT MIN\(g\.%ID\)", flat)
        if not m:
            raise AssertionError(f"the re-key fake does not model: {flat}")
        table = m.group(1)
        rows = self.db.rows[table]
        lowest: Dict[tuple, int] = {}
        for r in rows:
            key = self._natural(table, r, True)
            lowest[key] = min(lowest.get(key, r["%ID"]), r["%ID"])
        doomed = {r["%ID"] for r in rows if lowest[self._natural(table, r, True)] != r["%ID"]}
        if "k.val = d.val" in flat:
            doomed &= {
                d["%ID"] for d in rows
                if any(
                    k["%ID"] < d["%ID"] and k["val"] == d["val"]
                    and self._natural(table, k, True) == self._natural(table, d, True)
                    for k in rows
                )
            }
        elif table == "rdf_props":
            raise AssertionError("a props dedupe that does not compare val would discard values")
        before = len(self.db.rows[table])
        self.db.rows[table] = [r for r in self.db.rows[table] if r["%ID"] not in doomed]
        self.rowcount = before - len(self.db.rows[table])
