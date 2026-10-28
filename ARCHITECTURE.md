# Architecture

## Design constraints

These drove every decision below. They are listed first because most of the design is a
consequence of them.

1. **Screen content must never leave the device.** This rules out a remote multimodal API
   for the hot path and forces local inference for anything that sees pixels.
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
  area; reading produces low churn. This is the strongest single discriminator between
  reading and scrolling, and it is only visible in motion.
- **Attention locus** — which region co-occurs with input events.

Churn-over-time is the measurement that makes "doomscrolling" and "reading" separable, and
it is why this is a video problem rather than a screenshot-classification problem.

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

## Privacy model

What is held, and for how long:

| Data | Lifetime | Leaves device |
|------|----------|---------------|
| Raw screen frames | In memory, discarded after classification | Never |
| SAM 3.1 masks | In memory, discarded after feature extraction | Never |
| Derived region features (churn, persistence) | Discarded after classification | Never |
| Activity label + confidence + timestamps | Persisted | Only on explicit sync |
| Voice audio | Discarded after transcription | Never |

No frame is ever written to disk. The agent holds a single frame buffer and overwrites it.
This is enforced at the capture boundary rather than by convention.

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
- No cloud-hosted screen understanding, ever, on any tier.
