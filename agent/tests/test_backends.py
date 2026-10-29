"""Tests for the transport, focused on the placement guard.

The guard is the one place where the project's privacy claim is enforced in code rather
than asserted in a README. If these tests pass and the guard is later weakened, the claim
in `README.md` becomes false — so treat a failure here as a product bug, not a test bug.
"""

import pytest

from groundtruth.backends import (
    Backend,
    BackendError,
    Placement,
    PlacementViolation,
    TrainingTierRefused,
    _extract_text,
    vision,
)


def backend(placement: Placement) -> Backend:
    return Backend(
        base_url="https://example.invalid/v1",
        model="test-model",
        placement=placement,
    )


class TestPlacement:
    @pytest.mark.parametrize(
        "placement,allowed",
        [
            (Placement.LOCAL, True),
            (Placement.SELF_HOSTED, True),
            (Placement.MANAGED, False),
        ],
    )
    def test_pixel_permission(self, placement, allowed):
        assert placement.may_see_pixels is allowed

    @pytest.mark.asyncio
    async def test_vision_refuses_managed_backend(self):
        """The guard must fire before any network call is attempted."""
        with pytest.raises(PlacementViolation) as exc:
            await vision(
                backend(Placement.MANAGED),
                system="classify this",
                user_text="what is happening",
                frame=object(),  # never touched; the guard trips first
            )
        assert "refusing to send screen content" in str(exc.value)

    @pytest.mark.asyncio
    async def test_violation_is_not_a_generic_error(self):
        """Callers must not swallow a privacy violation with a broad except."""
        assert issubclass(PlacementViolation, BackendError)
        with pytest.raises(PlacementViolation):
            await vision(
                backend(Placement.MANAGED), system="s", user_text="u", frame=object()
            )


class TestBackendFromEnv:
    def test_requires_endpoint_and_model(self, monkeypatch):
        monkeypatch.delenv("GLIMMER_ENDPOINT", raising=False)
        monkeypatch.delenv("GLIMMER_MODEL", raising=False)
        with pytest.raises(BackendError, match="must both be set"):
            Backend.from_env("GLIMMER")

    def test_rejects_unknown_placement(self, monkeypatch):
        monkeypatch.setenv("GLIMMER_ENDPOINT", "http://127.0.0.1:8080/v1")
        monkeypatch.setenv("GLIMMER_MODEL", "muse-glimmer-30b")
        monkeypatch.setenv("GLIMMER_PLACEMENT", "somewhere-nice")
        with pytest.raises(BackendError, match="not one of"):
            Backend.from_env("GLIMMER")

    def test_defaults_to_managed(self, monkeypatch):
        """An unset placement must default to the most restrictive option.

        Forgetting to set it should cost a refused call, never a silent leak.
        """
        monkeypatch.setenv("GLIMMER_ENDPOINT", "http://127.0.0.1:8080/v1")
        monkeypatch.setenv("GLIMMER_MODEL", "muse-glimmer-30b")
        monkeypatch.delenv("GLIMMER_PLACEMENT", raising=False)
        # A key, so this test fails only for the reason it is named after. Defaulting to
        # `managed` also makes a credential mandatory, and without this the assertion
        # below would be unreachable behind an unrelated error.
        monkeypatch.setenv("GLIMMER_API_KEY", "k")
        assert Backend.from_env("GLIMMER").placement is Placement.MANAGED

    def test_falls_back_to_shared_key(self, monkeypatch):
        monkeypatch.setenv("GLIMMER_ENDPOINT", "http://127.0.0.1:8080/v1")
        monkeypatch.setenv("GLIMMER_MODEL", "muse-glimmer-30b")
        monkeypatch.setenv("GLIMMER_PLACEMENT", "local")
        monkeypatch.delenv("GLIMMER_API_KEY", raising=False)
        monkeypatch.setenv("META_API_KEY", "shared-key")
        assert Backend.from_env("GLIMMER").api_key == "shared-key"

    def test_a_remote_backend_without_a_key_is_refused(self, monkeypatch):
        """Better a clear error at startup than a 401 inside a retry loop on Sunday."""
        monkeypatch.setenv("GLIMMER_ENDPOINT", "https://api.meta.ai/v1")
        monkeypatch.setenv("GLIMMER_MODEL", "muse-spark-1.3")
        monkeypatch.setenv("GLIMMER_PLACEMENT", "managed")
        monkeypatch.setenv("GLIMMER_API_KEY", "")
        monkeypatch.setenv("META_API_KEY", "   ")
        with pytest.raises(BackendError, match="empty"):
            Backend.from_env("GLIMMER")

    def test_a_local_backend_needs_no_key(self, monkeypatch):
        """llama.cpp on a laptop has nothing to authenticate against."""
        monkeypatch.setenv("GLIMMER_ENDPOINT", "http://127.0.0.1:8080/v1")
        monkeypatch.setenv("GLIMMER_MODEL", "muse-glimmer-30b")
        monkeypatch.setenv("GLIMMER_PLACEMENT", "local")
        monkeypatch.delenv("GLIMMER_API_KEY", raising=False)
        monkeypatch.delenv("META_API_KEY", raising=False)
        assert Backend.from_env("GLIMMER").api_key is None

    def test_a_contributor_checkpoint_is_refused(self, monkeypatch):
        """The discount is paid for with the user's week. Not Tier 4's call to make."""
        monkeypatch.setenv("SPARK_ENDPOINT", "https://api.meta.ai/v1")
        monkeypatch.setenv("SPARK_MODEL", "muse-spark-1.2-contributor")
        monkeypatch.setenv("SPARK_PLACEMENT", "managed")
        monkeypatch.setenv("SPARK_API_KEY", "k")
        monkeypatch.delenv("ALLOW_TRAINING_TIER", raising=False)
        with pytest.raises(TrainingTierRefused, match="contributor-tier"):
            Backend.from_env("SPARK")

    def test_the_training_tier_can_be_chosen_deliberately(self, monkeypatch):
        """Refusing by default is a guardrail, not a policy someone cannot overrule."""
        monkeypatch.setenv("SPARK_ENDPOINT", "https://api.meta.ai/v1")
        monkeypatch.setenv("SPARK_MODEL", "muse-spark-1.2-contributor")
        monkeypatch.setenv("SPARK_PLACEMENT", "managed")
        monkeypatch.setenv("SPARK_API_KEY", "k")
        monkeypatch.setenv("ALLOW_TRAINING_TIER", "1")
        assert Backend.from_env("SPARK").model == "muse-spark-1.2-contributor"

    def test_the_standard_checkpoint_is_not_mistaken_for_one(self, monkeypatch):
        """`-contributor` must match a suffix, not any model with a hyphen in it."""
        monkeypatch.setenv("SPARK_ENDPOINT", "https://api.meta.ai/v1")
        monkeypatch.setenv("SPARK_MODEL", "muse-spark-1.3")
        monkeypatch.setenv("SPARK_PLACEMENT", "managed")
        monkeypatch.setenv("SPARK_API_KEY", "k")
        assert Backend.from_env("SPARK").model == "muse-spark-1.3"

    def test_strips_trailing_slash(self, monkeypatch):
        monkeypatch.setenv("GLIMMER_ENDPOINT", "http://127.0.0.1:8080/v1/")
        monkeypatch.setenv("GLIMMER_MODEL", "m")
        monkeypatch.setenv("GLIMMER_PLACEMENT", "local")
        monkeypatch.delenv("META_API_KEY", raising=False)
        assert Backend.from_env("GLIMMER").base_url == "http://127.0.0.1:8080/v1"


