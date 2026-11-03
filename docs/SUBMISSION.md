# Submission copy

Paste-ready. Every figure here is one the repository can be checked against — which is
the point, since the whole project is an argument about not asserting what you have not
measured.

**Track:** Indie Developer

**Links**

| | |
|---|---|
| Repository | https://github.com/whizztimmy/groundtruth |
| Live demo | https://whizztimmy.github.io/groundtruth/ |
| Video | *(paste after upload)* |

---

## Tagline (one line)

> Your screen time says "Chrome: 4 hours." GroundTruth says what those four hours
> actually were.

---

## Short description (~50 words)

> Every activity tracker classifies time by application name, so Chrome is "productive"
> whether you were reading API docs or deep in a feed. GroundTruth reads the screen
> instead and names the activity — using open-weights Meta models, with the privacy
> boundary enforced in code rather than promised.

---

## Full description

> **The problem.** Every tracker ever built classifies your time by application name. The
> distinction people actually care about — focused work versus doomscrolling, scripture
> reading versus idle browsing — is semantic, and no amount of window-title metadata
> recovers it. Chrome for four hours is a true fact that tells you nothing.
>
> **What it does.** An always-on agent watches the foreground window and escalates only
> when it has to. A process name resolves the unambiguous majority for free. When it
> cannot — a browser never can — the agent captures a frame, reduces it to 1024px,
> measures how much of the screen changed since the last sample, and asks an
> open-weights model what the person is doing. The answer carries a confidence and the
> evidence it rests on. Off-screen hours are stated by the user and recorded at the
> confidence a stated fact deserves. Once a week a reasoning model looks at the derived
> durations and reports what it notices.
>
> **Why open weights are the architecture, not a detail.** The most sensitive data a
> person has is their own screen. A tracker that understands your screen is only
> acceptable if that understanding never reaches anyone who could keep it. So
> classification runs on Muse Spark Open, on infrastructure the operator controls, and
> sending a frame to a vendor-managed endpoint raises `PlacementViolation` rather than
> succeeding. A backend with no declared placement defaults to the most restrictive one,
> so a misconfiguration costs a refused call instead of a silent leak.
>
> That rule cost something real. SAM 3.1 would give better attention tracking than what
> ships, and it is available on the Meta Model API — but only as a managed endpoint, and
> frames are the one thing that may not go to one. It is implemented, tested, and
> deliberately unused. What ships instead measures screen churn locally with no model at
> all: the frame reduced to a 64x36 grid of mean luminances, compared against the
> previous sample. Only a 2,304-byte digest outlives a sample, never a second frame.
>
> **It knows what it does not know.** On a calibration set, unambiguous screens come back
> at 0.88 and contentless ones abstain at 0.35, naming the evidence they lack. Those
> abstentions reach the user as questions rather than guesses. I also measured how stable
> that judgement is: five identical runs of one ambiguous screenshot span a 0.53
> confidence range and change category, while an unambiguous one moves 0.06. Variance
> concentrates exactly where the system is already unsure — which is why labels carry
> forward between samples and session confidence is weighted by duration.
>
> **What is not built**, stated because a demo that outruns its claims is worth less than
> a smaller honest one: SAM region tracking is written and refused on placement grounds;
> audio transcription for check-ins is written but its endpoint could not be found, so
> check-ins are typed; nothing mobile ships. `docs/validation.md` records what was tested
> and what it showed, including the results that contradicted the design.

---

## How it uses Meta AI tooling

> **Muse Spark Open** (`meta-models/muse-spark-open-1026`) is the Tier 2 classifier. It
> receives a downscaled frame plus a locally computed motion measurement and returns a
> category, a short label, a confidence and its evidence. Open weights are what makes the
> privacy argument hold: the same model runs on a rented GPU or your own, and moving it
> on-device is a config change rather than a rewrite.
>
> **Muse Spark 1.3** on the Meta Model API is Tier 4, the weekly review, and the only
> tier that touches a vendor-managed endpoint. It is handed category names and
> pre-computed durations — never a frame, a window title or a transcript. It also
> interprets Tier 3 check-ins, turning "at the gym for 45 minutes, lower body" into a
> bounded, categorised, backdated span.
>
> **SAM 3.1** is integrated and deliberately unused, for the placement reason above.
>
> Two findings worth passing on. Muse Spark Open reasons before it answers, so a tight
> `max_tokens` returns null content and a `finish_reason` of `length` rather than a
> visible error; the transport now detects truncation-before-answer and retries with a
> doubled budget. And the Tier 4 prompt had to be told that restating one figure is not
> an observation — and then that two figures which cannot differ are one figure, after it
> reported that a period total matched its only contributing day.

---

## Technical depth

> ~429 tests across three suites: 250 for the agent, 144 for the server, 35 for a
> pre-commit credential scanner.
>
> Offline-first by construction. Events are written to disk before any send and removed
> only once the server confirms, giving at-least-once delivery against an endpoint that
> is idempotent on `(device_id, ts)`. A laptop that spent two hours on a train does not
> come back with a two-hour hole.
>
> Sessions are derived, never written by clients, so a taxonomy change re-segments
> history instead of losing it. A user-stated span supersedes the observations it
> contradicts — within one device, because across devices an overlap is not a
> contradiction.
>
> Derived labels are scrubbed of credential-shaped text at the single gate every
> persisted string passes through, because a description of a screen is not a screen and
> the no-frames-on-disk invariant said nothing about it.
>
> Most of these were found by running the thing, not by testing it: a classifier silently
> replaced by a stub because the agent never loaded `.env`; a category named after a venue
> that made the system refuse "I went for a run"; an inference path the model never once
> took.

