"""Tests for credential scrubbing on the way into the buffer.

Fixtures are assembled at call time rather than written as literals, for the same reason
`scripts/test_scan_secrets.py` does it: a file full of realistic credentials has recreated
the problem it is testing for, and the repository's own pre-commit scanner has to be able
to read this file without blocking the commit.

Several tests here pin *limits* rather than capabilities. A control whose boundaries are
undocumented gets trusted further than it deserves, and the gap between what this catches
and what a reader assumes it catches is exactly where the next incident lives.
"""

from datetime import datetime, timezone

import pytest

from groundtruth.buffer import EventBuffer
from groundtruth.redact import MARKER, scrub, scrub_event
from groundtruth.types import Classification, Event, Tier

T0 = datetime(2026, 10, 29, 9, 0, tzinfo=timezone.utc)


def synth(prefix: str, length: int = 28, fill: str = "A") -> str:
    """A token-shaped string built at call time, never stored as a literal."""
    return prefix + fill * length


def event(*, evidence: str = "Static page, low churn.",
          label: str = "reading documentation",
          title: str | None = None,
          process: str = "chrome.exe") -> Event:
    return Event(
        ts=T0,
        device_id="dev-1",
        tier=Tier.SCREEN,
        classification=Classification(
            label=label, category="research", confidence=0.88, evidence=evidence
        ),
        process=process,
        window_title=title,
    )


class TestVendorTokens:
    @pytest.mark.parametrize(
        "token",
        [
            synth("b10_"),
            synth("ghp_", 36),
            synth("sk-", 36),
            synth("AKIA", 16, fill="B"),
            synth("xoxb-", 24),
            synth("sk_live_", 24),
            "eyJ" + "a" * 12 + "." + "b" * 12 + "." + "c" * 12,
        ],
    )
    def test_token_is_replaced(self, token):
        out = scrub(f"terminal showing {token} on screen")
        assert token not in out
        assert MARKER in out

    def test_surrounding_context_survives(self):
        """The point of replacing in place rather than dropping the string.

        An evidence field exists so the user can check a label. "terminal window in
        VS Code showing [redacted credential]" still does that job; an empty string
        does not, and would make the label unverifiable at exactly the moment a user
        most wants to know what the agent saw.
        """
        out = scrub(f"terminal window in VS Code showing {synth('b10_')} at a prompt")
        assert out.startswith("terminal window in VS Code showing ")
        assert out.endswith(" at a prompt")


class TestAssignments:
    def test_the_name_is_kept_and_the_value_removed(self):
        out = scrub("editor showing GLIMMER_API_KEY=" + synth("", 30))
        assert "GLIMMER_API_KEY=" in out
        assert out.endswith(MARKER)

    def test_a_prefixed_value_under_a_name_yields_one_marker(self):
        """Vendor rule runs first, so the two rules must not both fire on one value."""
        out = scrub("GLIMMER_API_KEY=" + synth("b10_"))
        assert out.count(MARKER) == 1

    def test_short_values_are_left_alone(self):
        assert scrub("API_KEY=unset") == "API_KEY=unset"


class TestWhatIsDeliberatelyNotCaught:
    def test_a_bare_unprefixed_token_survives(self):
        """Pinned as a known limit, not an oversight.

        Catching any long opaque string would mean redacting commit SHAs, UUIDs, file
        hashes and base64 thumbnails out of legitimate evidence. The cost of that is a
        steady erosion of the field's usefulness, paid on every sample; the cost of this
        gap is one unlabelled secret shape that no vendor reserves.
        """
        bare = "Z" * 40
        assert scrub(f"screen shows {bare}") == f"screen shows {bare}"

    def test_card_details_in_prose_survive(self):
        """The validation log's billing-page sentence, verbatim.

        This module targets credentials, not personal data generally. The sentence that
        motivated it is not itself caught, and a reader should know that before assuming
        the agent is safe to run over a banking tab.
        """
        line = "billing page showing $0.00, free credits $150.00, Visa"
        assert scrub(line) == line


class TestOrdinaryEvidenceIsUntouched:
    @pytest.mark.parametrize(
        "line",
        [
            "Static page, low churn, no input for 40s.",
            "x.com/home For you feed visible with timeline posts and engagement counts",
            "Substack page open to 'THE MOAT MATRIX' with paragraphs about stablecoins",
            "screen shows Claude reviewing validation.md and calibration results",
            "no motion or input data to distinguish browsing, reading, or task context",
        ],
    )
    def test_not_modified(self, line):
        assert scrub(line) == line

    def test_none_passes_through(self):
        assert scrub(None) is None


class TestEventScrubbing:
    def test_window_title_is_scrubbed(self):
        """An OAuth redirect lands a live token in a browser tab title."""
        out = scrub_event(event(title="Redirecting - access_token=" + synth("", 32)))
        assert MARKER in out.window_title

    def test_process_is_not_scrubbed(self):
        """An executable name came from the OS, not from a model reading a screen."""
        out = scrub_event(event(process="sk-launcher.exe"))
        assert out.process == "sk-launcher.exe"

    def test_label_is_scrubbed_too(self):
        out = scrub_event(event(label="copying " + synth("b10_")))
        assert MARKER in out.classification.label


class TestTheBufferGate:
    def test_a_token_never_reaches_the_stored_row(self, tmp_path):
        """The claim the whole module exists to make, checked end to end.

        Asserted against the row read back out of SQLite rather than against `scrub`,
        because the control is only worth anything if it sits on the path the agent
        actually takes.
        """
        token = synth("b10_", 30, fill="Q")
        with EventBuffer(tmp_path / "events.db") as buf:
            buf.append(event(evidence=f"terminal showing {token}"))
            [(_, stored)] = buf.take(10)

        assert token not in stored.classification.evidence
        assert MARKER in stored.classification.evidence

    def test_the_database_file_itself_holds_no_token(self, tmp_path):
        """Reading through `take` would hide a value that was written and re-scrubbed.

        Only the bytes on disk settle it: a sync client, a backup, or anyone with the
        file reads those, not this API.

        Every `events.db*` file is scanned, not just the main one. The buffer runs in
        WAL mode, so a row can sit in `events.db-wal` instead; closing the connection
        checkpoints it back, but depending on that is depending on a detail of when the
        test happens to read.
        """
        token = synth("b10_", 30, fill="R")
        path = tmp_path / "events.db"
        with EventBuffer(path) as buf:
            buf.append(event(evidence=f"terminal showing {token}"))

        for f in tmp_path.glob("events.db*"):
            assert token.encode() not in f.read_bytes(), f.name
