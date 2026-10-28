"""Tier 2 perception: screen regions as a video scene, via SAM 3.1.

The interesting claim of this module is that the desktop is a *video*, not a sequence of
screenshots. SAM 3.1's object multiplexing tracks up to 16 objects in one forward pass
with stable track ids across frames, and that stability is what makes churn measurable.

Churn is the payoff. A feed scrolling produces high mask instability inside a fixed
bounding area; reading the same region produces low instability. That single signal is
the strongest discriminator between "reading" and "doomscrolling", and it does not exist
in any individual frame — which is precisely why app-name trackers cannot recover it.

Frames live in memory only. Nothing here writes an image to disk, and the frame buffer is
overwritten on each sample.
"""

from __future__ import annotations

from ..types import Region

MAX_TRACKED = 16
"""SAM 3.1 multiplex width. Regions beyond this are dropped by descending area, on the
assumption that a region too small to rank has too little attention to matter."""

REGION_PROMPTS: tuple[str, ...] = (
    "text editor pane",
    "terminal window",
    "web page content",
    "video player",
    "chat panel",
    "image or design canvas",
    "notification popup",
)
"""Text prompts for SAM 3.1's concept segmentation.

Deliberately generic: these describe *kinds of surface*, not applications, so the same
prompt set works across a browser, a native app and a remote desktop. Tuning these is the
cheapest lever on Tier 2 quality.
"""


class RegionTracker:
    """Stateful tracker over consecutive frames.

    Holds the previous frame's masks so churn can be computed as a delta. One instance per
    display; callers must not share one across monitors or track ids will collide.
    """

    def __init__(self, *, endpoint: str, model: str = "sam-3.1") -> None:
        self._endpoint = endpoint
        self._model = model
        self._prev_masks: dict[int, object] = {}

    def track(self, frame: object) -> tuple[Region, ...]:
        """Segment and track regions in `frame`, returning them with churn filled in.

        `frame` is a PIL Image or an equivalent array; it is consumed and not retained.

        Churn for a track id is the symmetric difference between this frame's mask and the
        previous frame's, normalised by the union — so it is 0 for a perfectly static
        region and approaches 1 for a region whose contents fully replaced themselves.
        A track id seen for the first time has no previous mask and reports churn 0.0.
        """
        raise NotImplementedError("SAM 3.1 multiplex tracking — see docs/tier2.md")

    def reset(self) -> None:
        """Drop tracking state.

        Called on display change or after an idle gap, where track ids from before the gap
        no longer refer to the same on-screen things.
        """
        self._prev_masks.clear()


def attention_locus(regions: tuple[Region, ...]) -> Region | None:
    """The region most likely holding attention.

    Focus alone is insufficient — the focused window is often not where the eyes are, which
    is the two-monitor case every existing tracker gets wrong. Ranks by focus first, then
    by area, then by inverse churn, so a large stable region beats a small flickering one.
    """
    if not regions:
        return None
    return max(
        regions,
        key=lambda r: (r.has_focus, r.area_frac, -r.churn),
    )
