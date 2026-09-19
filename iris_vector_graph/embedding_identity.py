"""Embedding identity: what model produced the vectors in a table, and how wide they are.

An embedding table's model and width used to be known only to whichever engine instance was
writing to it. Two writers configured differently each concluded the schema agreed with
them, and where the two models shared a width, nothing raised: the vectors were well-formed,
cosine distances computed, and the rankings were meaningless.

This module is the comparison half of the fix — pure, with no IRIS import and no engine
reference. The persistence half lives in `Graph_KG.embedding_registry` (see
`_engine/schema.py`). Keeping them apart is what makes the comparison rules unit-testable
without a container.

Spec 226. Decisions: ADR-0005 (the width is never declared inside `TO_VECTOR`), ADR-0006
(identity lives in a registry, not the revision ledger).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Optional

__all__ = [
    "MECHANISMS",
    "EmbeddingIdentity",
    "conflicts",
    "identity_from_config",
    "normalize_model_key",
]

#: How vectors are produced. A closed set (FR-004): a value outside it is rejected rather
#: than recorded, because an unrecognized mechanism cannot be meaningfully compared.
MECHANISMS = (
    "iris-embedding-config",
    "sentence-transformers",
    "external",
)

_WHITESPACE_RUN = re.compile(r"\s+")


def normalize_model_key(raw: Optional[str]) -> str:
    """Normalize a declared configuration into the key that is compared on every write.

    Strip, case-fold, and collapse internal whitespace runs to a single space, so that two
    spellings of the same model compare equal regardless of which side of the wire they were
    written on.

    Deliberately conservative: this does **not** parse, reorder, or canonicalize JSON, and
    it does not hash. Two configurations differing only in key order normalize to different
    keys and therefore conflict. A false refusal is recoverable by an operator; a false
    match corrupts a vector space silently.

    Raises:
        ValueError: when the result is empty. An empty configuration is not a model key.
            "Unknown" is ``None``, which only adoption writes, and which never compares
            equal to anything.
    """
    if raw is None:
        raise ValueError(
            "model_key cannot be derived from None. An engine that cannot state what "
            "produces its vectors offers the undeclared identity (mechanism=None, "
            "model_key=None), it does not offer an empty key."
        )
    collapsed = _WHITESPACE_RUN.sub(" ", raw.strip()).casefold()
    if not collapsed:
        raise ValueError(
            "model_key cannot be empty. An empty configuration is not a model key; "
            "express caller-supplied vectors as mechanism='external' with an explicit "
            "model_key, or offer the undeclared identity (model_key=None)."
        )
    return collapsed


@dataclass(frozen=True)
class EmbeddingIdentity:
    """What a table holds, or what a writer declares it is about to write.

    ``mechanism`` and ``model_key`` are ``None`` together — an undeclared model. That state
    arises two ways, and both are legitimate:

    * **recorded unknown** — what adoption writes (FR-007): a width and dtype read from the
      data dictionary, and no claim about the model, because nothing in the database knows
      it. The first writer to declare a model at that width claims it (FR-008).
    * **offered unknown** — an engine with no ``embedding_config`` and no local embedder,
      storing a caller-supplied list. It declares no model, so no model is compared, its
      **width is still enforced**, and it makes no claim on the recorded row.

    An offered unknown is not a loophole and not a degradation of FR-014: FR-010 refuses a
    write "when the declared model conflicts", and a caller who declares nothing cannot
    conflict on a model. The width check still runs.

    The mechanism follows the **engine's configuration**, not the call site. An engine
    configured with ``embedding_config`` offers ``iris-embedding-config`` from every seam it
    owns, including ``store_embedding``. Deriving it from which function was called would
    make such an engine claim ``iris-embedding-config`` at ``initialize_schema`` and then
    offer ``external`` from ``store_embedding``, so every direct vector write would conflict
    with its own registry row.
    """

    mechanism: Optional[str]
    model_key: Optional[str]
    declared_config: Optional[str] = None
    dimension: Optional[int] = None
    dtype: str = "DOUBLE"

    @property
    def is_unknown(self) -> bool:
        """True when no model is declared."""
        return self.model_key is None

    def normalized(self) -> "EmbeddingIdentity":
        """This identity with ``model_key`` normalized. Unknown identities pass through."""
        if self.model_key is None:
            return self
        return replace(self, model_key=normalize_model_key(self.model_key))

    def describe(self) -> str:
        """A short human-readable form, for exception messages and logs."""
        model = self.model_key if self.model_key is not None else "<unknown>"
        mechanism = self.mechanism if self.mechanism is not None else "<unknown>"
        width = self.dimension if self.dimension is not None else "<undeclared>"
        return f"model={model!r} mechanism={mechanism!r} dimension={width} dtype={self.dtype!r}"


def identity_from_config(
    embedding_config: Optional[str],
    *,
    mechanism: Optional[str] = None,
    dimension: Optional[int] = None,
    dtype: str = "DOUBLE",
) -> EmbeddingIdentity:
    """Build the identity an engine offers, from how that engine is configured.

    ``mechanism`` defaults to ``iris-embedding-config`` when ``embedding_config`` is
    non-empty, because a non-empty config names a model IRIS resolves through
    ``SELECT EMBEDDING(?, ?)``. Callers pass ``mechanism`` explicitly where they know
    better: a worker with a local embedder passes ``sentence-transformers`` and its model
    name; a caller asserting provenance for vectors it computed elsewhere passes
    ``external`` with its own key.

    An empty or ``None`` ``embedding_config`` with no explicit ``mechanism`` yields the
    **undeclared** identity rather than raising — the engine genuinely has no model to name,
    and width-only enforcement is the honest result. Naming a mechanism, on the other hand,
    is a claim about a model, so it requires a model key.
    """
    if mechanism is not None and mechanism not in MECHANISMS:
        raise ValueError(
            f"mechanism {mechanism!r} is not one of {MECHANISMS}. The set is closed: an "
            f"unrecognized mechanism cannot be meaningfully compared."
        )

    has_config = bool(embedding_config and embedding_config.strip())

    if not has_config:
        if mechanism is not None:
            raise ValueError(
                f"mechanism={mechanism!r} was given without an embedding_config to derive "
                f"a model_key from. Naming a mechanism is a claim about a model; pass the "
                f"model as embedding_config, or omit the mechanism to offer the undeclared "
                f"identity."
            )
        return EmbeddingIdentity(
            mechanism=None,
            model_key=None,
            declared_config=embedding_config,
            dimension=dimension,
            dtype=dtype,
        )

    return EmbeddingIdentity(
        mechanism=mechanism or "iris-embedding-config",
        model_key=normalize_model_key(embedding_config),
        declared_config=embedding_config,
        dimension=dimension,
        dtype=dtype,
    )


def conflicts(recorded: EmbeddingIdentity, offered: EmbeddingIdentity) -> Optional[str]:
    """Compare a recorded identity against an offered one.

    Returns ``None`` when the two agree, or a human-readable reason when they do not.

    The model comparison is a 2×2 over (recorded known/unknown) × (offered known/unknown):

    =====================  ==========================  ==========================
    recorded → offered ↓   recorded model known        recorded model unknown
    =====================  ==========================  ==========================
    offered known          must match, else conflict   claim it (FR-008)
    offered unknown        no model comparison         nothing to compare
    =====================  ==========================  ==========================

    Width and dtype are enforced in **all four** cells. That is what makes a wrong-width
    writer refusable on a never-claimed table, and what keeps an engine that declares no
    model from being a way around the contract.
    """
    rec = recorded.normalized()
    off = offered.normalized()

    # Model first: it is the more fundamental disagreement, and the one IRIS cannot see.
    both_models_known = rec.model_key is not None and off.model_key is not None
    if both_models_known and rec.model_key != off.model_key:
        return (
            f"recorded model {rec.model_key!r} but this writer declares "
            f"{off.model_key!r}. Vectors from two models are not comparable even at the "
            f"same width: distances compute and the rankings are meaningless. Use the "
            f"recorded model, write to a different table, or override deliberately with "
            f"set_embedding_identity(..., force=True), which invalidates every vector "
            f"already stored."
        )

    # Mechanism is only meaningful once both sides name a model.
    if both_models_known and rec.mechanism != off.mechanism:
        return (
            f"recorded mechanism {rec.mechanism!r} but this writer declares "
            f"{off.mechanism!r} for the same model {rec.model_key!r}. The same key produced "
            f"by two different mechanisms is not guaranteed to be the same vector space."
        )

    # Width: a property of the column, compared whether or not a model is declared.
    if rec.dimension is not None and off.dimension is not None and rec.dimension != off.dimension:
        # "dimension mismatch" is load-bearing wording, not decoration: it is what
        # `store_embedding` raised before this feature existed, and callers match on it
        # (`tests/integration/test_embeddings_paths.py:80`). Keep the phrase so a width
        # refusal stays recognisable to code written against the old error (FR-020).
        return (
            f"dimension mismatch: recorded dimension {rec.dimension} but this writer "
            f"declares {off.dimension}. The column declaration is what has to change: IRIS enforces "
            f"a declared VECTOR width at INSERT (SQLCODE -104), so widening the writer "
            f"alone cannot work. Migrate the column, or write to a table declared at "
            f"{off.dimension}."
        )

    if rec.dtype.strip().upper() != off.dtype.strip().upper():
        return (
            f"recorded dtype {rec.dtype!r} but this writer declares {off.dtype!r}. The "
            f"element type is part of the column declaration."
        )

    return None
