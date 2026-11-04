# GroundTruth

**Your phone says "Chrome: 4 hours." GroundTruth says what those four hours actually were.**

**[See it running → whizztimmy.github.io/groundtruth](https://whizztimmy.github.io/groundtruth/)**

That link shows a sample day, and says so: the header badge reads `sample day` rather
than `live`. There is no server behind it on purpose. The sync server binds to
loopback, has no authentication, and its events carry descriptions of whatever was on
screen — so the version with real data only ever runs on the operator's own machine.
That is not a deployment gap; it is the product.

Every activity tracker ever built classifies your time by *application name*. So Chrome is
"productive" whether you were reading API docs or three hours deep in a feed. VS Code is
"coding" whether you were shipping or staring at a file you opened an hour ago. The
distinction people actually care about — focused work vs. doomscrolling, devotion vs.
idle reading — is **semantic**, and no amount of window-title metadata can recover it.

GroundTruth replaces app-name heuristics with an always-on agent that understands what you
were *doing*.

```
  Screen Time                     GroundTruth
  -----------                     -----------
  Chrome ............ 4h 12m      Scripture reading ......... 25m
  VS Code ........... 2h 03m      Deep work (auth refactor) . 1h 40m
  Instagram ......... 0h 48m      Doomscrolling ............. 2h 31m
                                  Research (reading docs) ... 1h 09m
                                  Uncertain - ask me ........ 0h 18m
                                  Idle / away ............... 0h 23m
```

That `Uncertain` row is not a defect. It is a system that declines to guess, and it is
measured: on a calibration set, genuinely contentless screens come back at 0.35 confidence
with the model naming the evidence it lacks, while unambiguous ones clear 0.88. A tracker
that silently invents a label for the hard 10% is a tracker whose other 90% you cannot
trust either.

## It reports the old metric too, and shows you where it is wrong

Hubstaff, the Upwork desktop app and Time Doctor all measure the same thing: split the
clock into ten-minute segments, count the minutes containing any keyboard or mouse input,
divide. GroundTruth implements that metric faithfully in `agent/groundtruth/activity.py`
and carries it on every event, because an argument you cannot check is worth nothing.

```
Static documentation page
[Input activity 10% over the last 10m. For comparison, an input-activity tracker
 would score this 10% and flag it as idle; the screen shows research.]
```

It fails in exactly two directions. Quiet work scores zero: reading, reviewing, a long
debugging stare, all filed as idle. Loud waste scores full marks: forty minutes pulling a
feed is continuous input and ranks among your most productive time. One keystroke in sixty
seconds scores that minute the same as sixty seconds of typing, which is why a mouse
jiggler defeats it completely and why people pay money for one.

Most of the time the two agree, and GroundTruth says nothing. The comparison only fires in
those two cases, because a tool that contradicted you constantly would be an accusation
rather than a measurement.

The deeper difference is who is asking. Activity tracking grew up as something an employer
installs on a contractor's machine, so screenshots are uploaded for somebody else to look
at and the score exists to be audited. GroundTruth answers "what did I actually do
today?", and nobody else is the audience. That is what makes the rest of the architecture
possible. See [why this is not another time tracker](docs/WHY.md), which is also honest
about what those tools still do better.

## Why this is built on open weights

The most sensitive data a person has is their own screen. A tracker that understands your
screen is only acceptable if that understanding never reaches anyone who could keep it.

So GroundTruth classifies with **Muse Spark Open** — Meta's open-weights reasoning model —
rather than a vendor-managed endpoint. Frames are perceived and discarded; only derived
labels are ever persisted or synced.

Because the weights are open, that argument holds wherever the model runs: your own GPU,
or infrastructure you rent and control. It is the same model either way, and moving it
on-device is a config change rather than a rewrite. What it never runs on is a
vendor-managed endpoint that could retain your screen.

The agent enforces this rather than promising it. Sending a frame to a `managed` backend
raises `PlacementViolation` instead of succeeding, and a backend with no declared
placement defaults to `managed` — so a misconfiguration costs a refused call, never a
silent leak. See [Placement](ARCHITECTURE.md#placement).

Two further controls follow from the same argument. Frames are reduced to 1024px before
they leave the capture module, which costs nothing in accuracy (measured — see
[validation](docs/validation.md)) and strips fine body text. And because a *description*
of a screen is not a screen, derived labels are scrubbed of credential-shaped text before
they are stored, at the single gate every persisted string passes through.

That is not a feature bolted onto a time tracker. It is the entire thesis, and a working
implementation of the argument in *The Future Is for Everyone*: superintelligence that
belongs to the individual, running where the individual decides.

## How it works

An escalation ladder. Each rung is more expensive and more revealing than the last, so
GroundTruth only climbs when the rung below is genuinely ambiguous.

| Tier | Signal | Cost | Fires when | Status |
|------|--------|------|-----------|--------|
| 1 | Foreground window, process name, and whether anybody is actually there | Free | Always | **Running** |
| 2 | Screen frame + local motion signal, classified by **Muse Spark Open** | Rented GPU | Tier 1 is ambiguous (e.g. a browser) | **Running** |
| 3 | Check-in you state yourself, interpreted by **Muse Spark** | Remote, rare | Off-screen time — the gym, a commute, a walk | **Running** (typed; audio transcription unverified) |
| 4 | Weekly synthesis via **Muse Spark** on the Meta Model API | Remote, weekly | On request | **Running** |

Photoshop is unambiguous; a browser never is. A process-name table resolves the
unambiguous majority for free, which is what makes always-on operation affordable, and an
escalation policy decides the rest — rate limit, then ambiguous process, then unusable
title, then unrecognised process, then a low-confidence streak, then periodic
re-verification.

### An open window is not an activity

The first version of Tier 1 answered "what is focused?" and stopped. A focused editor
became `deep work` at 0.9 confidence whether somebody was typing in it or had gone to
lunch, because the input signal was measured on every sample and then not consulted when
the label was written. On a machine set never to sleep, that manufactured up to five
minutes of confident work per idle stretch.

Shortening the idle timeout does not fix it. Input idleness alone cannot tell "away from
the machine" from "reading", and a tracker that files twenty minutes of reading
documentation as idle is wrong in a way you notice immediately.

So `presence.py` asks two questions instead of one. **Is the machine attended?** Only
input answers that. **Is anything happening on screen?** Churn answers that, locally and
for free. Neither alone is enough, and the pair rules out exactly the case that was being
mislabelled: focused, nothing typed, nothing moving, for minutes.

| Input | Screen | Recorded as |
|---|---|---|
| recent | either | the activity, full confidence |
| none | changing | the activity, reduced confidence (a video, a build, a feed) |
| none | static, briefly | the activity, decaying confidence (reading looks like this) |
| none | static, sustained | **`unattended`** rather than the activity |

Where the pair cannot settle it, the claim weakens rather than changes. Tier 1 confidence
decays toward the retry threshold as time since input grows, which does real work
downstream: below `ACCEPT` the escalation policy sends the sample to Tier 2 to be looked
at properly, and session confidence is duration-weighted so a long unattended stretch
pulls the session down instead of riding on one good sample.

And when no frame is captured at all, which is the normal case with Tier 2 disabled,
`unattended` is never claimed. Input alone cannot separate somebody reading from an empty
chair, so the system declines to say.

### Attention as motion, not as window focus

The unit of attention is not the window. A developer with docs on one monitor and a feed
on the other is the normal case, and every existing tracker scores that as 100% of
whichever window held focus.

A single frame also cannot separate reading from scrolling — the same page, the same URL,
the same layout. What separates them is *content replacement over time*: a feed replaces
what sits in a region while reading holds it. So Tier 2 pairs a churn measurement with
input activity and hands the classifier a two-axis prior — scrolling, playing, reading or
working — phrased as an observation rather than a verdict, so the model can overrule it
when a terminal scrolls during real work.

The classifier asked for this signal by name before it was built. Both abstentions in the
calibration run cited the same absence, unprompted:

> no motion or input data to distinguish browsing, reading, or task context

**What ships** is `perceive/framediff.py`: the frame reduced to a 64x36 grid of mean
luminances and compared against the previous sample's grid. No model, no network, nothing
leaves the machine. Only the 2,304-byte digest outlives a sample, never a second frame, so
the single-buffer capture invariant holds.

**What is written but deliberately not used** is the SAM 3.1 path. `perceive/regions.py` uses
SAM 3.1's object multiplexing (up to 16 objects per forward pass) to track named screen
regions — editor, terminal, feed, video player — so churn can be attributed to *which*
region moved, which the whole-screen measurement cannot do. It is implemented and
unit-tested against synthetic detections, and it has never processed a real frame.

That is a choice, not a shortage. `sam-3.1` **is** served on the Meta Model API, via
`/v1/responses`. But that is a vendor-`managed` endpoint, and SAM is the one component
that receives raw frames — so routing to it is exactly what the placement invariant above
exists to refuse. Using it would mean either weakening the invariant or declaring a
vendor endpoint `self_hosted`, which would be a lie in a config file. The privacy claim
costs something here, and this is what it costs.

One function, `_parse_detections`, is written against documentation rather than a live
endpoint and is contained for exactly that reason.

The honest summary: the cheap signal answers the question the model was asking. The SAM
path would answer it better, and is not currently running.

## Meta models used

**In the loop:**

| Model | Role | Placement |
|-------|------|-----------|
| **Muse Spark Open** (`muse-spark-open-1026`) | Tier 2 activity classification and session labelling | `self_hosted` — open weights on rented infrastructure |
| **Muse Spark 1.3** (Meta Model API) | Tier 4 weekly synthesis and pattern reasoning | `managed` — receives category names and durations, never a frame, title or transcript |

**Deliberately unused:** SAM 3.1, which is available on the Meta Model API but only as a
managed endpoint, and frames may not go to one. See above.

**Written, unverified:** `muse-voice-transcribe-1.0` appears in the model catalogue but was
not reachable on `/chat/completions`, `/audio/transcriptions` or `/audio/translations`.
Tier 3 therefore ships with typed check-ins, which are verified end to end; the audio
leg is one function away whenever the right surface is found.

## Platforms

| Platform | Capture method | Status |
|----------|---------------|--------|
| Windows | Tiers 1, 2 and 4 | **Running** |
| Android | `UsageStatsManager` + voice check-in | Not started |
| iOS | Shortcuts automations + Focus triggers + voice | Not started — and constrained, see below |

Nothing mobile ships in this submission. The agent, the sync server and the dashboard are
desktop only.

**On iOS,** when it is built: Apple's `DeviceActivityReport` renders inside a sandboxed
extension specifically so third-party code cannot read usage values out. There is no API,
no entitlement and no workaround. The plan works within that limit using Shortcuts
personal automations, Focus-mode boundaries and voice check-ins rather than pretending the
restriction does not exist.

## What is in this repository

| Part | Contents | Tests |
|------|----------|-------|
| `agent/` | Capture, presence, activity metering, escalation, perception, classification, sync | 296 |
| `server/` | Event ingest, idempotent on `(device_id, ts)`, session segmentation, derivation | 144 |
| `web/` | Dashboard: day timeline, the contrast view, the uncertain queue | — |
| `scripts/` | Pre-commit credential scanner | 35 |

`docs/validation.md` records what was actually tested and what it showed, including the
results that contradicted the design — a still frame being sufficient where churn was
assumed necessary, confidence turning out to be calibrated after it was assumed constant,
and a reasoning model spending its whole token budget before answering.

## Running it

```bash
cp .env.example .env
git config core.hooksPath .githooks
```

```bash
cd agent && ./.venv/Scripts/python.exe -m pytest tests/ -q
```

```bash
cd agent && ./.venv/Scripts/python.exe check_api.py
```

```bash
cd agent && TIER2_ENABLED=1 ./.venv/Scripts/python.exe -m groundtruth.main
```

```bash
cd agent && ./.venv/Scripts/python.exe -m groundtruth.checkin --text "at the gym for 45 minutes"
```

Tier 2 opts in rather than out. Leave `TIER2_ENABLED` unset and the agent captures no
frames at all — a misconfigured agent should see less than intended, never more.

---

## Hackathon disclosure

Built for the **Meta Global AI Developer Hackathon** (Oct 26 – Nov 4, 2026), Indie
Developer track.

**100% of the code in this repository was written during the hackathon window.** The
repository was created on Oct 28, 2026 and has no prior history, no pre-existing codebase
and no vendored work from earlier projects. Every commit is dated within the event period
and the full history is public.

## License

MIT — see [LICENSE](LICENSE).
