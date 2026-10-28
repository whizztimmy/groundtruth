"""Tests for the Glimmer classifier.

Two properties matter more than accuracy, which cannot be tested without a model:

1. The classifier never crashes the capture loop. A malformed reply or an unreachable
   backend costs one uncertain span, never a gap in someone's day.
2. An honest "uncertain" survives every path. It is the thing that distinguishes this
   from trackers that guess silently, and it is easy to lose by accident.
"""

import json
from datetime import datetime, timezone

import pytest

from groundtruth.backends import Backend, BackendError, Placement
from groundtruth.perceive.classify import (
    DEFAULT_CATEGORIES,
    Classifier,
    build_prompt,
    parse_response,
)
from groundtruth.types import UNCERTAIN, Observation, Region, Tier

T0 = datetime(2026, 10, 28, 9, 0, tzinfo=timezone.utc)


def backend(placement=Placement.SELF_HOSTED) -> Backend:
    return Backend(
        base_url="https://glimmer.invalid/v1",
        model="muse-glimmer-30b",
        placement=placement,
    )


def region(track_id=1, label="web page content", churn=0.0, area=0.6, focus=True):
    return Region(
        track_id=track_id,
        label=label,
        bbox=(0, 0, 100, 100),
        area_frac=area,
        churn=churn,
        has_focus=focus,
    )


def obs(regions=(), process="chrome.exe", title="a page") -> Observation:
    return Observation(
        ts=T0, tier=Tier.SCREEN, process=process, window_title=title, regions=regions
    )


class TestPrompt:
    def test_lists_categories_and_uncertain(self):
        text = build_prompt(obs(), categories=DEFAULT_CATEGORIES)
        assert "doomscrolling" in text
        assert "uncertain" in text

    def test_includes_region_measurements(self):
        text = build_prompt(
            obs(regions=(region(churn=0.9),)), categories=DEFAULT_CATEGORIES
        )
        assert "web page content" in text
        assert "churning" in text
        assert "60% of screen" in text

    def test_states_motion_as_observation_not_verdict(self):
        """The model must weigh what was seen, not ratify a heuristic.

        If the prompt said "this is doomscrolling", the model could never overrule the
        heuristic when a terminal scrolls during real work.
        """
        text = build_prompt(
            obs(regions=(region(churn=0.9),)),
            categories=DEFAULT_CATEGORIES,
            input_history=[True, True, True],
        ).lower()
        assert "pulling content past" in text
        # "doomscrolling" appears once, in the category list. The motion line must
        # describe the observation without naming a verdict.
        motion_line = next(ln for ln in text.splitlines() if "across recent samples" in ln)
        assert "doomscrolling" not in motion_line

    def test_includes_corrections_for_this_process(self):
        classifier = Classifier(backend())
        classifier.learn("chrome.exe", "reading release notes", "research")
        text = build_prompt(
            obs(),
            categories=DEFAULT_CATEGORIES,
            examples=classifier._examples["chrome.exe"],
        )
        assert "reading release notes -> research" in text

    def test_omits_regions_section_when_there_are_none(self):
        assert "Tracked regions" not in build_prompt(obs(), categories=DEFAULT_CATEGORIES)


