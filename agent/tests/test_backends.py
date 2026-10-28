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
        assert Backend.from_env("GLIMMER").placement is Placement.MANAGED

    def test_falls_back_to_shared_key(self, monkeypatch):
        monkeypatch.setenv("GLIMMER_ENDPOINT", "http://127.0.0.1:8080/v1")
        monkeypatch.setenv("GLIMMER_MODEL", "muse-glimmer-30b")
        monkeypatch.setenv("GLIMMER_PLACEMENT", "local")
        monkeypatch.delenv("GLIMMER_API_KEY", raising=False)
        monkeypatch.setenv("META_API_KEY", "shared-key")
        assert Backend.from_env("GLIMMER").api_key == "shared-key"

    def test_strips_trailing_slash(self, monkeypatch):
        monkeypatch.setenv("GLIMMER_ENDPOINT", "http://127.0.0.1:8080/v1/")
        monkeypatch.setenv("GLIMMER_MODEL", "m")
        monkeypatch.setenv("GLIMMER_PLACEMENT", "local")
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
