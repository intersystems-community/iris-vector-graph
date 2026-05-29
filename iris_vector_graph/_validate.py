from __future__ import annotations

import math
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


_IVF_METRICS = frozenset({"cosine", "dot", "euclidean", "l2"})
_VEC_METRICS = frozenset({"cosine", "dot", "euclidean"})


def _nonempty(v: str, name: str) -> str:
    if not v or not v.strip():
        raise ValueError(f"{name} must be a non-empty string, got {v!r}")
    return v


def _finite_floats(vec: list, name: str) -> list:
    for i, x in enumerate(vec):
        try:
            f = float(x)
        except (TypeError, ValueError):
            raise ValueError(f"{name}[{i}] is not numeric: {x!r}")
        if math.isnan(f) or math.isinf(f):
            raise ValueError(f"{name}[{i}] contains NaN or Inf")
    return vec


class NodeIdInput(BaseModel):
    node_id: str

    @field_validator("node_id")
    @classmethod
    def must_be_nonempty(cls, v: str) -> str:
        return _nonempty(v, "node_id")


class EdgeInput(BaseModel):
    source_id: str
    predicate: str
    target_id: str

    @field_validator("source_id")
    @classmethod
    def source_nonempty(cls, v: str) -> str:
        return _nonempty(v, "source_id")

    @field_validator("predicate")
    @classmethod
    def predicate_nonempty(cls, v: str) -> str:
        return _nonempty(v, "predicate")

    @field_validator("target_id")
    @classmethod
    def target_nonempty(cls, v: str) -> str:
        return _nonempty(v, "target_id")


class CypherInput(BaseModel):
    cypher_query: str

    @field_validator("cypher_query")
    @classmethod
    def query_nonempty(cls, v: str) -> str:
        return _nonempty(v, "cypher_query")


class IVFBuildInput(BaseModel):
    name: str
    nlist: int = Field(ge=1)
    metric: str = "cosine"
    batch_size: int = Field(default=10000, ge=1)
    build_batch_size: int = Field(default=500, ge=1)

    @field_validator("name")
    @classmethod
    def name_nonempty(cls, v: str) -> str:
        return _nonempty(v, "name")

    @field_validator("metric")
    @classmethod
    def metric_valid(cls, v: str) -> str:
        if v not in _IVF_METRICS:
            raise ValueError(f"metric must be one of {sorted(_IVF_METRICS)}, got {v!r}")
        return v


class VectorSearchInput(BaseModel):
    name: str
    query: list
    k: int = Field(ge=1)
    nprobe: int = Field(default=8, ge=1)

    @field_validator("name")
    @classmethod
    def name_nonempty(cls, v: str) -> str:
        return _nonempty(v, "name")

    @field_validator("query")
    @classmethod
    def query_nonempty(cls, v: list) -> list:
        if not v:
            raise ValueError("query vector cannot be empty")
        return _finite_floats(v, "query")


class BM25BuildInput(BaseModel):
    name: str
    text_props: list
    k1: float = Field(default=1.5, ge=0.0, le=10.0)
    b: float = Field(default=0.75, ge=0.0, le=1.0)

    @field_validator("name")
    @classmethod
    def name_nonempty(cls, v: str) -> str:
        return _nonempty(v, "name")

    @field_validator("text_props")
    @classmethod
    def props_nonempty(cls, v: list) -> list:
        if not v:
            raise ValueError("text_props cannot be empty")
        return v


class BM25SearchInput(BaseModel):
    name: str
    query: str
    k: int = Field(ge=1)

    @field_validator("name")
    @classmethod
    def name_nonempty(cls, v: str) -> str:
        return _nonempty(v, "name")

    @field_validator("query")
    @classmethod
    def query_nonempty(cls, v: str) -> str:
        return _nonempty(v, "query")


class KHop2Input(BaseModel):
    node_id: str

    @field_validator("node_id")
    @classmethod
    def must_be_nonempty(cls, v: str) -> str:
        return _nonempty(v, "node_id")


class TemporalEdgeInput(BaseModel):
    source: str
    predicate: str
    target: str
    timestamp: int = Field(ge=0)
    weight: float = Field(default=1.0, ge=0.0)

    @field_validator("source")
    @classmethod
    def source_nonempty(cls, v: str) -> str:
        return _nonempty(v, "source")

    @field_validator("predicate")
    @classmethod
    def predicate_nonempty(cls, v: str) -> str:
        return _nonempty(v, "predicate")

    @field_validator("target")
    @classmethod
    def target_nonempty(cls, v: str) -> str:
        return _nonempty(v, "target")


class VecSearchInput(BaseModel):
    query: list
    k: int = Field(ge=1)
    nprobe: int = Field(default=8, ge=1)

    @field_validator("query")
    @classmethod
    def query_nonempty(cls, v: list) -> list:
        if not v:
            raise ValueError("query vector cannot be empty")
        return _finite_floats(v, "query")


class DegreeCentralityInput(BaseModel):
    direction: Literal["out", "in", "both"] = "out"
    predicate: Optional[str] = None
    top_k: int = Field(default=10000, ge=0, le=1_000_000)


class BetweennessInput(BaseModel):
    sample_size: int = Field(default=0, ge=0, le=1_000_000)
    direction: Literal["out", "in", "both"] = "out"
    max_hops: int = Field(default=0, ge=0, le=1000)
    top_k: int = Field(default=10000, ge=0, le=1_000_000)
    mem_budget_mb: int = Field(default=256, ge=16, le=8192)


class ClosenessInput(BaseModel):
    formula: Literal["harmonic", "classical"] = "harmonic"
    direction: Literal["out", "in", "both"] = "out"
    max_hops: int = Field(default=0, ge=0, le=1000)
    top_k: int = Field(default=10000, ge=0, le=1_000_000)


class EigenvectorInput(BaseModel):
    max_iter: int = Field(default=30, ge=1, le=1000)
    tol: float = Field(default=1e-6, gt=0.0, lt=1.0)
    top_k: int = Field(default=10000, ge=0, le=1_000_000)


class LeidenInput(BaseModel):
    max_levels: int = Field(default=10, ge=1, le=100)
    gamma: float = Field(default=1.0, gt=0.0, le=100.0)
    tol: float = Field(default=1e-4, gt=0.0, lt=1.0)
    top_k: int = Field(default=10000, ge=0, le=1_000_000)
    mem_budget_mb: int = Field(default=256, ge=16, le=8192)
    random_seed: Optional[int] = Field(default=None, ge=0, le=2**32 - 1)


class TriangleCountInput(BaseModel):
    top_k: int = Field(default=10000, ge=0, le=1_000_000)


class SCCInput(BaseModel):
    top_k: int = Field(default=10000, ge=0, le=1_000_000)


class KCoreInput(BaseModel):
    top_k: int = Field(default=10000, ge=0, le=1_000_000)
