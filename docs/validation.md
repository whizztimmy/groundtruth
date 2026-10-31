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

> **Corrected 2026-10-31.** This section is wrong, and wrong in a way that mattered. The
> 0.1 figure came from comparing a capture-resolution run against a downscaled one —
> *different inputs* — so it never measured repeat variance at all. Measured properly, it
> is 0.53 on an ambiguous image and the category flips. See the entry at the end of this
> log. The consequences drawn below happen to survive; the number does not.

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

## 2026-10-31 — Tier 4 against Meta's own endpoint

`META_API_KEY` in place, so Tier 4 now runs on `muse-spark-1.3` at `api.meta.ai/v1` with
`managed` placement. `check_api.py` settled the transport in one pass: `/chat/completions`
works, inline base64 images work, and the review path returns usable JSON. The playground
generates `/responses` and an upload step for images; neither is required, so `backends.py`
needed no change for Meta any more than it did for Baseten.

### A harness, because a review of real data cannot be marked

If the model says deep work is fragmented, there is no way to know whether that is insight
or noise unless the answer is already known. `review_week.py` builds a week with three
patterns planted on purpose — a deep-work/doomscrolling trade-off across days, deep work
fragmented into 8 stretches averaging 48m, and weekdays opening with deep work while
weekends open with devotion — and takes a backend prefix, so the same input can go through
two models.

### The "restating one figure" rule had a loophole

Five runs on identical input. The trade-off was found in all five. Two runs also produced
this shape:

> The period fitness total matches the Saturday fitness amount
> (fitness: 1h; Saturday: fitness 1h)

Which satisfies every rule in the prompt. It cites figures, it computes nothing, it
relates two numbers rather than restating one — and it says nothing at all, because
fitness happened on exactly one day, so those two figures are the same fact wearing a
comparison's clothes.

The prompt now names that: *two figures that cannot differ are one figure*, with the test
being whether they could have come out differently from each other.

**Three runs after the change.** The tautologies are gone. Two runs were clean — the
trade-off plus either the fragmentation or the stretch-length comparison. The third
produced a different fault instead:

> Days begin with implementing the sync client, writing the classifier, ... while
> doomscrolling totals 7h 50m (41%)

Two unrelated figures bolted together. Pushing the model off tautology moved it toward
spurious relation, which is the harder failure to catch because it reads as analysis. Net
better — a vacuous observation wastes attention, a spurious one misleads — but not solved.

### Tier 4 output varies more than Tier 2 does

Five runs, identical input, and the set of observations differed every time. The 2026-10-29
entry noted ~0.1 confidence drift on Tier 2; at Tier 4 the same non-determinism shows up as
*which* patterns surface rather than as a number moving, because the model picks two or
three from a larger set of true things.

Consequence: a weekly review is not reproducible, and should not be presented as though it
were. The planted trade-off appeared in 5/5 runs and is the signal; anything appearing in
1/5 is a coin toss. If a pattern matters, it should be computed in `patterns.py` and handed
to the model, not left for the model to notice.

### Open, from the Fireworks workshop

The hackathon's Fireworks session published an effort sweep on short-answer text benchmarks
where Muse scores *worse* at high effort than at low. `perceive/classify.py` escalates
`EFFORT_LADDER` on low confidence, assuming more effort helps. Those benchmarks are text,
not screen classification, so this does not transfer — but it does mean the ladder is an
assumption this project has never tested, and `probe.py` can test it.

## 2026-10-31 — No SAM host, a stub running in production, and churn that argues against itself

### SAM 3.1 is not reachable, so churn is measured locally

> **Corrected 2026-10-31, later the same day.** "Not reachable" was wrong, and the
> mistake was mine: I listed what the *Baseten* key served and never asked Meta's
> own endpoint. `sam-3.1` is there. It is still not used, for a better reason —
> see the entry at the end of this log. The frame differ stands on its own merits.

Listing the models the Baseten key can reach returned 21, every one of them text —
DeepSeek, GLM, Kimi, Nemotron, `muse-spark-open`. No segmentation model of any kind.
Baseten's Model APIs are text inference; SAM would need a dedicated deployment on an
account this project administers, and there is no local GPU to serve it on instead.

So `perceive/framediff.py` measures the same underlying quantity without a model: the
frame reduced to a 64x36 grid of mean luminances, compared with the previous sample's
grid. Only the 2,304-byte digest outlives a sample, so the single-buffer capture invariant
holds and nothing derived from pixels leaves the machine.

Writing the tests corrected the code. `CELL_DELTA` was documented as the thing that stops
a blinking caret counting as churn. It is not — a 2x18 caret lights its own cell
comfortably. The protection is the *ratio*: one or two cells out of 2,304 sits three
orders of magnitude below `STATIC`. What `CELL_DELTA` actually prevents is a global
luminance step — a backlight change or a theme animation — reading as 100% churn on an
untouched screen. The test now pins that behaviour instead of the comment.

### The agent had been running a stub

