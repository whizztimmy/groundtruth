# Architecture

## Design constraints

These drove every decision below. They are listed first because most of the design is a
consequence of them.

1. **Screen content must never reach a party who can retain it.** This rules out a
   vendor-managed multimodal API for the hot path. It does not rule out open weights
   running on infrastructure the operator controls — see *Placement* below, where this
   distinction is drawn precisely and enforced in code.
2. **Always-on means cost-per-hour, not cost-per-call.** A classifier that runs every few
   seconds for sixteen hours a day cannot cost a cent per invocation.
3. **The ground truth is unobservable.** Nobody logs what they were "really" doing, so
   there is no labelled dataset and no supervised baseline. The system must be
   self-verifying rather than accuracy-benchmarked.
4. **Off-screen time is half of life.** A tracker that only sees the screen structurally
   cannot see the gym, devotion, or a conversation.

## The capture loop

```
sample --> tier 1 --> ambiguous? --no--> emit event
              |           |                  |
              |          yes                 |
              |           v                  |
              |       tier 2                 |
              |   SAM 3.1 regions            |
              |           |                  |
              |           v                  |
              |   Muse Glimmer               |
              |   classify + confidence      |
              |           |                  |
              |      confident? --no--> retry (bounded)
              |           |                  |
              |          yes                 |
              |           +------------------+
              |                              v
              +------------------------> segmenter --> sessions --> sync
                                              ^
voice check-in ------- tier 3 ----------------+
```

## Escalation policy

Tier 2 is the expensive tier, so the decision to climb is the most important rule in the
system. A sample escalates when **any** of these hold:

- The foreground process is in the *ambiguous set* — browsers, chat clients, file managers,
  terminals, anything that can host arbitrary activity.
- The window title changed but the process did not, and the new title is not a known title.
- More than `STALE_AFTER` has elapsed since the last Tier 2 classification for this process,
  so long sessions re-verify rather than drifting on a stale label.
- Tier 1 produced a label whose historical confidence for this process is below threshold.

Everything else resolves at Tier 1 for free. In practice a normal day is roughly 70% Tier 1,
which is what makes always-on viable on consumer hardware.

## SAM 3.1 as an attention sensor

Tier 2 treats the desktop as a video scene rather than a still image.

SAM 3.1's object multiplexing tracks up to 16 objects in a single forward pass, which maps
cleanly onto screen regions: editor pane, terminal, browser viewport, video player, chat
panel, notification surface. Tracking those regions *across frames* yields signals that no
single screenshot contains:

- **Region persistence** — how long a region stayed on screen.
- **Region churn** — a feed scrolling produces high mask instability in a fixed bounding
  area; reading produces low churn. Only visible in motion, never in a single frame.
- **Attention locus** — which region co-occurs with input events.

Churn covers what a still frame cannot reach. Testing against a real endpoint
([validation log](validation.md)) showed that a single screenshot is often *enough* — a
social feed and a long-form article were separated correctly from one frame each, by
layout and content rather than by domain. So churn is not the primary discriminator it
was first assumed to be.

Its role is the residue, and the residue is where trackers fail: the same page read
versus scrolled, a feed and an article on one domain, a terminal scrolling its own
output. Those cases are invisible to any single frame, which is why this remains a video
problem even though most samples resolve without motion.

### Churn is half the signal

Churn says *that* the screen changed. It can never say *who* changed it — and a fixture
day caught the consequence: a long-running build scrolls its own output continuously with
nobody at the keyboard, producing exactly the instability that defines a social feed.
Churn alone would file a compiling project as doomscrolling.

Input rate answers the second question, and costs nothing: `GetLastInputInfo` is already
sampled for idle detection. Pairing them gives four quadrants rather than one axis:

|                     | little input | steady input |
|---------------------|--------------|--------------|
| **churning screen** | `playing` — build log, video, slideshow | `scrolling` — a feed being pulled past |
| **static screen**   | `reading` — held content | `working` — editing, composing |

The diagonal is the point. `scrolling` and `playing` are indistinguishable to churn;
`reading` and `working` are indistinguishable to input rate. Neither signal alone
separates all four, which is why `perceive/churn.py` only measures and
`perceive/motion.py` interprets.

Motion is a **prior handed to the classifier, never a label shown to a user** — a terminal
can scroll and still be work. The evidence strings describe what was observed and contain
no verdict, so the model weighs the observation rather than being steered by it. A run
that is thin or sits between the bands returns `unclear` and escalates.

## Verification rather than accuracy

Because constraint 3 means there is no ground-truth label set, the system cannot be
validated by accuracy alone. Instead the classifier is asked to commit to a prediction it
can be held to:

- Glimmer returns a label **and** a confidence **and** the evidence it used.
- Low-confidence classifications are retried with escalated reasoning effort before being
  accepted. Glimmer is trained to diagnose and retry on failure rather than halt, which is
  the behaviour this loop depends on.
