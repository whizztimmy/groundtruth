# GroundTruth

**Your phone says "Chrome: 4 hours." GroundTruth says what those four hours actually were.**

Every activity tracker ever built classifies your time by *application name*. So Chrome is
"productive" whether you were reading API docs or three hours deep in a feed. VS Code is
"coding" whether you were shipping or staring at a file you opened an hour ago. The
distinction people actually care about — focused work vs. doomscrolling, devotion vs.
idle reading — is **semantic**, and no amount of window-title metadata can recover it.

GroundTruth replaces app-name heuristics with an always-on local agent that understands
what you were *doing*.

```
  Screen Time                     GroundTruth
  ───────────                     ───────────
  Chrome ............ 4h 12m      Scripture reading ......... 25m
  VS Code ........... 2h 03m      Deep work (auth refactor) . 1h 40m
  Instagram ......... 0h 48m      Doomscrolling ............. 2h 31m
                                  Research (reading docs) ... 1h 09m
                                  Gym (voice-logged) ........ 55m
                                  Idle / away ............... 0h 23m
```

## Why this is built on open weights

The most sensitive data a person has is their own screen. A tracker that understands your
screen is only acceptable if that understanding never leaves your machine.

So GroundTruth runs **Muse Glimmer** — Meta's Apache-2.0, 30B, always-on local agent
model — as its classifier. Your screen content is perceived locally and discarded; only
derived labels are ever persisted or synced. The model comes to your data instead of your
data going to a model.

That is not a feature bolted onto a time tracker. It is the entire thesis, and it is a
working implementation of the argument in *The Future Is for Everyone*: superintelligence
that belongs to the individual, running on hardware the individual controls.

## How it works

A three-tier escalation ladder. Each tier is more expensive and more revealing than the
last, so GroundTruth only climbs when the tier below it is genuinely ambiguous.

| Tier | Signal | Cost | Fires when |
|------|--------|------|-----------|
| 1 | Foreground window + process name | Free | Always |
| 2 | Screen regions via **SAM 3.1** + classification via **Muse Glimmer** | Local GPU | Tier 1 is ambiguous (e.g. a browser) |
| 3 | Voice check-in via **Muse Voice Transcribe** | Remote, rare | Off-screen time — gym, devotion, travel |

Photoshop is unambiguous; a browser never is. Roughly 70% of a day resolves at Tier 1 for
free, which is what makes always-on local operation affordable.

### SAM 3.1 as an attention sensor

Tier 2 does something SAM 3.1 was not built for. Rather than segmenting photographs,
GroundTruth treats **the desktop as a video scene** and uses SAM 3.1's object multiplexing
(up to 16 objects per forward pass) to track screen regions over time — editor, terminal,
feed, video player, chat.

This matters because the unit of attention is not the window. A developer with docs on one
monitor and a feed on the other is the normal case, and every existing tracker scores that
as 100% of whichever window held focus. GroundTruth measures where attention actually sat.

## Meta models used

| Model | Role |
|-------|------|
| **Muse Glimmer** (open weights, local) | Always-on activity classification, session labelling |
| **SAM 3.1** | Multiplexed screen-region tracking as an attention signal |
| **Muse Voice Transcribe** | Off-screen activity capture via voice check-in |
| **Muse Spark / Muse Wonder** | Escalation tier — weekly synthesis and pattern reasoning |

## Platforms

| Platform | Capture method | Status |
|----------|---------------|--------|
| Windows | Full three-tier agent | Primary |
| Android | `UsageStatsManager` + voice check-in | Planned |
| iOS | Shortcuts automations + Focus triggers + voice | Constrained — see below |

**On iOS:** Apple's `DeviceActivityReport` renders inside a sandboxed extension
specifically so third-party code cannot read usage values out. There is no API, no
entitlement, and no workaround. GroundTruth works within that limit using Shortcuts
personal automations, Focus-mode boundaries, and voice check-ins rather than pretending
the restriction does not exist.

---

## Hackathon disclosure

Built for the **Meta Global AI Developer Hackathon** (Oct 26 – Nov 4, 2026), Indie
Developer track.

**100% of the code in this repository was written during the hackathon window.** The
repository was created on Oct 28, 2026 and has no prior history, no pre-existing codebase,
and no vendored work from earlier projects. Every commit is dated within the event period
and the full history is public.

## License

MIT — see [LICENSE](LICENSE).