The first live run after wiring returned `shallow work | 0.50` for every sample, Tier 1
and Tier 2 alike. The stored evidence said why in its own words:

> Browser in foreground; contents unexamined. (stub classifier — not a real observation)

**`main.py` never loaded `.env`.** `probe.py` and `check_api.py` each carried their own
loader, so both diagnostics saw a configured backend while the product saw nothing:
launched the way `README.md` documents, `Backend.from_env` raised and the loop fell back
to `StubClassifier` for the whole session.

This is the second fault of the same shape. The first was startup coupling `SAM_ENDPOINT`
to classifier construction, which discarded a working classifier and reverted the agent to
app-name labelling. Both are degradation paths that keep the agent *running* while it
stops doing the one thing it exists to do, and both were invisible from the outside
because the stub is well-behaved.

There is now one loader in `config.py`, called by the agent, with both scripts deferring
to it. A tool that reports on configuration must not be configured differently from the
thing it reports on.

### The motion signal reaches the model, and sometimes argues against the frame

Five Tier 2 classifications on a real desktop after the fix, confidence 0.35 to 0.88. Both
bands populated in a *live* run for the first time — previously every live sample came
back confident.

Three of five evidence strings cite the motion measurement unprompted, so the prompt wiring
works:

> telemetry shows content held still with almost no input indicating reading/reviewing
> rather than active typing

That one became `deep work` at 0.82, where the frame alone had been ambiguous. The signal
helped.

**Then this one, which matters more:**

> Screenshot shows a static Mercor contracts page but telemetry reports continuous content
> replacement with almost no input and whole-screen measurement cannot localize the
> movement to a specific region

Confidence 0.35. The frame showed a static page; whole-screen churn said the screen was
being replaced continuously. Something else on the display was moving, the measurement
could not say what, and the model declined to resolve the contradiction rather than
guessing.

**So the fallback converted an answerable sample into an abstention.** That is the exact
weakness predicted when it was written, now observed rather than asserted, and it is the
strongest available argument for the SAM path: per-region churn would have attributed the
movement to the region that moved and left the contracts page reading as static. The model
even named the deficiency — *"cannot localize the movement to a specific region"* — which
is the caveat line the prompt supplies, used as intended.

A third sample reported no measurements at all: middling churn resolves to `UNCLEAR` in
`motion.classify`, which emits no evidence line. Honest, and it means some samples are
still no better off than before.

**Net:** whole-screen churn is worth having and is not a substitute. It helps where it
agrees with the frame, abstains where it conflicts, and says nothing in the middle band.
The claim that it "converts abstentions into answers" is not supported — it also creates
them.

### Noted in passing

One evidence string captured `work.mercor.com/earnings with $10.77 total earnings`. That is
the documented limit of `redact.py` working as specified: it scrubs credentials, not
personal data generally. The limit is pinned by a test, and this is what it looks like in
practice on a real screen.

## 2026-10-31 — How much does an identical input move on its own?

The effort sweep raised a question it could not answer. Seven images through
`EFFORT_LADDER`, one reading per cell, reported confidence rising on two and falling on
three, with two categories flipping — which reads like evidence that escalating reasoning
effort hurts. But the low-effort readings also disagreed with the 2026-10-29 table on the
same images at the same effort, by far more than the 0.1 that entry claimed as the drift
floor. Either something had changed, or the floor was wrong.

**The floor was wrong.** Five repeats of one identical image, same effort, same backend,
same width:

| | a-x-feed (ambiguous) | b-substack (unambiguous) |
|---|---|---|
| run 1 | doomscrolling 0.62 | research 0.82 |
| run 2 | uncertain 0.45 | research 0.88 |
| run 3 | uncertain 0.45 | research 0.82 |
| run 4 | doomscrolling 0.88 | research 0.88 |
| run 5 | uncertain 0.35 | research 0.82 |
| **spread** | **0.53** | **0.06** |

### Variance is not a constant; it concentrates where the system is already unsure

This is the finding, and it is more useful than a drift number. The unambiguous article is
near-deterministic — 0.06 across five runs, category never in doubt. The ambiguous feed
moves 0.53 and **flips category** between `doomscrolling` and `uncertain` on identical
pixels.

The evidence strings show the mechanism. On three of five runs the model demanded the
motion signal:

> no multi-second input or content-replacement measurements provided to confirm active
> scrolling versus idle viewing

On the two runs where it answered confidently it did not ask. Same image. So *whether the
model decides it needs more evidence* is itself non-deterministic, and that decision is
what moves the number.

### What this retracts

**The effort sweep proves nothing.** One reading per cell against per-cell noise of 0.53
is coin-flipping. "Escalating made answers less confident more often than more confident"
was not a finding, and neither were the two category flips it reported — those are inside
the noise on exactly the kind of image that produced them. `EFFORT_LADDER` remains
untested. Answering it properly needs repeats per cell, and the cheap version of that
question is whether escalation reduces *variance* rather than whether it raises the mean.

