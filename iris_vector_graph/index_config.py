from __future__ import annotations

from typing import Annotated, List, Literal, Optional, Union

from pydantic import BaseModel, Field


Metric = Literal["cosine", "l2", "ip"]


class _BaseIndexConfig(BaseModel):
    model_config = {"extra": "forbid"}
    name: str = Field(min_length=1)


class VectorIndexConfig(_BaseIndexConfig):
    type: Literal["vector"] = "vector"
    method: Literal["ivf", "vec"] = "ivf"
    metric: Metric = "cosine"
    nlist: int = Field(default=256, gt=0)
    num_trees: int = Field(default=4, gt=0)
    leaf_size: int = Field(default=50, gt=0)
    dim: Optional[int] = Field(default=None, gt=0)


class FulltextIndexConfig(_BaseIndexConfig):
    type: Literal["fulltext"] = "fulltext"
    properties: List[str] = Field(min_length=1)
    k1: float = Field(default=1.5, ge=0)
    b: float = Field(default=0.75, ge=0, le=1)


class MultiVectorIndexConfig(_BaseIndexConfig):
    type: Literal["multivector"] = "multivector"
    dim: int = Field(default=128, gt=0)
    n_clusters: Optional[int] = Field(default=None, gt=0)


class NeighborhoodVectorConfig(_BaseIndexConfig):
    type: Literal["neighborhood_vector"] = "neighborhood_vector"
    buckets: int = Field(default=256, gt=0)
    metric: Metric = "cosine"
    max_buckets: int = Field(default=32, gt=0)


class HNSWIndexConfig(_BaseIndexConfig):
    type: Literal["hnsw"] = "hnsw"
    metric: Metric = "cosine"


IndexConfig = Annotated[
    Union[
        VectorIndexConfig,
        FulltextIndexConfig,
        MultiVectorIndexConfig,
        NeighborhoodVectorConfig,
        HNSWIndexConfig,
    ],
    Field(discriminator="type"),
]


__all__ = [
    "Metric",
    "VectorIndexConfig",
    "FulltextIndexConfig",
    "MultiVectorIndexConfig",
    "NeighborhoodVectorConfig",
    "HNSWIndexConfig",
    "IndexConfig",
]
