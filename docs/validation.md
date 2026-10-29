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
