"""Block commits that would publish a credential.

This repository is public by hackathon rule, so a secret reaching a commit is not a
near-miss — it is disclosure the moment it is pushed, and rewriting history afterwards
does not help because clones and forks keep the object.

Scans **staged content**, not the working tree. Those differ in exactly the case that
matters: a file edited after `git add` would otherwise be scanned in its safe state while
its unsafe state is what gets committed.

Findings are reported redacted. A scanner that prints the secret it found has moved it
from one file into your terminal, your scrollback, and your CI logs — which is how the
leak this was written for happened in the first place.
"""

from __future__ import annotations

import re
import subprocess
import sys

# Vendor-prefixed tokens. These are near-zero false positives: the prefix is reserved and
# the length requirement excludes prose.
VENDOR_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("Baseten API key", re.compile(r"\bb10_[A-Za-z0-9._\-]{20,}")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("OpenAI-style key", re.compile(r"\bsk-[A-Za-z0-9]{32,}")),
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}")),
    ("Stripe key", re.compile(r"\b[sr]k_(live|test)_[A-Za-z0-9]{20,}")),
    ("Google API key", re.compile(r"\bAIza[A-Za-z0-9_\-]{35}")),
    ("Private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]

# A secret-shaped value assigned to a secret-shaped name. Broader, so it demands both a
# suggestive name and a long opaque value before it will fire.
ASSIGNMENT = re.compile(
    r"""(?ix)
    \b(?P<name>[A-Z0-9_]*(?:API[_-]?KEY|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL)[A-Z0-9_]*)
    \s*[:=]\s*
    ['"]?(?P<value>[A-Za-z0-9/+_\-.]{20,})['"]?
    """
)

# Values that look like secrets but are not. Placeholders are the common case: a template
# that cannot be committed is a template nobody uses.
PLACEHOLDER = re.compile(
    r"""(?ix)
    ^( x{3,} | \.{3,} | <.*> | \$\{.*\} | \{\{.*\}\}
     | your[_-]?.* | my[_-]?.* | example.* | sample.* | dummy.* | placeholder.*
     | changeme.* | replace[_-]?me.* | insert.* | redacted.* | test[_-]?key.*
     | none | null | true | false )$
    """
)

# Paths whose job is to describe secrets rather than hold them.
SKIP_PATHS = (
    ".env.example",
    "scripts/scan_secrets.py",
    "docs/validation.md",
)

SKIP_SUFFIXES = (
    ".lock", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
    ".pdf", ".zip", ".gz", ".woff", ".woff2", ".ttf",
)


def redact(value: str) -> str:
    """Enough to recognise the finding, not enough to use it."""
    head = value[:4]
    return f"{head}{'*' * 8} ({len(value)} chars)"


def staged_files() -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        capture_output=True, text=True, check=True,
    ).stdout
    return [p for p in out.splitlines() if p.strip()]


def staged_content(path: str) -> str | None:
    """The blob as it will be committed, which is not necessarily what is on disk."""
    result = subprocess.run(
        ["git", "show", f":{path}"], capture_output=True, check=False
    )
    if result.returncode != 0:
        return None
    try:
        return result.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None  # binary; nothing text-shaped to find


def scan(path: str, content: str) -> list[tuple[int, str, str]]:
    findings: list[tuple[int, str, str]] = []
    for lineno, line in enumerate(content.splitlines(), 1):
        for label, pattern in VENDOR_PATTERNS:
            match = pattern.search(line)
            if match:
                findings.append((lineno, label, redact(match.group(0))))

        match = ASSIGNMENT.search(line)
        if match and not PLACEHOLDER.match(match.group("value")):
            findings.append(
                (lineno, f"{match.group('name')} assignment", redact(match.group("value")))
            )
    return findings


def main() -> int:
    try:
        paths = staged_files()
    except subprocess.CalledProcessError:
        print("secret scan: not a git repository; skipping")
        return 0

    findings: list[tuple[str, int, str, str]] = []
    for path in paths:
        if path in SKIP_PATHS or path.endswith(SKIP_SUFFIXES):
            continue
        content = staged_content(path)
        if content is None:
            continue
        for lineno, label, redacted in scan(path, content):
            findings.append((path, lineno, label, redacted))

    if not findings:
        return 0

    print()
    print("COMMIT BLOCKED - possible credential in staged content")
    print()
    for path, lineno, label, redacted in findings:
        print(f"  {path}:{lineno}")
        print(f"    {label}: {redacted}")
    print()
    print("This repository is public. A committed secret is disclosed the moment it is")
    print("pushed, and rewriting history does not recall it from clones or forks.")
    print()
    print("  - move the value into .env, which is gitignored")
    print("  - if it was ever real, rotate it; scrubbing is not revoking")
    print("  - a genuine false positive can pass with: git commit --no-verify")
    print()
    return 1


if __name__ == "__main__":
    sys.exit(main())
