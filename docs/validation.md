# Validation log

What was actually tested, and what it showed. Recorded as it happened, including the
results that contradicted an assumption, because a design document that only records the
beliefs that survived is not evidence of anything.

## 2026-10-28 — Can a single frame separate reading from scrolling?

**Why it mattered.** The architecture rests on the claim that an application name is a
poor proxy for an activity, and that screen *content* is a better one. If a still frame is
enough, Tier 2 is simpler than designed. If it is not, churn is load-bearing.

**Method.** Two full-resolution screenshots, both in Chrome, sent to `muse-spark-1.3`
through the Meta Model API playground in **separate chats**, so the model could not
compare them. Separation matters: the agent classifies one frame at a time and never gets
a contrast. Reasoning effort medium, the production classifier prompt verbatim.

### A — x.com, "For you" feed

```json
{"category": "doomscrolling",
 "label": "scrolling a social feed",
 "confidence": 0.9,
 "evidence": "x.com/home For you feed visible with timeline posts from Dr. APK and
              Falshi, engagement counts, and right sidebar showing Today's News and
              What's happening trending topics"}
```

### B — substack.com, a long-form article

```json
{"category": "research",
 "label": "reading article on stablecoin revenue defensibility",
 "confidence": 0.9,
 "evidence": "Substack page open to 'THE MOAT MATRIX: A Framework for Measuring
              Stablecoin Revenue Defensibility' with paragraphs about evaluating
              stablecoin moats"}
```

### What this showed

**Both correct, and not by domain matching.** The first reading of result A was that the
model had simply recognised `x.com` — which a URL blocklist could do without any model at
all. Result B rules that out: `substack.com` serves both a scrollable feed and long-form
articles, so the domain carries no signal, and the model cited the article's title and the
subject of its paragraphs. It read the page.

Result A's evidence also went beyond the URL — "timeline posts, engagement counts, right
sidebar trending" are **layout** observations. This is the encouraging part, because
layout is what survives the 1024px reduction in `capture/screen.py` while fine body text
does not.

**So a still frame is sufficient for these two cases.** Churn is not doing the work here.

### What it did not show

Three things remain open, and the design should not claim otherwise:

1. **Same-site, different-intent is untested.** X search results for a technical question
   versus the For You feed; a YouTube tutorial versus Shorts. These are the cases where
   both URL and page structure are identical and only behaviour differs — the cases churn
   exists for.
2. **Downscaling is untested.** Both screenshots were full resolution. Production frames
   are reduced to 1024px wide. Layout should survive; the Substack body text may not.
3. **Confidence may not be calibrated.** Both answers returned exactly `0.9`. Two samples
   prove nothing, but if the model returns 0.9 regardless, the `uncertain` path never
   fires — and honest abstention is a differentiating feature of this product, not a
   nicety. Worth probing with a deliberately ambiguous frame.

### Consequence for the design

Churn and the motion signal are **not** redundant, but their role is narrower than first
assumed: they cover the cases a still frame cannot reach — the same page being read versus
scrolled, a terminal scrolling its own output, a feed and an article on one domain — rather
than being the primary discriminator. The tiered escalation already reflects this. The
claim in `ARCHITECTURE.md` has been narrowed to match.

## 2026-10-29 — First real inference, via Baseten

**Setup.** Muse Spark Open (`meta-models/muse-spark-open-1026`) served by Baseten at
`inference.baseten.co/v1`. Placement `self_hosted`: open weights on infrastructure the
operator controls.

### Transport

- **`/chat/completions` works.** Baseten's OpenAI-compatible surface is the one
  `backends.py` already spoke, so Tier 2 needed configuration and no code.
- **Inline base64 images work.** No file upload step is required, despite the Meta
  playground's generated code uploading for a `file_id` first. `vision()` was right.

### Muse Spark Open reasons before it answers

The first call failed with `unexpected content type: NoneType` — an error pointing at the
parser rather than the cause. The model spends its token budget thinking, so a tight
`max_tokens` returns null content and `finish_reason: "length"`.

This is not a tuning detail. The classifier shipped with `max_tokens=400`, which on a
complex screen would have failed as an *uncertain span* rather than a visible error —
producing a mysteriously useless classifier instead of an obvious fault.

The budget a reasoning model needs scales with input size, which a caller cannot know in
advance, so `chat` now detects truncation-before-answer and retries once with a doubled
budget up to a ceiling. A self-describing failure deserves a mechanical fix rather than a
magic number.

### The review prompt was too strict to say anything

With the transport working, Tier 4 produced this:

> Your period total is recorded as 4h 31m across 3 sessions.
> For doomscrolling, the recorded time is 2h 31m (56%).

Every rule satisfied, every figure cited, and completely worthless. The constraints
written to prevent confabulation — *every number is given to you, cite the figure it rests
on* — left restatement as the only safe move.

The fix was to name the failure: restating one figure is not an observation, and an
observation must relate figures to each other or to what one would expect. The prompt now
lists the shapes that qualify, and says that returning nothing is correct for thin data.

**Verified both directions.** On three spans in one day it now returns *"nothing notable
in this period"*, which is the honest answer. On a synthetic week containing a deliberately
planted pattern it found it:

> Deep work and doomscrolling trade off across days — higher doomscrolling coincides with
> lower deep work. (Thursday doomscrolling 2h with deep work 28m ... compared to Monday
> deep work 1h 50m)