class TestExtractText:
    def test_string_content(self):
        body = {"choices": [{"message": {"content": "hello"}}]}
        assert _extract_text(body) == "hello"

    def test_parts_content(self):
        """Some providers return typed parts rather than a bare string."""
        body = {
            "choices": [
                {"message": {"content": [{"type": "text", "text": "a"}, {"text": "b"}]}}
            ]
        }
        assert _extract_text(body) == "ab"

    def test_unexpected_shape_raises(self):
        with pytest.raises(BackendError, match="unexpected response shape"):
            _extract_text({"nope": True})


class TestReasoningModelResponses:
    """Muse Spark Open reasons before it answers.

    A tight max_tokens makes it spend the whole budget thinking and return null content,
    which previously surfaced as "unexpected content type: NoneType" — an error that
    points at the parser rather than at the real cause, and sent a debugging session
    after the wrong thing.
    """

    def test_truncated_reasoning_names_the_real_cause(self):
        body = {
            "model": "meta-models/muse-spark-open-1026",
            "choices": [{"message": {"content": None}, "finish_reason": "length"}],
        }
        with pytest.raises(BackendError, match="ran out of tokens"):
            _extract_text(body)

    def test_reasoning_without_an_answer_is_explained(self):
        body = {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "reasoning_content": "The screen shows a feed, so I should",
                    },
                    "finish_reason": "stop",
                }
            ]
        }
        with pytest.raises(BackendError, match="reasoning but no answer"):
            _extract_text(body)

    def test_empty_string_is_not_a_valid_answer(self):
        """An empty string is a failure dressed as a success."""
        body = {"choices": [{"message": {"content": "   "}, "finish_reason": "stop"}]}
        with pytest.raises(BackendError, match="empty content"):
            _extract_text(body)

    def test_real_content_still_wins_over_reasoning(self):
        body = {
            "choices": [
                {
                    "message": {
                        "content": '{"category": "research"}',
                        "reasoning_content": "thinking...",
                    },
                    "finish_reason": "stop",
                }
            ]
        }
        assert _extract_text(body) == '{"category": "research"}'
