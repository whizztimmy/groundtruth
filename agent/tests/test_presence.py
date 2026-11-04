"""Tests for whether an open window counts as an activity.

The bug these exist for: a focused editor was recorded as `deep work` at 0.9 confidence
whether or not anybody was typing in it, because the input signal was measured every
sample and never consulted when the label was written. Minutes of invented work per idle
stretch, asserted confidently.

The hard part is not detecting absence. It is detecting absence without breaking reading,
which looks identical on the input signal and nearly identical on the churn signal. Most
of what follows pins that distinction, because a fix that files twenty minutes of reading
documentation as "not in use" is worse than the bug.
"""

from __future__ import annotations

import pytest

from groundtruth.perceive.churn import ACTIVE, STATIC
from groundtruth.presence import (
    FLOOR,
    FRESH_S,
    STALE_S,
    Attention,
    assess,
    evidence,
)
from groundtruth.main import _carry
from groundtruth.types import Classification, Confidence


class TestSomebodyIsThere:
    def test_recent_input_is_full_confidence(self):
        p = assess(idle_s=1.0, churn=0.0)
        assert p.attention is Attention.ACTIVE
        assert p.confidence == 0.9

    def test_a_pause_for_thought_does_not_count_against_you(self):
        """Half a minute of not typing happens inside real work constantly: reading a
        paragraph, thinking about a line, listening on a call. A window that punished it
        would make the confidence number noise."""
        p = assess(idle_s=FRESH_S - 1, churn=0.0)
        assert p.attention is Attention.ACTIVE
        assert p.confidence == 0.9

    def test_a_moving_screen_without_input_is_passive_not_absent(self):
        """A video, a build log, a feed advancing on its own. The activity is real even
        though nobody touched the keyboard."""
        p = assess(idle_s=STALE_S * 2, churn=ACTIVE)
        assert p.attention is Attention.PASSIVE
        assert p.credits_activity


class TestNobodyIsThere:
    def test_a_static_screen_and_no_input_stops_crediting_the_activity(self):
        """The reported bug, directly: an editor open, nothing typed, nothing moving."""
        p = assess(idle_s=STALE_S, churn=0.0)
        assert p.attention is Attention.UNATTENDED
        assert not p.credits_activity

    def test_it_waits_before_saying_so(self):
        """Just past the fresh window with a still screen is not yet evidence of absence.
        Reading looks exactly like this, and reading is real work."""
        p = assess(idle_s=FRESH_S + 1, churn=0.0)
        assert p.attention is not Attention.UNATTENDED
        assert p.credits_activity

    def test_the_boundary_sits_below_the_idle_threshold(self):
        """This makes the narrower claim, that the activity is not happening. Idle makes
        the stronger one, that the person left. The narrower claim has to be able to fire
        first or it never fires at all."""
        assert STALE_S < 300.0


class TestMissingEvidenceIsNotEvidenceOfAbsence:
    def test_without_a_churn_measurement_it_never_claims_unattended(self):
        """The honesty rule. With Tier 2 disabled no frame is captured, and input alone
        cannot separate somebody reading from an empty chair. Guessing here would file
        every long read as "not in use"."""
        p = assess(idle_s=STALE_S * 10, churn=None)
        assert p.attention is Attention.UNKNOWN
        assert p.credits_activity

    def test_but_confidence_still_decays_without_it(self):
        """The absence of input is a fact whether or not a frame was taken, so the claim
        weakens even where it cannot be withdrawn."""
        assert assess(idle_s=STALE_S, churn=None).confidence < 0.9


class TestConfidenceFollowsTheEvidence:
    def test_it_decays_as_input_recedes(self):
        values = [assess(idle_s=t, churn=None).confidence for t in (FRESH_S, 60, 90, 120, STALE_S)]
        assert values == sorted(values, reverse=True)

    def test_it_floors_rather_than_collapsing(self):
        """A focused window is real evidence of something even with nobody at the
        keyboard. The claim weakens; it does not invert."""
        assert assess(idle_s=STALE_S * 100, churn=None).confidence == FLOOR

    def test_the_floor_is_the_retry_threshold(self):
        """Not arbitrary. Landing exactly here is what hands the sample to Tier 2: the
        escalation policy's low-confidence rule fires below ACCEPT, so a decayed Tier 1
        label gets looked at properly instead of standing unchallenged.
        """
        assert FLOOR == Confidence.RETRY
        assert FLOOR < Confidence.ACCEPT

    def test_a_decayed_label_is_below_accept(self):
        assert assess(idle_s=STALE_S, churn=None).confidence < Confidence.ACCEPT