class TestParseResponse:
    def test_well_formed(self):
        raw = json.dumps(
            {
                "category": "research",
                "label": "reading API docs",
                "confidence": 0.88,
                "evidence": "Static documentation, low churn.",
            }
        )
        result = parse_response(raw, effort="low")
        assert result.category == "research"
        assert result.confidence == 0.88
        assert result.is_acceptable

    def test_tolerates_code_fence(self):
        raw = '```json\n{"category":"admin","label":"x","confidence":0.9,"evidence":"y"}\n```'
        assert parse_response(raw, effort="low").category == "admin"

    def test_unparseable_becomes_uncertain_not_an_exception(self):
        """A malformed reply must not kill the capture loop."""
        result = parse_response("I think they're browsing!", effort="low")
        assert result.category == UNCERTAIN
        assert result.confidence == 0.0

    def test_uncertain_is_honoured(self):
        raw = json.dumps(
            {"category": "uncertain", "label": "maybe research", "confidence": 0.9,
             "evidence": "Mixed content."}
        )
        result = parse_response(raw, effort="low")
        assert result.category == UNCERTAIN
        assert result.label == UNCERTAIN, "a refusal must not keep a plausible label"
        assert not result.is_acceptable

    def test_confidence_is_clamped(self):
        raw = json.dumps({"category": "admin", "label": "x", "confidence": 4.2, "evidence": "y"})
        assert parse_response(raw, effort="low").confidence == 1.0

    def test_non_numeric_confidence_is_zero(self):
        raw = json.dumps({"category": "admin", "label": "x", "confidence": "high", "evidence": "y"})
        assert parse_response(raw, effort="low").confidence == 0.0

    def test_missing_evidence_is_filled_not_blank(self):
        raw = json.dumps({"category": "admin", "label": "x", "confidence": 0.8})
        assert parse_response(raw, effort="low").evidence

    def test_records_the_effort_used(self):
        raw = json.dumps({"category": "admin", "label": "x", "confidence": 0.8, "evidence": "y"})
        assert parse_response(raw, effort="high").reasoning_effort == "high"


class TestClassifier:
    def test_refuses_a_managed_backend(self):
        with pytest.raises(BackendError, match="requires 'local' or 'self_hosted'"):
            Classifier(backend(Placement.MANAGED))

    def test_corrections_are_kept_per_process(self):
        """Uncertainty in a terminal and in a browser are different problems."""
        c = Classifier(backend())
        c.learn("chrome.exe", "a", "research")
        c.learn("wt.exe", "b", "deep work")
        assert c._examples["chrome.exe"] == [("a", "research")]
        assert c._examples["wt.exe"] == [("b", "deep work")]

    async def test_backend_failure_yields_uncertain(self, monkeypatch):
        """A hiccup costs one answerable span, never a gap in the day."""
        c = Classifier(backend())

        async def boom(*a, **k):
            raise BackendError("connection reset")

        monkeypatch.setattr("groundtruth.perceive.classify.vision", boom)
        result = await c.classify_verified(obs(), frame=object())
        assert result.category == UNCERTAIN
        assert "unreachable" in result.evidence

    async def test_retries_at_higher_effort(self, monkeypatch):
        efforts = []

        async def responder(backend_, system, user_text, frame, **kw):
            efforts.append(kw.get("reasoning_effort"))
            confidence = 0.5 if len(efforts) == 1 else 0.9
            return json.dumps(
                {"category": "research", "label": "x", "confidence": confidence,
                 "evidence": "y"}
            )

        monkeypatch.setattr("groundtruth.perceive.classify.vision", responder)
        result = await Classifier(backend()).classify_verified(obs(), frame=object())
        assert efforts == ["low", "medium"]
        assert result.is_acceptable

    async def test_does_not_retry_a_hopeless_answer(self, monkeypatch):
        """Below the retry band, more compute will not rescue it."""
        calls = []

        async def responder(*a, **kw):
            calls.append(1)
            return json.dumps(
                {"category": "research", "label": "x", "confidence": 0.05, "evidence": "y"}
            )

        monkeypatch.setattr("groundtruth.perceive.classify.vision", responder)
        result = await Classifier(backend()).classify_verified(obs(), frame=object())
        assert len(calls) == 1
        assert result.category == UNCERTAIN

    async def test_exhausted_retries_return_uncertain(self, monkeypatch):
        async def responder(*a, **kw):
            return json.dumps(
                {"category": "research", "label": "x", "confidence": 0.5, "evidence": "y"}
            )

        monkeypatch.setattr("groundtruth.perceive.classify.vision", responder)
        result = await Classifier(backend()).classify_verified(obs(), frame=object())
        assert result.category == UNCERTAIN