`probe.py` now reports within-image spread separately whenever `--repeat` is above one,
and says that across-image comparisons are only meaningful where they exceed it. The tool
previously pooled repeats into a single spread and concluded "the signal varies, so
thresholds are meaningful" — which is true of the across-image comparison and false of
the within-image one, from the same ten numbers.

### What this vindicates

Two decisions made for other reasons turn out to be load-bearing:

- **Labels carry forward between classifications** rather than being recomputed every
  sample. Without it, a stable activity would flip category every few samples.
- **Session confidence is a duration-weighted mean**, not a last-value. A single 0.35
  reading inside a confident block cannot drag the session.

And one consequence for the product: `ACCEPT = 0.75` is not a threshold an ambiguous
sample sits above or below, it is a threshold such a sample *crosses repeatedly*. The
retry in `classify_verified` is therefore partly resampling noise rather than thinking
harder — which may be the real reason it appears to work.

**Open:** whether the uncertain queue should require a label to abstain twice before
asking the user. On these numbers a single abstention is weak evidence of ambiguity, and
asking a person about a sample that would have answered itself on the next poll is the
most expensive mistake this system can make.

## 2026-10-31 — Tier 3, and what the model catalogues actually serve

### Ask the endpoint, not the documentation

Listing `/models` on both endpoints took one request each and settled several questions
that guesswork had got wrong.

`inference.baseten.co/v1` serves 21 models, all text. No segmentation, no audio.

`api.meta.ai/v1` serves nine: `muse-spark-1.1/1.2/1.3`, two `-contributor` variants,
`muse-wonder-1.0`, `muse-image-1.0`, `muse-voice-transcribe-1.0`, and **`sam-3.1`**.

### I was wrong about SAM, and the right answer is better

The earlier entry said no SAM endpoint was reachable. I had checked Baseten and not Meta.
`sam-3.1` is served, and a `/chat/completions` call says exactly where:

> The model `sam-3.1` is not available on this endpoint. Use /v1/responses.

So the SAM path could be wired. **It is still not going to be, and the reason is the
architecture rather than the logistics.** `api.meta.ai` is a vendor-`managed` endpoint,
and SAM is the one component that receives raw frames. Routing frames there is precisely
what `PlacementViolation` exists to prevent. The only ways to do it are to weaken the
invariant or to declare a vendor endpoint `self_hosted`, which would be a lie written
into a config file.

This is a better position than the one it replaces. "I could not find a host" is an
excuse; "the host exists and the design refuses it" is the privacy claim actually costing
something. The frame differ is no longer a fallback — it is the implementation that fits.

### The voice model is in the catalogue and on none of the obvious routes

`muse-voice-transcribe-1.0` exists in `/models`. Three attempts to reach it:

| Attempt | Result |
|---|---|
| `/chat/completions` with an `input_audio` part | 404 `model_not_found` |
| `/audio/transcriptions`, multipart | 404, empty body |
| `/audio/translations`, multipart | 404, empty body |

An empty 404 is a missing route; the chat endpoint's structured error is a route that
exists and declined the model. Given `sam-3.1` turned out to live on `/v1/responses`,
that is where to look next. Until then Tier 3 ships with typed check-ins, verified end to
end, and `transcribe()` records what was tried in its own docstring.

### Two bugs the live runs found, both mine

**A category named after a venue.** The first taxonomy had `gym`, and `interpret` then
refused "I went for a run" and "I was on a walk" — they had nowhere to go. A gym is a
place; the category had to be the activity. Renamed to `fitness`, including in the fixture
and its assertions, because one concept under two names is how a timeline becomes
unreadable.

**An inference path that never fired.** The prompt asked the model to estimate a duration
when none was stated and record it at reduced confidence. Across five phrasings it never
estimated once — it returned 0 every time and declined. That is better behaviour than the
design asked for, so `INFERRED_CONFIDENCE` is gone and an unstated duration is now an
explicit refusal with something to say back. A guessed number on somebody's timeline is
one they will plan around.

### What Tier 3 does

Verified against `muse-spark-1.3`:

| Check-in | Result |
|---|---|
| "at the gym for 45 minutes, lower body" | `fitness` / lower body at the gym, 45m, 0.97 |
| "I went for a run, about 30 minutes" | `fitness` / run, 30m, 0.97 |
| "reviewing a paper contract for about an hour" | `admin` / reviewing a paper contract, 60m |
| "I just got back from the gym" | refused — no duration stated |
| "twenty minutes reading API docs in Chrome" | refused — not off-screen time |
| "ugh whatever" | refused — nothing in the taxonomy |

The screen-work refusal is the one worth noting: that time is observed directly and does
not need stating, and a tier that accepted it would double-count the day.

**The agent never opens a microphone.** There is no capture path in the codebase, so Tier
3 only runs when a person invokes it. That is a stronger control than a runtime check — a
guard that permits background audio under conditions is one somebody eventually widens.
