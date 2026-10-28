"""Tier 2 capture: one frame of the display the user is actually looking at.

The most sensitive thing this project touches. Three rules hold without exception:

1. **Nothing is written to disk.** Not a cache, not a temp file, not a debug dump. The
   grabber holds exactly one frame and overwrites it on the next grab.
2. **Only the active display.** A second monitor the user is not looking at is captured
   only if the foreground window is on it. Grabbing every screen because it is easier
   would double the exposure for no gain.
3. **Downscaled before it goes anywhere.** See `TARGET_WIDTH` — this is a privacy control
   as much as a cost one.

`mss` is used rather than Pillow's ImageGrab because it can address individual monitors
and is markedly faster, which matters on a loop that may run every couple of seconds.
"""

from __future__ import annotations

from dataclasses import dataclass

try:
    import mss
    from PIL import Image

    _AVAILABLE = True
except ImportError:  # pragma: no cover - deps not installed
    _AVAILABLE = False

from .window import CaptureUnavailable

TARGET_WIDTH = 1024
"""Width every frame is reduced to before leaving this module.

Chosen as a deliberate trade rather than a performance tweak. At this width the
*structure* of a screen survives — pane layout, where a feed sits, whether a video is
playing, how regions are arranged — which is what SAM 3.1 segments and what the motion
model needs. Fine body text largely does not survive, which is the point: the classifier
should be reading the shape of the screen, not its contents.

Raising this makes the agent see more of someone's private correspondence than its job
requires. It should only be raised with a specific reason.
"""


@dataclass(frozen=True, slots=True)
class Display:
    """One monitor's bounds, in virtual-desktop coordinates."""

    left: int
    top: int
    width: int
    height: int

    def contains(self, x: int, y: int) -> bool:
        return (
            self.left <= x < self.left + self.width
            and self.top <= y < self.top + self.height
        )

    @property
    def as_mss(self) -> dict[str, int]:
        return {
            "left": self.left,
            "top": self.top,
            "width": self.width,
            "height": self.height,
        }


class FrameGrabber:
    """Grabs one downscaled frame at a time.

    Holds a single `mss` instance because creating one per grab leaks display handles on
    Windows over a long-running loop. Not thread-safe; the agent samples serially.
    """

    def __init__(self, *, target_width: int = TARGET_WIDTH) -> None:
        if not _AVAILABLE:
            raise CaptureUnavailable(
                "mss/Pillow unavailable; install with: pip install -e agent[dev]"
            )
        if target_width < 256:
            # Below this, region segmentation stops being meaningful and the grab is
            # wasted work rather than a cheaper one.
            raise ValueError(f"target_width={target_width} is too small to segment")
        self._target_width = target_width
        # mss.mss() is deprecated in favour of mss.MSS; keep the old name as a fallback
        # so an older pinned mss still works.
        factory = getattr(mss, "MSS", None) or mss.mss
        self._sct = factory()

    def close(self) -> None:
        self._sct.close()

    def __enter__(self) -> FrameGrabber:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def displays(self) -> list[Display]:
        """Physical monitors, excluding the virtual union `mss` reports at index 0."""
        return [
            Display(m["left"], m["top"], m["width"], m["height"])
            for m in self._sct.monitors[1:]
        ]

    def display_at(self, x: int, y: int) -> Display:
        """The monitor containing a point, falling back to the primary.

        The fallback matters: a window can sit at negative coordinates while being
        restored, or briefly off-screen during a resolution change, and neither should
        crash a capture loop.
        """
        for display in self.displays():
            if display.contains(x, y):
                return display
        return self.displays()[0]

    def grab(self, display: Display | None = None) -> Image.Image:
        """One downscaled RGB frame, in memory.

        Returns a new image each call rather than mutating a shared one, because the
        caller hands it to an async classifier — a reused buffer could be overwritten
        mid-inference by the next sample.
        """
        display = display or self.displays()[0]
        raw = self._sct.grab(display.as_mss)
        frame = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        return _downscale(frame, self._target_width)

    def grab_active(self) -> Image.Image:
        """The display holding the foreground window.

        Falls back to the primary display when no window has focus, rather than grabbing
        every monitor.
        """
        return self.grab(self._active_display())

    def _active_display(self) -> Display:
        try:
            import win32gui

            hwnd = win32gui.GetForegroundWindow()
            if hwnd:
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                return self.display_at((left + right) // 2, (top + bottom) // 2)
        except Exception:
            # A window can die between handle and rect. The primary display is a safe
            # answer and a failed grab is worse than a slightly wrong one.
            pass
        return self.displays()[0]


def _downscale(frame: Image.Image, target_width: int) -> Image.Image:
    """Reduce to `target_width`, preserving aspect ratio. Never upscales.

    Upscaling a small window would invent detail the model might read as real, and cost
    tokens to encode it.
    """
    if frame.width <= target_width:
        return frame
    ratio = target_width / frame.width
    return frame.resize(
        (target_width, max(1, round(frame.height * ratio))), Image.Resampling.LANCZOS
    )