- Classifications that remain uncertain are stored as `uncertain` rather than guessed, and
  surface in the UI as a question the user can answer in one tap. User answers become
  few-shot examples for that process, so the system converges on the individual.

This is the difference between an agent that acts and an agent that *verifies it acted
correctly*, applied to perception rather than to tool calls.

## Placement

"Local" is the goal, but stating it as an absolute would be dishonest about how this
actually runs, so the architecture names three placements and enforces the distinction
between them:

| Placement | Meaning | May see pixels |
|-----------|---------|----------------|
| `local` | On the user's machine | Yes |
| `self_hosted` | Open weights on infrastructure the operator controls and can tear down | Yes |
| `managed` | A vendor-managed endpoint | **No** |

The meaningful line is not on-device versus off-device — it is whether the operator
controls the weights and the machine. Glimmer is Apache-2.0, so a `self_hosted`
deployment is the *same model* the user could run on their own GPU; moving it on-device is
a config change, not a port. A `managed` endpoint is a different proposition: the operator
controls neither retention nor what the content is subsequently used for.

That last point is not hypothetical. Meta's own API states that while free credits are in
use, "your content may be used for product improvement" — which is entirely reasonable for
text, and exactly why frames never go there.

Enforcement lives in `agent/groundtruth/backends.py`. `vision()` raises
`PlacementViolation` rather than sending a frame to a `managed` backend, and an unset
placement defaults to `managed`, so forgetting to configure one costs a refused call
instead of a silent leak. A privacy guarantee that depends on every future caller
remembering it is not a guarantee.

## Privacy model

What is held, and for how long:

| Data | Lifetime | Leaves device |
|------|----------|---------------|
| Raw screen frames | In memory, discarded after classification | Never |
| Downscaled frame (1024px) | In memory, for the duration of one inference | Only to a `local` or `self_hosted` backend |
| SAM 3.1 masks | In memory, discarded after feature extraction | Never |
| Derived region features (churn, persistence) | Discarded after classification | Never |
| Activity label + confidence + timestamps | Persisted | Only on explicit sync |
| Voice audio | Discarded after transcription | Never |

No frame is ever written to disk. This is enforced at the capture boundary rather than by
convention, and `test_capture_writes_nothing_to_disk` checks it.

Three further controls sit in `capture/screen.py`:

**Tier 2 opts in, never out.** Unset `TIER2_ENABLED` and the agent captures no frames at
all, running Tier 1 only. A misconfigured agent should see less than intended, never more.
The grabber is not even constructed unless a backend is allowed to look at pixels —
capturing a screen that nothing will examine is precisely the thing this project exists
not to do.

**Only the active display.** A second monitor is captured only when the foreground window
is on it. Grabbing every screen is easier and doubles the exposure for no gain.

**Downscaled to 1024px before leaving the module.** This is a privacy control as much as a
cost one. At that width the *structure* of a screen survives — pane layout, where a feed
sits, whether a video is playing — which is what SAM 3.1 segments and what the motion
model reads. Fine body text largely does not, which is the point: the classifier should be
reading the shape of a screen, not someone's correspondence.

## Data model

```
Event      (raw observation)
  ts, device_id, tier, process, window_title,
  regions[], label, confidence, evidence

Session    (derived span)
  start_ts, end_ts, device_id, category, label,
  confidence, event_count, evidence_summary

Category   (user-owned taxonomy)
  name, parent, examples[]
```

Sessions are derived from events by a segmenter that merges adjacent same-label events and
closes a session on label change, idle timeout, or device handoff. Events are retained so
segmentation can be recomputed when the taxonomy changes — the user renaming or splitting a
category must not require re-observing the day.

## Sync

Devices are **offline-first and authoritative for their own events**. Each device owns its
event stream and never edits another device's. This makes sync an append-only merge with no
write conflicts by construction — the hard part of multi-device sync is avoided rather than
solved, which is the correct trade for a system where every event is immutably timestamped.

Cross-device session stitching (a phone pickup during a desktop session) happens at read
time in the query layer, not at write time.

## Cost model

| Tier | Where it runs | Marginal cost |
|------|--------------|---------------|
| 1 | Local, no model | 0 |
| 2 | Muse Glimmer, local GPU or partner inference | GPU-seconds only |
| 3 | Muse Voice Transcribe | Per check-in, user-initiated |
| Weekly synthesis | Muse Spark / Wonder | Once per week |

Glimmer at 4-bit fits under ~20GB and targets a single consumer GPU. On machines without
one, the same model is served through partner inference behind an identical interface, so
the deployment target is a config change rather than a code path.

## Deliberately out of scope

Stated so the boundary is explicit rather than discovered:

- No productivity scoring. The system reports what happened; it does not grade it.
- No blocking or intervention in v1. Measurement must be trustworthy before it earns the
  right to act.
- No `managed` screen understanding, ever, on any tier. Frames go to `local` or
  `self_hosted` placements or nowhere.