---

## What's next

> A SAM 3.1 deployment on infrastructure the operator controls, so region-level attention
> works without weakening the placement rule. The audio leg of check-ins, once the right
> endpoint is found. Android, where `UsageStatsManager` makes this tractable. And the
> open question the frame differ raised: whether per-region churn converts the
> abstentions that whole-screen churn currently causes.

---

## For the X / viral post

> Every time tracker tells you "Chrome: 4 hours."
>
> Mine tells you what those four hours were: deep work, research, or two and a half hours
> of doomscrolling.
>
> It runs on open weights, so your screen never leaves a machine you control. Sending a
> frame to a vendor endpoint doesn't warn — it raises.
>
> Built for #MetaHackathon:
> https://whizztimmy.github.io/groundtruth/

Attach the contrast view screenshot — the two columns side by side are the whole argument
and need no caption.

A second post, if the first lands:

> The part I'm proudest of is that it refuses to guess.
>
> Contentless screens come back at 0.35 confidence with the model naming the evidence it
> lacks, and you get asked instead of told.
>
> I measured the instability too: identical input varies 0.53 on ambiguous screens, 0.06
> on clear ones.

Remember the Most Viral prize needs the post link submitted through the platform, not
just posted.

---

# Field by field

The form as it actually appears. Fields marked * are required.

## Team Name *

> GroundTruth

Same as the project. A solo entry does not need a separate team identity, and one name
is one less thing for a judge to reconcile.

## Track *

**Indie Developer Track**

## Which problem statement? *

**Build for Your World**

This is a tool built to answer a question about its author's own days - devotion time,
gym, doomscrolling, coding - on the author's own machine, with the privacy constraint
that follows from it being personal. That is squarely the first statement. "Push the
Frontier" invites comparison against research entries on novelty alone, which is not
where this is strongest; its strength is that it works, is honest about what it does not
do, and is built on an argument.

## Public GitHub Repository *

> https://github.com/whizztimmy/groundtruth

## 3-Minute Demo Video *

Paste after upload. Test the link in a private window first - an unlisted video that is
actually private is the most common way to lose a demo score.

## Project Description *

Use the full description above. If the box is short, use the ~50-word version.

## Describe your use of Meta products *

Use the "How it uses Meta AI tooling" section above.

## Why are Meta's models important to your solution? *

> Because the central claim is only credible if the weights are open.
>
> A tracker that understands your screen is acceptable only if that understanding never
> reaches anyone who could keep it. That is a property of where a model can run, not of a
> policy page. Muse Spark Open runs on a rented GPU today and on the user's own hardware
> with a config change, so the agent can enforce "a frame never reaches a vendor-managed
> endpoint" as an exception that raises rather than a promise in a README.
>
> The division of labour depends on it too. Open weights see the pixels; the managed Meta
> Model API sees only category names and pre-computed durations for the weekly review, and
> never a frame, a window title or a transcript. That split is only available because one
> of the two models is open.
>
> It cost something, which is how you know it is real. SAM 3.1 would give better attention
> tracking than what ships, and it is served on the Meta Model API - but only as a managed
> endpoint, and frames are the one thing that may not go to one. It is implemented, tested
> and deliberately unused. A closed-weights stack would have made this project's argument
> unmakeable rather than merely expensive.

## Partner technologies feedback (Baseten)

> Baseten served Muse Spark Open for Tier 2. The OpenAI-compatible surface meant the tier
> needed configuration and no code - the transport already spoke it.
>
> One thing worth documenting on the model page: Muse Spark Open reasons before it
> answers, so a tight max_tokens returns null content with finish_reason "length" rather
> than an error. The first failure read as a parser bug and pointed away from the cause.
> The fix was mechanical once understood - detect truncation-before-answer and retry with
> a doubled budget - but a line in the docs would have saved an hour.
>
> GET /v1/models was accurate and genuinely useful: listing what a key could actually
> reach settled two questions that guesswork had got wrong.

## Survey questions

These are opinions, so they are yours rather than mine. What follows is what this project
actually encountered, which is the only honest basis for answering.

**Biggest obstacle: Finding clear documentation or code examples.**

Both non-text models appear in /models but are not on /chat/completions. sam-3.1 returned
a genuinely helpful error naming /v1/responses. muse-voice-transcribe-1.0 returned a bare
model_not_found, and /audio/transcriptions and /audio/translations did not exist, so the
audio leg of Tier 3 shipped unverified. If a "please specify" box is offered, that is the
specific, and it is useful feedback rather than a complaint.

**What matters most when choosing a model: Open weights or self-hosting.**

True here in the strongest sense: the project does not exist without it.

**Compared with other models:** answer from your own experience, or pick "We haven't used
other models for this kind of work" if that is the truth. Do not guess a comparison.

**Muse Code recommendation:** leave blank. This project did not use it, and a rating
without use is noise in someone's data.

**Production blocker:** "Not available in our country or region" or "Nothing would stop
us" - whichever is true for you. The honest one for this architecture is that Tier 2 would
move on-device, so the Meta Model API would carry only the weekly review.

## Consent checkbox

Required, and it is a real grant: Meta and Cerebral Valley may publish and promote what
you submit. The repository is already public and the demo shows a sample day, so there is
nothing here you have not already chosen to show - but read it rather than tick past it.
