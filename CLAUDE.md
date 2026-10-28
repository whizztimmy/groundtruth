# GroundTruth — working notes

## Commits

**Do not add AI attribution trailers.** No `Co-Authored-By: Claude`, no "Generated with
Claude Code", no tool attribution of any kind in commit messages or PR descriptions.

Write the commit message as the author of the change. Explain why the change was made,
not what the diff already shows.

## Context

Entry for the Meta Global AI Developer Hackathon (Oct 26 – Nov 4, 2026), Indie Developer
track. Submission is due Nov 4 at 9:00 PM ET.

All code must be written within the event window, and the repository is public. Do not
vendor, copy, or adapt code from outside sources without checking the licence — "uses
code, data or assets you do not have the rights to" is a disqualifying condition.

## Architecture

Read `README.md` and `ARCHITECTURE.md` before changing anything structural. The design
decisions there were made deliberately and the reasoning is written down.

Two invariants are load-bearing and must not be weakened without saying so explicitly:

1. **Placement.** Screen frames go to `local` or `self_hosted` backends only. Sending a
   frame to a `managed` backend raises `PlacementViolation` — see
   `agent/groundtruth/backends.py`. An unset placement defaults to `managed` so that a
   misconfiguration costs a refused call rather than a silent leak. The README makes a
   privacy claim to users; this is what makes it true.

2. **No frame touches disk.** Capture holds a single in-memory buffer and overwrites it.
   Nothing writes a screenshot, a mask, or derived pixel data to storage.

The API contract in `docs/API.md` is frozen. Implement it as written; if something in it
is wrong, raise it rather than diverging silently.

## Style

- Type hints throughout, `from __future__ import annotations` in Python modules.
- Comments explain *why*, never restate the line. If a rule has a reason, write the
  reason down — the escalation policy in `agent/groundtruth/escalate.py` is the model to
  follow.
- Tests cover the reasoning, not just the happy path. A test that would still pass if the
  rule it covers were deleted is not earning its place.

## Running things

```bash
# Agent tests
cd agent && ./.venv/Scripts/python.exe -m pytest tests/ -q
```

Secrets live in `.env`, which is gitignored. Never commit a key, and never print one into
a log or a commit message.
