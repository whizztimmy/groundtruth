"""Tests for the secret scanner.

Every fixture here is synthetic — repeated characters and obvious filler — because a test
suite containing a realistic credential has recreated the problem it is testing for. The
patterns match on shape, so filler exercises them exactly as a real value would.

The false-positive cases matter as much as the true positives. A scanner that blocks
ordinary commits gets bypassed with `--no-verify` within a day, and a safeguard everyone
routes around protects nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scan_secrets import PLACEHOLDER, redact, scan  # noqa: E402


def flagged(line: str) -> bool:
    return bool(scan("example.txt", line))


def synth(prefix: str, length: int = 28, fill: str = "A") -> str:
    """Build a token-shaped string at call time.

    Assembled rather than written as a literal so this file contains no string that the
    scanner would match. The alternative — adding this path to the scanner's skip list —
    would carve a hole in the safeguard exactly where someone might hide something, and a
    test suite the scanner refuses to read is a test suite that stops protecting anything.
    """
    return prefix + fill * length


def pem(kind: str) -> str:
    """A PEM private-key header, assembled for the same reason as `synth`."""
    return "-----BEGIN {} PRIVATE KEY-----".format(kind)


class TestVendorTokens:
    @pytest.mark.parametrize(
        "line",
        [
            "GLIMMER_API_KEY=" + synth("b10_"),
            synth("b10_"),
            'token = "' + synth("ghp_", 36) + '"',
            'key: "' + synth("sk-", 36) + '"',
            synth("AKIA", 16),
            'slack = "' + synth("xoxb-", 24) + '"',
            'stripe = "' + synth("sk_live_", 24) + '"',
            pem("RSA"),
            pem("OPENSSH"),
        ],
    )
    def test_detected(self, line):
        assert flagged(line)

    def test_bare_token_is_caught(self):
        """The exact shape that defeated PowerShell's own filter.

        PSReadLine suppresses lines whose *context* looks secret-handling. A naked token
        has no context, so it was saved to disk. This scanner matches on the value, which
        is why it catches what that filter could not.
        """
        assert flagged(synth("b10_"))


class TestAssignments:
    def test_long_opaque_value_under_a_secret_name(self):
        assert flagged('DATABASE_PASSWORD = "' + synth("", 28) + '"')

    def test_short_value_is_not_enough(self):
        """Demanding length keeps ordinary config out of the way."""
        assert not flagged('API_KEY = "abc"')

    def test_innocuous_name_with_long_value_is_ignored(self):
        assert not flagged('DESCRIPTION = "a fairly long sentence about nothing at all"')


class TestFalsePositives:
    @pytest.mark.parametrize(
        "line",
        [
            "META_API_KEY=",
            "GLIMMER_API_KEY=<your key>",
            "SPARK_API_KEY=your_key_here",
            "API_KEY=${META_API_KEY}",
            "TOKEN={{ secret_token }}",
            "PASSWORD=changeme",
            "SPARK_ENDPOINT=https://api.meta.ai/v1",
            "GLIMMER_MODEL=meta-models/muse-spark-open-1026",
            "The key is stored in .env and never committed.",
            "confidence = min(1.0, max(0.0, confidence))",
            "GLIMMER_PLACEMENT=self_hosted",
            'parser.add_argument("--model-prefix", default="SPARK")',
        ],
    )
    def test_not_flagged(self, line):
        assert not flagged(line)

    def test_the_projects_own_env_template_passes(self):
        """`.env.example` must stay committable or nobody can clone and run this."""
        template = Path(__file__).resolve().parent.parent / ".env.example"
        if not template.exists():
            pytest.skip("no .env.example")
        findings = scan(".env.example", template.read_text(encoding="utf-8"))
        assert findings == [], findings


class TestRedaction:
    def test_redaction_keeps_only_a_prefix(self):
        out = redact(synth("b10_"))
        assert out.startswith("b10_")
        assert "AAAAAAAA" not in out

    def test_redaction_reports_length(self):
        assert "(30 chars)" in redact("A" * 30)

    def test_a_finding_never_carries_the_value(self):
        """A scanner that prints the secret has moved it into your terminal and CI logs.

        That is the failure mode this whole tool exists to prevent, so it must not be
        the tool's own behaviour.
        """
        secret = synth("b10_", 30, fill="Z")
        [(_, _, reported)] = scan("x.txt", secret)
        assert secret not in reported


class TestPlaceholderRule:
    @pytest.mark.parametrize(
        "value", ["<your key>", "your_key_here", "xxxxxxxx", "changeme", "REDACTED"]
    )
    def test_recognised(self, value):
        assert PLACEHOLDER.match(value)

    def test_a_real_looking_value_is_not_a_placeholder(self):
        assert not PLACEHOLDER.match("A" * 32)
