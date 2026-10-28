"""Tier 1 capture: foreground window and owning process.

Free, synchronous, and runs on every sample. Produces an Observation with no label —
deciding what the activity *is* belongs to the escalation policy and Tier 2.

Windows-only for now. The Observation contract is platform-neutral, so an Android or
macOS capture module slots in behind the same return type.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..types import Observation, Tier

try:
    import win32gui
    import win32process
    import psutil

    _AVAILABLE = True
except ImportError:  # pragma: no cover - non-Windows or deps not installed
    _AVAILABLE = False


class CaptureUnavailable(RuntimeError):
    """Raised when the platform capture backend is not importable.

    Surfaced loudly rather than degraded silently: a tracker that quietly stops observing
    is worse than one that refuses to start.
    """


def foreground() -> Observation:
    """Sample the current foreground window.

    Returns an Observation with `process` and `window_title` populated. Both may be None
    when no window holds focus — a locked screen or a desktop with nothing open — which
    the segmenter reads as idle rather than as an activity.
    """
    if not _AVAILABLE:
        raise CaptureUnavailable(
            "win32gui/psutil unavailable; install with: pip install -e agent[dev]"
        )

    ts = datetime.now(timezone.utc)
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return Observation(ts=ts, tier=Tier.WINDOW)

    title = win32gui.GetWindowText(hwnd) or None

    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        process = psutil.Process(pid).name()
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        # A window can die between handle and lookup; a title with no process is still
        # a usable observation, so this is not fatal.
        process = None

    return Observation(ts=ts, tier=Tier.WINDOW, process=process, window_title=title)


def idle_seconds() -> float:
    """Seconds since the last keyboard or mouse input, session-wide.

    Exposed raw rather than only as a boolean because it answers two different questions.
    A long value means the user is away. A *short* value, sampled alongside screen churn,
    means the user is causing the change they are looking at — which is what separates
    scrolling a feed from watching a build log scroll itself.

    Uses GetLastInputInfo, which counts input across the whole session rather than just
    this process.
    """
    if not _AVAILABLE:
        raise CaptureUnavailable("win32 idle detection unavailable")

    import ctypes

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        # Treat an API failure as "input just happened" rather than as idleness: wrongly
        # reporting activity costs a stale label, wrongly reporting idle discards a
        # genuine session.
        return 0.0

    # GetTickCount wraps roughly every 49 days. A wrap would otherwise produce a huge
    # negative, which max() turns into a harmless zero.
    millis_idle = ctypes.windll.kernel32.GetTickCount() - info.dwTime
    return max(0.0, millis_idle / 1000.0)


def is_idle(threshold_s: float = 300.0) -> bool:
    """Whether the user has been input-idle longer than `threshold_s`.

    Idle closes sessions rather than extending them: a window left focused overnight must
    not read as eight hours of work.
    """
    return idle_seconds() >= threshold_s


def input_active(within_s: float) -> bool:
    """Whether any input arrived in roughly the last `within_s` seconds.

    Called with the sample interval, this answers "did the user do something between the
    previous sample and this one" — the per-sample signal the motion model pairs with
    churn.
    """
    return idle_seconds() < within_s
