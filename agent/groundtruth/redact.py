"""Strip credential-shaped text out of derived labels before they are stored.

The capture invariant is about *pixels*: no frame, mask or pixel-derived feature is
written to disk. A description of a frame is not a pixel, and `buffer.py` persists two
strings the model wrote while looking at a screen — `label` and `evidence` — which then
sync to the server. So the invariant held while the thing it was protecting against
walked out through a different door.

This is not hypothetical. `docs/validation.md` records the classifier reading a billing
page back as "$0.00, free credits $150.00, Visa" — verbatim, unprompted. The 1024px
downscale in `capture/screen.py` thins fine body text but does not remove it, and a key
pasted at a terminal prompt is rendered in a monospace face large enough to survive.

What this does *not* claim to do is make the agent safe to run over a password manager.
It catches credentials that announce themselves — a reserved vendor prefix, or a value
sitting after a name like `API_KEY=`. A bare high-entropy string with no prefix and no
label is left alone, deliberately: see `SECRET_ASSIGNMENT` below for why widening that
costs more than it buys.
"""

from __future__ import annotations

import re
from dataclasses import replace

from .types import Event

MARKER = "[redacted credential]"
"""Named rather than blank so a user reviewing an uncertain span can tell the difference
between the system removing something and the model failing to describe it."""

# Reserved vendor prefixes. Near-zero false positives, because the prefix is registered
# and the length requirement excludes prose. The JWT shape is here because a screenshot
# of browser devtools or an Authorization header is a realistic way for one to appear.
VENDOR_TOKENS = re.compile(
    r"""(?x)
    \b(?:
        b10_[A-Za-z0-9._\-]{20,}
      | gh[pousr]_[A-Za-z0-9]{30,}
      | sk-[A-Za-z0-9]{32,}
      | AKIA[0-9A-Z]{16}
      | xox[baprs]-[A-Za-z0-9\-]{10,}
      | [sr]k_(?:live|test)_[A-Za-z0-9]{20,}
      | AIza[A-Za-z0-9_\-]{35}
      | eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}
    )
    """
)

# A long opaque value introduced by a secret-shaped name. This is the shape a screenshot
# of a `.env` file or a terminal `export` produces, and the shape an OAuth redirect puts
# in a window title.
#
# Only the value is replaced; the name is kept. "terminal showing GLIMMER_API_KEY=
# [redacted credential]" is better evidence than "terminal showing [redacted credential]"
# — it still tells the user what they were looking at, which is the entire job of the
# evidence field.
#
# Unlike `scripts/scan_secrets.py` this has no placeholder exemption, and the asymmetry
# is deliberate. A false positive there blocks a commit and gets the hook disabled; a
# false positive here costs a few characters of a sentence nobody will miss. When the
# penalties are that lopsided, the thresholds should not match.
SECRET_ASSIGNMENT = re.compile(
    r"""(?ix)
    (?P<lead>
        \b[A-Z0-9_]*(?:API[_-]?KEY|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL)[A-Z0-9_]*
        \s* [:=] \s* ['"]?
    )
    (?P<value>[A-Za-z0-9/+_\-.]{16,})
    """
)


def scrub(text: str | None) -> str | None:
    """Replace credential-shaped substrings with `MARKER`. `None` passes through.

    Order matters: vendor tokens are matched first so that `API_KEY=b10_...` loses the
    value to the more specific rule and the assignment rule then finds nothing left to do.
    """
    if not text:
        return text
    text = VENDOR_TOKENS.sub(MARKER, text)
    return SECRET_ASSIGNMENT.sub(lambda m: m.group("lead") + MARKER, text)


def scrub_event(event: Event) -> Event:
    """Scrub every field of an event that carries text read off a screen.

    `process` is left alone: it is an executable name the OS supplied, not something a
    model transcribed, and redacting it would only ever damage a legitimate label.
    """
    return replace(
        event,
        window_title=scrub(event.window_title),
        classification=replace(
            event.classification,
            label=scrub(event.classification.label) or "",
            evidence=scrub(event.classification.evidence) or "",
        ),
    )
