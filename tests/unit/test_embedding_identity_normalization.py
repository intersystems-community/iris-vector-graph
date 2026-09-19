"""Unit tests for iris_vector_graph.embedding_identity (spec 226, T006).

This module is the only pure logic in the feature, and the only part unit-tested. Everything
that touches a cursor, a vector, or the registry is tested live against
`ivg-iris-enterprise` — `mock.patch(..., create=True)` has previously fabricated missing
attributes in `embeddings.py` and hidden three real bugs, so it is not used here.

The contract under test is `specs/226-embedding-identity-contract/contracts/
embedding_identity_api.md`.
"""

import pytest

from iris_vector_graph.embedding_identity import (
    MECHANISMS,
    EmbeddingIdentity,
    conflicts,
    identity_from_config,
    normalize_model_key,
)


class TestNormalizeModelKey:
    def test_strips_surrounding_whitespace(self):
        assert normalize_model_key("  my-model-v1  ") == "my-model-v1"

    def test_case_folds(self):
        assert normalize_model_key("My-Model-V1") == "my-model-v1"
        assert normalize_model_key("MY-MODEL-V1") == normalize_model_key("my-model-v1")

    def test_collapses_internal_whitespace_runs(self):
        assert normalize_model_key("sentence  transformers\tall\nmini") == (
            "sentence transformers all mini"
        )

    def test_idempotent(self):
        once = normalize_model_key("  Sentence   Transformers  ")
        assert normalize_model_key(once) == once

    @pytest.mark.parametrize("raw", ["", "   ", "\t\n", None])
    def test_empty_raises(self, raw):
        """An empty configuration is not a model key. 'Unknown' is None, which only
        adoption writes, and which never compares equal to anything."""
        with pytest.raises(ValueError):
            normalize_model_key(raw)

    def test_does_not_canonicalize_json(self):
        """Two configs differing only in key order must NOT normalize to the same key.

        A false refusal is recoverable by an operator; a false match silently corrupts a
        vector space. Conservative normalization is the deliberate choice (ADR-0006).
        """
        a = normalize_model_key('{"model": "m", "dim": 128}')
        b = normalize_model_key('{"dim": 128, "model": "m"}')
        assert a != b


class TestMechanisms:
    def test_closed_set_is_exactly_the_three_from_fr_004(self):
        assert MECHANISMS == (
            "iris-embedding-config",
            "sentence-transformers",
            "external",
        )

    def test_is_a_tuple_so_callers_cannot_extend_it(self):
        assert isinstance(MECHANISMS, tuple)


class TestEmbeddingIdentity:
    def test_is_frozen(self):
        ident = EmbeddingIdentity(mechanism="external", model_key="m", dimension=128)
        with pytest.raises(Exception):
            ident.model_key = "other"

    def test_is_unknown_is_model_key_is_none(self):
        assert EmbeddingIdentity(mechanism=None, model_key=None).is_unknown is True
        assert EmbeddingIdentity(mechanism="external", model_key="m").is_unknown is False

    def test_dtype_defaults_to_double(self):
        assert EmbeddingIdentity(mechanism="external", model_key="m").dtype == "DOUBLE"


class TestIdentityFromConfig:
    def test_non_empty_config_defaults_to_iris_embedding_config(self):
        ident = identity_from_config("my-model-v1", dimension=128)
        assert ident.mechanism == "iris-embedding-config"
        assert ident.model_key == "my-model-v1"
        assert ident.declared_config == "my-model-v1"
        assert ident.dimension == 128

    def test_declared_config_keeps_the_raw_string(self):
        """The normalized key is for comparison; the raw string is for diagnostics."""
        ident = identity_from_config("  My-Model-V1  ")
        assert ident.model_key == "my-model-v1"
        assert ident.declared_config == "  My-Model-V1  "

    def test_explicit_mechanism_overrides_the_default(self):
        ident = identity_from_config("all-MiniLM-L6-v2", mechanism="sentence-transformers")
        assert ident.mechanism == "sentence-transformers"
        assert ident.model_key == "all-minilm-l6-v2"

    @pytest.mark.parametrize("empty", [None, "", "   "])
    def test_empty_config_yields_the_undeclared_identity_rather_than_raising(self, empty):
        """An engine with no embedding_config and no local embedder genuinely has no model
        to name. Forcing it to invent one would break every existing
        store_embedding(list) caller (FR-020); width-only enforcement is the honest result.
        """
        ident = identity_from_config(empty, dimension=128)
        assert ident.is_unknown is True
        assert ident.mechanism is None
        assert ident.model_key is None
        assert ident.dimension == 128  # the width survives

    def test_mechanism_outside_the_closed_set_raises(self):
        with pytest.raises(ValueError):
            identity_from_config("m", mechanism="hand-rolled")

    def test_explicit_mechanism_with_empty_config_still_raises(self):
        """Naming a mechanism is a claim about a model, so it needs a model key."""
        with pytest.raises(ValueError):
            identity_from_config("", mechanism="external")


