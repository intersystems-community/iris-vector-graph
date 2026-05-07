from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel, Field

from iris_vector_graph.cypher.translator import QueryMetadata


class IVGResult(BaseModel):
    columns: list = Field(default_factory=list)
    rows: list = Field(default_factory=list)
    error: Optional[str] = None
    metadata: QueryMetadata = Field(default_factory=QueryMetadata)
    sql: Optional[str] = None
    params: Optional[list] = None

    model_config = {"arbitrary_types_allowed": True}

    def __bool__(self) -> bool:
        return self.error is None

    def __getitem__(self, key: str) -> Any:
        if key == "columns":
            return self.columns
        if key == "rows":
            return self.rows
        if key == "metadata":
            return self.metadata
        if key == "sql":
            if self.sql is None:
                raise KeyError(key)
            return self.sql
        if key == "params":
            if self.params is None:
                raise KeyError(key)
            return self.params
        if key == "error":
            if self.error is None:
                raise KeyError(key)
            return self.error
        raise KeyError(key)

    def __contains__(self, key: object) -> bool:
        if key == "error":
            return self.error is not None
        if key == "sql":
            return self.sql is not None
        if key == "params":
            return self.params is not None
        return key in {"columns", "rows", "metadata"}

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default
