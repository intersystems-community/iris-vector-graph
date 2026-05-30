from __future__ import annotations

from typing import Any, Literal, runtime_checkable, Protocol

from pydantic import BaseModel, Field

from .errors import IndexNotBuiltError


@runtime_checkable
class IVGIndex(Protocol):
    def search(self, query: Any, k: int = 10, **kwargs) -> list: ...
    def insert(self, id: str, vector: Any) -> None: ...
    def build(self, wait: bool = True, **kwargs) -> dict: ...
    def status(self) -> dict: ...
    def drop(self) -> None: ...
    def info(self) -> dict: ...


IndexType = Literal["vector", "fulltext", "multivector", "neighborhood_vector", "hnsw"]


_BUILD = {
    "vector": lambda e, name, kw: e._build_vector_index(name, **kw),
    "fulltext": lambda e, name, kw: e._build_fulltext_index(name, **kw),
    "multivector": lambda e, name, kw: e._build_multivector_index(name, **kw),
    "neighborhood_vector": lambda e, name, kw: e._build_neighborhood_index(name, **kw),
    "hnsw": lambda e, name, kw: {"type": "hnsw", "available": e._probe_native_vec()},
}
_SEARCH = {
    "vector": lambda e, name, q, k, kw: e._search_vector_index(name, q, k, **kw),
    "fulltext": lambda e, name, q, k, kw: e.bm25_search(name, q, k),
    "multivector": lambda e, name, q, k, kw: e.plaid_search(name, q, k, **kw),
    "neighborhood_vector": lambda e, name, q, k, kw: e._search_neighborhood_index(name, q, k, **kw),
    "hnsw": lambda e, name, q, k, kw: e.search_nodes_by_vector(q, k=k, **kw),
}
_INSERT = {
    "vector": lambda e, name, id_, vec: e._vector_index_insert(name, id_, vec),
    "fulltext": lambda e, name, id_, vec: e.bm25_insert(name, id_, vec),
    "multivector": lambda e, name, id_, vec: e.plaid_insert(name, id_, vec),
    "neighborhood_vector": lambda e, name, id_, vec: e._vector_index_insert(name, id_, vec),
    "hnsw": lambda e, name, id_, vec: e.store_embedding(id_, vec),
}
_DROP = {
    "vector": lambda e, name: e._vector_index_drop(name),
    "fulltext": lambda e, name: e.bm25_drop(name),
    "multivector": lambda e, name: e.plaid_drop(name),
    "neighborhood_vector": lambda e, name: e._neighborhood_index_drop(name),
    "hnsw": lambda e, name: None,
}
_INFO = {
    "vector": lambda e, name: e._vector_index_info(name),
    "fulltext": lambda e, name: e.bm25_info(name),
    "multivector": lambda e, name: e.plaid_info(name),
    "neighborhood_vector": lambda e, name: e._neighborhood_index_info(name),
    "hnsw": lambda e, name: {"type": "hnsw", "available": e._probe_native_vec()},
}


def _rows_of(info: dict) -> int:
    for key in ("rows", "count", "num_vectors", "indexed", "n_docs", "size", "total"):
        if key in info:
            try:
                return int(info[key])
            except (TypeError, ValueError):
                pass
    return 0


class Index(BaseModel):
    """A handle to one index. Build it, check its status, then search it."""

    name: str = Field(min_length=1)
    type: IndexType
    engine: Any

    model_config = {"arbitrary_types_allowed": True}

    def build(self, wait: bool = True, **kwargs) -> dict:
        return _BUILD[self.type](self.engine, self.name, kwargs)

    def status(self) -> dict:
        info = self.info() or {}
        rows = _rows_of(info)
        state = "ready" if rows > 0 else "empty"
        return {"name": self.name, "type": self.type, "state": state, "rows": rows}

    def search(self, query: Any, k: int = 10, **kwargs) -> list:
        if self.status()["state"] == "empty":
            raise IndexNotBuiltError(self.name, rows=0)
        return _SEARCH[self.type](self.engine, self.name, query, k, kwargs)

    def insert(self, id: str, vector: Any) -> None:
        _INSERT[self.type](self.engine, self.name, id, vector)

    def drop(self) -> None:
        _DROP[self.type](self.engine, self.name)
        self.engine._index_registry.pop(self.name, None)

    def info(self) -> dict:
        return _INFO[self.type](self.engine, self.name)


IndexHandle = Index
