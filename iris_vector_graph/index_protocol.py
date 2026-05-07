from __future__ import annotations

from typing import Any, Literal, TYPE_CHECKING, runtime_checkable, Protocol

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    pass


@runtime_checkable
class IVGIndex(Protocol):
    def search(self, query: Any, k: int = 10, **kwargs) -> list: ...
    def insert(self, id: str, vector: Any) -> None: ...
    def drop(self) -> None: ...
    def info(self) -> dict: ...


_SEARCH = {
    "ivf":   lambda e, name, q, k, kw: e.ivf_search(name, q, k, **kw),
    "bm25":  lambda e, name, q, k, kw: e.bm25_search(name, q, k),
    "vec":   lambda e, name, q, k, kw: e.vec_search(name, q, k, **kw),
    "plaid": lambda e, name, q, k, kw: e.plaid_search(name, q, k),
}
_INSERT = {
    "ivf":   lambda e, name, id_, vec: e.ivf_insert(name, id_, vec),
    "bm25":  lambda e, name, id_, vec: e.bm25_insert(name, id_, vec),
    "vec":   lambda e, name, id_, vec: e.vec_insert(name, id_, vec),
    "plaid": lambda e, name, id_, vec: e.plaid_insert(name, id_, vec),
}
_DROP = {
    "ivf":   lambda e, name: e.ivf_drop(name),
    "bm25":  lambda e, name: e.bm25_drop(name),
    "vec":   lambda e, name: e.vec_drop(name),
    "plaid": lambda e, name: e.plaid_drop(name),
}
_INFO = {
    "ivf":   lambda e, name: e.ivf_info(name),
    "bm25":  lambda e, name: e.bm25_info(name),
    "vec":   lambda e, name: e.vec_info(name),
    "plaid": lambda e, name: e.plaid_info(name),
}


class IndexHandle(BaseModel):
    name: str = Field(min_length=1)
    type: Literal["ivf", "bm25", "vec", "plaid"]
    engine: Any

    model_config = {"arbitrary_types_allowed": True}

    def search(self, query: Any, k: int = 10, **kwargs) -> list:
        return _SEARCH[self.type](self.engine, self.name, query, k, kwargs)

    def insert(self, id: str, vector: Any) -> None:
        _INSERT[self.type](self.engine, self.name, id, vector)

    def drop(self) -> None:
        _DROP[self.type](self.engine, self.name)

    def info(self) -> dict:
        return _INFO[self.type](self.engine, self.name)
