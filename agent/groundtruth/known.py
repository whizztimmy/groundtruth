"""Processes whose activity the application name genuinely does describe.

The product's whole argument is that an application name is a poor proxy for an activity.
That argument is about applications that can host *anything* — a browser, a terminal, a
chat client. It is not true of every application: a photo editor in the foreground really
does mean photo editing, and spending a model call to discover that would be waste.

So this table is the Tier 1 labeller, and it is what makes the claim in `ARCHITECTURE.md`
true — that roughly 70% of a day resolves for free. A process here is resolved without
ever looking at the screen, which is cheaper *and* more private than the alternative.

Membership is a claim that the name is sufficient. When in doubt, leave a process out:
an omission costs one model call, while a wrong entry silently mislabels every minute
spent in that application.
"""

from __future__ import annotations

PROCESS_CATEGORIES: dict[str, tuple[str, str]] = {
    # Creative tools: the application is the activity.
    "photoshop.exe": ("design", "image editing"),
    "illustrator.exe": ("design", "vector design"),
    "figma.exe": ("design", "interface design"),
    "blender.exe": ("design", "3d modelling"),
    "gimp.exe": ("design", "image editing"),
    "inkscape.exe": ("design", "vector design"),
    "afterfx.exe": ("design", "motion graphics"),
    "premiere.exe": ("design", "video editing"),
    "davinciresolve.exe": ("design", "video editing"),
    "obs64.exe": ("design", "screen recording"),
    # Communication: who is being talked to is private, that it is talking is not.
    "zoom.exe": ("communication", "video call"),
    "teams.exe": ("communication", "video call"),
    "outlook.exe": ("communication", "email"),
    "thunderbird.exe": ("communication", "email"),
    # Admin and documents.
    "excel.exe": ("admin", "spreadsheet"),
    "winword.exe": ("admin", "document editing"),
    "powerpnt.exe": ("admin", "presentation"),
    "acrobat.exe": ("admin", "reading a pdf"),
    # Entertainment where the app admits what it is.
    "spotify.exe": ("entertainment", "music"),
    "vlc.exe": ("entertainment", "video"),
    "steam.exe": ("entertainment", "games"),
    # System surfaces that are not activities at all.
    "lockapp.exe": ("idle", "lock screen"),
    "searchhost.exe": ("idle", "system search"),
    "shellexperiencehost.exe": ("idle", "system shell"),
}


def lookup(process: str | None) -> tuple[str, str] | None:
    """Category and label for a process, or None when the name is not sufficient.

    None is the interesting answer: it means Tier 1 cannot resolve this, so the sample
    must escalate rather than be assigned a guess.
    """
    if not process:
        return None
    return PROCESS_CATEGORIES.get(process.lower())


def is_known(process: str | None) -> bool:
    return lookup(process) is not None