def _ident(model_key, dimension=128, mechanism="iris-embedding-config", dtype="DOUBLE"):
    return EmbeddingIdentity(
        mechanism=None if model_key is None else mechanism,
        model_key=model_key,
        dimension=dimension,
        dtype=dtype,
    )


class TestConflictsModelKey:
    """The 2×2 from the contract. Width is enforced in all four cells."""

    def test_both_known_and_equal_agrees(self):
        assert conflicts(_ident("m"), _ident("m")) is None

    def test_both_known_and_different_conflicts(self):
        reason = conflicts(_ident("m"), _ident("other"))
        assert reason is not None
        assert "m" in reason and "other" in reason

    def test_normalization_applies_before_comparison(self):
        assert conflicts(_ident("my-model"), _ident("MY-MODEL")) is None

    def test_recorded_unknown_offered_known_agrees_at_the_same_width(self):
        """This is the claim case (FR-008): adoption recorded a width, no model."""
        assert conflicts(_ident(None), _ident("m")) is None

    def test_recorded_unknown_offered_known_conflicts_at_a_different_width(self):
        """Refusable even though the model was never claimed — the pre-existing
        contradiction FR-021 has the release notes explain."""
        reason = conflicts(_ident(None, dimension=128), _ident("m", dimension=768))
        assert reason is not None
        assert "768" in reason and "128" in reason

    def test_recorded_known_offered_unknown_agrees_at_the_same_width(self):
        """A caller who declares nothing cannot conflict on a model."""
        assert conflicts(_ident("m"), _ident(None)) is None

    def test_recorded_known_offered_unknown_conflicts_at_a_different_width(self):
        """Declaring no model must not be a way past the contract."""
        assert conflicts(_ident("m", dimension=128), _ident(None, dimension=768)) is not None

    def test_both_unknown_agrees_at_the_same_width(self):
        assert conflicts(_ident(None), _ident(None)) is None

    def test_both_unknown_conflicts_at_a_different_width(self):
        assert conflicts(_ident(None, dimension=128), _ident(None, dimension=768)) is not None


class TestConflictsMechanism:
    def test_compared_only_when_both_model_keys_are_known(self):
        recorded = EmbeddingIdentity(
            mechanism="iris-embedding-config", model_key="m", dimension=128
        )
        offered = EmbeddingIdentity(mechanism="sentence-transformers", model_key="m", dimension=128)
        reason = conflicts(recorded, offered)
        assert reason is not None
        assert "mechanism" in reason.lower()

    def test_not_compared_when_one_model_key_is_unknown(self):
        recorded = EmbeddingIdentity(mechanism=None, model_key=None, dimension=128)
        offered = EmbeddingIdentity(mechanism="sentence-transformers", model_key="m", dimension=128)
        assert conflicts(recorded, offered) is None


class TestConflictsDimension:
    def test_equal_widths_agree(self):
        assert conflicts(_ident("m", dimension=128), _ident("m", dimension=128)) is None

    def test_different_widths_conflict(self):
        assert conflicts(_ident("m", dimension=128), _ident("m", dimension=384)) is not None

    def test_none_on_either_side_means_unknown_not_agrees(self):
        """A column with no declared length records dimension = NULL. Unknown must not be
        read as agreement — but neither is it a conflict, because nothing is known to
        disagree with."""
        assert conflicts(_ident("m", dimension=None), _ident("m", dimension=128)) is None
        assert conflicts(_ident("m", dimension=128), _ident("m", dimension=None)) is None

    def test_width_conflict_message_says_the_declaration_must_change(self):
        reason = conflicts(_ident("m", dimension=128), _ident("m", dimension=768))
        assert "declar" in reason.lower()

    def test_width_conflict_message_keeps_the_pre_feature_wording(self):
        """FR-020: `store_embedding` raised "dimension mismatch" before this feature.

        Callers match on that phrase — `tests/integration/test_embeddings_paths.py:80`
        does — so a width refusal has to stay recognisable to code written against the
        old error. This is a compatibility assertion, not a style one.
        """
        reason = conflicts(_ident("m", dimension=128), _ident("m", dimension=2))
        assert "dimension mismatch" in reason


class TestConflictsDtype:
    def test_compared_case_insensitively(self):
        assert conflicts(_ident("m", dtype="DOUBLE"), _ident("m", dtype="double")) is None

    def test_different_dtype_conflicts(self):
        reason = conflicts(_ident("m", dtype="DOUBLE"), _ident("m", dtype="FLOAT"))
        assert reason is not None
        assert "dtype" in reason.lower()

    def test_dtype_compared_even_when_both_models_are_unknown(self):
        """Like width, dtype is a property of the column, not of the model."""
        assert conflicts(_ident(None, dtype="DOUBLE"), _ident(None, dtype="FLOAT")) is not None


class TestConflictsReportsOneFieldAtATime:
    def test_names_the_field_that_differs(self):
        reason = conflicts(_ident("m", dimension=128), _ident("other", dimension=768))
        assert reason is not None
        # Model is the more fundamental disagreement; it is what the message leads with.
        assert "model" in reason.lower()