class TestEvidenceReadsAsObservation:
    @pytest.mark.parametrize(
        "idle_s,churn",
        [(1.0, 0.0), (STALE_S, ACTIVE), (STALE_S, 0.0), (STALE_S, None)],
    )
    def test_it_states_what_was_seen_not_what_it_means(self, idle_s, churn):
        """Same rule as motion.evidence: the reader should be able to disagree with the
        conclusion while accepting the observation. No verdicts in this string."""
        text = evidence(assess(idle_s, churn), idle_s)
        assert text
        for verdict in ("working", "idle", "productive", "wasting", "not in use"):
            assert verdict not in text

    def test_it_names_the_measurement(self):
        text = evidence(assess(STALE_S, 0.0), STALE_S)
        assert "input" in text and "static" in text


class TestTheReportedScenario:
    def test_an_editor_left_open_is_not_deep_work(self):
        """VS Code focused, machine kept awake, nobody there. Three minutes in."""
        p = assess(idle_s=180.0, churn=0.0)
        assert not p.credits_activity

    def test_the_same_editor_being_typed_in_is(self):
        p = assess(idle_s=0.5, churn=0.2)
        assert p.credits_activity
        assert p.confidence == 0.9

    def test_the_same_editor_with_a_build_running_is(self):
        """Output scrolling past counts. Nobody typed, but something is happening."""
        p = assess(idle_s=180.0, churn=ACTIVE)
        assert p.credits_activity

    def test_reading_in_that_editor_still_counts_for_a_while(self):
        """The regression this fix most risks. A minute of reading a file keeps its
        label, at reduced confidence rather than none."""
        p = assess(idle_s=60.0, churn=STATIC / 2)
        assert p.credits_activity
        assert 0.0 < p.confidence < 0.9


class TestCarriedLabelsRespectPresence:
    """Where the first version of this fix leaked.

    Most tier 1 labels are carried forward from the last tier 2 answer rather than looked
    up in the process table, because the interesting applications are the ambiguous ones
    that are not in it. Capping only the table fixed almost nothing: a timeline check
    showed code.exe holding 0.88 through 110 seconds of no input, because carry-forward
    reasserted the old confidence untouched.
    """

    def label(self, confidence=0.88):
        return Classification(
            label="auth refactor", category="deep work",
            confidence=confidence, evidence="editor with a diff open",
        )

    def test_a_carried_label_cannot_outrun_the_presence_evidence(self):
        ceiling = assess(idle_s=110.0, churn=0.0).confidence
        assert _carry(self.label(), ceiling).confidence == ceiling

    def test_it_does_not_inflate_a_label_that_was_already_weaker(self):
        """A cap, not an assignment. A 0.5 classification does not become 0.9 because
        somebody happens to be typing."""
        carried = _carry(self.label(0.5), ceiling=0.9)
        assert carried.confidence == 0.5

    def test_an_attended_machine_carries_the_label_untouched(self):
        """The behaviour the original no-decay rule protects: during focused work the
        label must not drain away just because the rate limit skipped a classification."""
        ceiling = assess(idle_s=1.0, churn=0.0).confidence
        assert _carry(self.label(), ceiling).confidence == 0.88

    def test_the_default_ceiling_changes_nothing(self):
        assert _carry(self.label()).confidence == 0.88

    def test_a_decayed_carry_drops_below_accept(self):
        """Which is what hands it to tier 2 instead of letting it stand."""
        ceiling = assess(idle_s=110.0, churn=0.0).confidence
        assert _carry(self.label(), ceiling).confidence < Confidence.ACCEPT