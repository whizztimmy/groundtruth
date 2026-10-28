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
