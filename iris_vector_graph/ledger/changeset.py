"""Changeset builder, wire format, canonical JSON and fingerprint (spec 213, R14).

The wire format is defined by ``contracts/changeset.schema.json``. All stored
values are strings (exact stored representation, FR-014a). The idempotency
fingerprint covers ``{actor, actor_type, ops}`` only (clarification Q2).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from .errors import ChangesetOperationError, EmptyChangesetError

RESERVED_PROPERTIES = frozenset({"id", "__graph"})

ACTOR_TYPES = ("human", "agent", "system", "ingest")


@dataclass(frozen=True)
class OpRef:
    """Reference to a relationship created or upserted earlier in the same changeset."""

    index: int


TupleRef = Union[Tuple[str, str, str], Tuple[str, str, str, Optional[str]]]
RelRefLike = Union[str, TupleRef, OpRef]


def _str_value(value: Any, op_index: int, what: str) -> str:
    if value is None:
        raise ChangesetOperationError(
            op_index, f"{what}: value may not be null; use a remove operation"
        )
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    raise ChangesetOperationError(
        op_index, f"{what}: unsupported value type {type(value).__name__}"
    )


def _check_reserved(key: str, op_index: int) -> None:
    if key in RESERVED_PROPERTIES:
        raise ChangesetOperationError(
            op_index, f"reserved_property: '{key}' may not be set or removed"
        )


def _tuple_wire(t: Sequence[Any]) -> Dict[str, Any]:
    if len(t) == 3:
        s, p, o = t
        g = None
    elif len(t) == 4:
        s, p, o, g = t
    else:
        raise ValueError("relationship tuple must be (source, type, target[, graph])")
    return {"s": str(s), "p": str(p), "o": str(o), "graph": None if g in (None, "") else str(g)}


def _rel_ref(ref: Optional[RelRefLike], stmt_id: Optional[str]) -> Dict[str, Any]:
    if (ref is None) == (stmt_id is None):
        raise ValueError("exactly one of ref (tuple or OpRef) or stmt_id must be given")
    if stmt_id is not None:
        return {"stmt_id": str(stmt_id)}
    if isinstance(ref, OpRef):
        return {"op_ref": int(ref.index)}
    if isinstance(ref, str):
        return {"stmt_id": ref}
    if isinstance(ref, (tuple, list)):
        return {"tuple": _tuple_wire(ref)}
    raise TypeError(f"unsupported relationship reference: {ref!r}")


@dataclass
class Changeset:
    """Ordered collection of structural mutations plus commit metadata."""

    actor: str
    actor_type: str
    message: Optional[str] = None
    source: Optional[Dict[str, Any]] = None
    correlation_id: Optional[str] = None
    expected_head: Optional[str] = None
    idempotency_key: Optional[str] = None
    ops: List[Dict[str, Any]] = field(default_factory=list)

    # -- internal -----------------------------------------------------------

    def _add(self, op: Dict[str, Any]) -> int:
        self.ops.append(op)
        return len(self.ops) - 1

    @property
    def _next_index(self) -> int:
        return len(self.ops)

    # -- node operations ------------------------------------------------------

    def create_node(
        self,
        node_id: str,
        labels: Optional[Sequence[str]] = None,
        properties: Optional[Dict[str, Any]] = None,
    ) -> "Changeset":
        idx = self._next_index
        op: Dict[str, Any] = {"op": "create_node", "id": str(node_id)}
        if labels:
            op["labels"] = [str(lbl) for lbl in labels]
        if properties:
            op["props"] = self._props(properties, idx)
        self._add(op)
        return self

    def upsert_node(
        self,
        node_id: str,
        labels: Optional[Sequence[str]] = None,
        properties: Optional[Dict[str, Any]] = None,
    ) -> "Changeset":
        idx = self._next_index
        op: Dict[str, Any] = {"op": "upsert_node", "id": str(node_id)}
        if labels:
            op["labels"] = [str(lbl) for lbl in labels]
        if properties:
            op["props"] = self._props(properties, idx)
        self._add(op)
        return self

    def _props(self, properties: Dict[str, Any], idx: int) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for k, v in properties.items():
            _check_reserved(str(k), idx)
            out[str(k)] = _str_value(v, idx, f"property {k}")
        return out

    def delete_node(self, node_id: str, mode: str = "strict") -> "Changeset":
        if mode not in ("strict", "detach"):
            raise ValueError("mode must be 'strict' or 'detach'")
        self._add({"op": "delete_node", "id": str(node_id), "mode": mode})
        return self

    def add_label(self, node_id: str, label: str) -> "Changeset":
        self._add({"op": "add_label", "id": str(node_id), "label": str(label)})
        return self

    def remove_label(self, node_id: str, label: str) -> "Changeset":
        self._add({"op": "remove_label", "id": str(node_id), "label": str(label)})
        return self

    def set_property(self, node_id: str, key: str, value: Any) -> "Changeset":
        idx = self._next_index
        _check_reserved(str(key), idx)
        self._add(
            {
                "op": "set_prop",
                "id": str(node_id),
                "key": str(key),
                "value": _str_value(value, idx, f"property {key}"),
            }
        )
        return self

    def remove_property(self, node_id: str, key: str) -> "Changeset":
        idx = self._next_index
        _check_reserved(str(key), idx)
        self._add({"op": "remove_prop", "id": str(node_id), "key": str(key)})
        return self

    # -- relationship operations ---------------------------------------------

    def _quals(self, qualifiers: Optional[Dict[str, Any]], idx: int) -> Dict[str, str]:
        return {str(k): _str_value(v, idx, f"qualifier {k}") for k, v in (qualifiers or {}).items()}

    def create_relationship(
        self,
        source: str,
        rel_type: str,
        target: str,
        qualifiers: Optional[Dict[str, Any]] = None,
        graph: Optional[str] = None,
    ) -> OpRef:
        idx = self._next_index
        op: Dict[str, Any] = {
            "op": "create_rel",
            "tuple": _tuple_wire((source, rel_type, target, graph)),
        }
        if qualifiers:
            op["quals"] = self._quals(qualifiers, idx)
        return OpRef(self._add(op))

    def upsert_relationship(
        self,
        source: str,
        rel_type: str,
        target: str,
        qualifiers: Optional[Dict[str, Any]] = None,
        graph: Optional[str] = None,
    ) -> OpRef:
        idx = self._next_index
        op: Dict[str, Any] = {
            "op": "upsert_rel",
            "tuple": _tuple_wire((source, rel_type, target, graph)),
        }
        if qualifiers:
            op["quals"] = self._quals(qualifiers, idx)
        return OpRef(self._add(op))

    def delete_relationship(
        self, ref: Optional[RelRefLike] = None, *, stmt_id: Optional[str] = None
    ) -> "Changeset":
        self._add({"op": "delete_rel", "ref": _rel_ref(ref, stmt_id)})
        return self

    def set_qualifier(
        self,
        ref: Optional[RelRefLike] = None,
        key: str = "",
        value: Any = None,
        *,
        stmt_id: Optional[str] = None,
    ) -> "Changeset":
        idx = self._next_index
        if not key:
            raise ValueError("qualifier key is required")
        self._add(
            {
                "op": "set_qual",
                "ref": _rel_ref(ref, stmt_id),
                "key": str(key),
                "value": _str_value(value, idx, f"qualifier {key}"),
            }
        )
        return self

    def replace_qualifiers(
        self,
        ref: Optional[RelRefLike] = None,
        *,
        stmt_id: Optional[str] = None,
        qualifiers: Optional[Dict[str, Any]] = None,
    ) -> "Changeset":
        idx = self._next_index
        self._add(
            {
                "op": "replace_quals",
                "ref": _rel_ref(ref, stmt_id),
                "quals": self._quals(qualifiers or {}, idx),
            }
        )
        return self

    def remove_qualifier(
        self, ref: Optional[RelRefLike] = None, *, stmt_id: Optional[str] = None, key: str = ""
    ) -> "Changeset":
        if not key:
            raise ValueError("qualifier key is required")
        self._add({"op": "remove_qual", "ref": _rel_ref(ref, stmt_id), "key": str(key)})
        return self

    # -- validation / serialization -------------------------------------------

    def validate(self) -> None:
        """Client-side checks that need no server: non-empty, op_ref targets valid."""
        if not self.ops:
            raise EmptyChangesetError("changeset has no operations")
        for i, op in enumerate(self.ops):
            ref = op.get("ref")
            if isinstance(ref, dict) and "op_ref" in ref:
                j = ref["op_ref"]
                if not (0 <= j < i):
                    raise ChangesetOperationError(
                        i, f"op_ref {j} must point to an earlier operation"
                    )
                if self.ops[j]["op"] not in ("create_rel", "upsert_rel"):
                    raise ChangesetOperationError(
                        i, f"op_ref {j} must point to a create_rel or upsert_rel operation"
                    )

    def to_wire(self) -> Dict[str, Any]:
        return {
            "actor": self.actor,
            "actor_type": self.actor_type,
            "message": self.message,
            "source": self.source,
            "correlation_id": self.correlation_id,
            "expected_head": self.expected_head,
            "idempotency_key": self.idempotency_key,
            "ops": [dict(op) for op in self.ops],
        }

    def canonical_json(self) -> str:
        return canonical_dumps(self.to_wire())

    def fingerprint(self) -> str:
        """SHA-256 hex digest used for idempotency matching.

        **Included fields**: ``actor``, ``actor_type``, ``ops``.

        **Excluded fields**: ``expected_head``, ``idempotency_key``, ``message``,
        ``correlation_id``, ``committed_at``, and all other session-operational fields.

        Consequence: two ``Changeset`` objects with the same ``actor``, ``actor_type``,
        and ``ops`` but different ``expected_head`` values produce **the same
        fingerprint**. If the first commit is replayed with a different
        ``expected_head``, the server returns ``replayed=True`` rather than
        ``StaleHeadError`` — ``expected_head`` is checked separately, after the
        idempotency check.
        """
        scope = {
            "actor": self.actor,
            "actor_type": self.actor_type,
            "ops": [dict(op) for op in self.ops],
        }
        return hashlib.sha256(canonical_dumps(scope).encode("utf-8")).hexdigest()


def canonical_dumps(obj: Any) -> str:
    """Byte-stable JSON: sorted keys, no insignificant whitespace, UTF-8 preserved (R14)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


__all__ = ["Changeset", "OpRef", "RESERVED_PROPERTIES", "ACTOR_TYPES", "canonical_dumps"]