It also found the fragmentation — deep work totalling 6h 26m with a 48m average stretch —
and that weekdays began with deep work while weekends began with devotion. None of those
are restatements, and all cite figures that were computed rather than generated.

**Open:** whether a real week of the user's own data yields observations this clean, and
whether confidence calibration on Tier 2 varies at all. `probe.py` answers the second.

## 2026-10-29 — Calibration, downscaling, and threshold stability

Nine screenshots through the production classifier prompt on Muse Spark Open, at capture
resolution and again reduced to `TARGET_WIDTH`. Two unambiguous full screens, three
cluttered application screens, four small UI crops with almost no context.

### Confidence is calibrated

| | at capture | at 1024px |
|---|---|---|
| x.com feed | doomscrolling 0.88 | doomscrolling 0.88 |
| Substack article | research 0.88 | research 0.88 |
| billing page | admin 0.92 | admin 0.92 |
| API playground | deep work 0.68 | deep work 0.70 |
| model dropdown | deep work 0.68 | research 0.62 |
| text fragment | **uncertain 0.35** | **uncertain 0.35** |
| checkbox crop | admin 0.88 | admin 0.85 |
| radio crop | admin 0.72 | admin 0.62 |
| radio crop 2 | **uncertain 0.35** | **uncertain 0.20** |

Five to eight distinct values, spread 0.57 to 0.72. The earlier worry — that both
playground answers returning exactly 0.9 meant a constant — was wrong. All three bands
the architecture depends on are populated: clear cases clear `ACCEPT` (0.75), ambiguous
ones land in the retry band, and genuinely contentless crops abstain.

**The uncertain queue will fill and the retry ladder will climb.** Neither was previously
demonstrated.

### The model asked for the motion signal by name

Both abstentions cited the same missing evidence, unprompted:

> no motion or input data to distinguish browsing, reading, or task context

> no input or content-replacement measurements to determine ongoing activity

The prompt never mentions churn or input rate. The model identified the absence of
exactly the two signals `perceive/motion.py` computes, as its reason for refusing to
guess. That is independent support for the two-axis design — and it means supplying those
measurements should convert some abstentions into confident answers rather than merely
decorating them.

### Downscaling to 1024px is safe

Body text survives: the Substack run still cited "stablecoin demand and moats", and the
billing run still read "$0.00, free credits $150.00, Visa". No classification changed
category because of the reduction. `TARGET_WIDTH` stands, and with it the argument that
the reduction is a privacy control that costs nothing in accuracy.

### Confidence drifts about 0.1 between identical runs

The four crops are smaller than `TARGET_WIDTH`, so `--downscale` left them byte-identical
— yet the radio crop moved 0.72 to 0.62 and the second moved 0.35 to 0.20, at temperature
0. Reasoning models are not deterministic even with sampling disabled.

**Consequence.** A sample sitting near a threshold will flip between runs, so the system
must not treat a single reading as authoritative. Two existing decisions turn out to
matter more than they did when they were made: labels carry forward between
classifications rather than being recomputed every sample, and session confidence is a
duration-weighted mean rather than a last-value. Both damp this drift. Thresholds should
not be tuned more finely than 0.1, because that is below the noise floor.

## 2026-10-29 — First live run on a real screen

Agent running with `TIER2_ENABLED=1` against Baseten, 92 seconds, classifying the
operator's actual desktop. Nine events: four Tier 2 classifications, five Tier 1
carry-forwards, all buffered.

Region tracking was unavailable — no SAM host yet — so this ran with no churn evidence at
all, on single frames.

```
05:02:38 | tier 2 | claude.exe | deep work | 0.87
05:03:18 | tier 2 | claude.exe | deep work | 0.87
05:03:39 | tier 2 | claude.exe | deep work | 0.88
05:03:51 | tier 1 | claude.exe | deep work | 0.88   <- carried forward
05:03:59 | tier 2 | claude.exe | deep work | 0.88
```

### The labels

> debugging and validating an AI screen-agent for a hackathon

> developing and debugging an AI agent for a hackathon

> building and testing an AI agent for hackathon

With evidence:

> screen shows Claude reviewing validation.md, calibration results, and a SAM_ENDPOINT
> wiring fix with a 'Run it live against Baseten' workflow for the GroundTruth dashboard
> project

This is the claim in `README.md`, met. A conventional tracker would have recorded
`claude.exe, 92 seconds` — or at best "coding". What it actually recorded was the
project, the phase of work within it, and the files on screen. The distinction between
"an editor was focused" and "validating a screen-agent before a deadline" is the entire
product.

Four independent classifications over ninety seconds produced four consistent labels at
0.87–0.88, which also bears on the drift noted above: the variance is real but small
enough that a category does not flip within a stable activity.

### Notes

- The rate limit held: four Tier 2 calls in 92 seconds, with Tier 1 carrying the label
  between them. The timeline has no gaps.
- Startup previously coupled classifier and tracker construction, so an unset
  `SAM_ENDPOINT` discarded a working classifier and silently reverted the agent to
  app-name labelling — the exact failure the product exists to correct, hidden inside the
  product. They are now constructed independently.
- Still open: whether churn evidence improves these labels or merely decorates them. That
  needs a SAM host, and the abstentions recorded earlier suggest it will matter most on
  the samples that currently decline to answer.
