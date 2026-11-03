# Submission copy

Every field on the form, filled in. Copy straight across. Every figure is checkable
against this repository, which matters for a project whose argument is about not
asserting what you have not measured.

---

## Team Name *

```
GroundTruth
```

## Team Members

Already filled: Timothy Adetunji (timztech).

## Which track are you submitting to? *

**Indie Developer Track**

## Which problem statement did you build towards? *

**Build for Your World**

This answers a question about its author's own days, on the author's own machine, with
the privacy constraint that follows from it being personal. "Push the Frontier" would
invite comparison against research entries on novelty alone, which is not where this is
strongest.

## Most Viral Track: X post link

Paste after posting. Post text is at the bottom of this document.

## Project Description *

```
Every activity tracker ever built classifies your time by application name. So Chrome is
"productive" whether you were reading API docs or three hours deep in a feed. VS Code is
"coding" whether you were shipping or staring at a file you opened an hour ago. The
distinction people actually care about, focused work versus doomscrolling, is semantic,
and no amount of window-title metadata recovers it.

GroundTruth replaces app-name heuristics with an always-on agent that reads the screen and
names the activity.

It escalates only when it has to. A process name resolves the unambiguous majority for
free. When it cannot, and a browser never can, the agent captures a frame, reduces it to
1024px, measures how much of the screen changed since the last sample, and asks an
open-weights model what the person is doing. The answer carries a confidence and the
evidence it rests on. Off-screen hours are stated by the user and recorded at the
confidence a stated fact deserves. Once a week a reasoning model looks at the derived
durations and reports what it notices.

The privacy boundary is enforced in code, not promised. Classification runs on Muse Spark
Open, on infrastructure the operator controls, and sending a frame to a vendor-managed
endpoint raises PlacementViolation rather than succeeding. A backend with no declared
placement defaults to the most restrictive one, so a misconfiguration costs a refused call
instead of a silent leak.

That rule cost something real. SAM 3.1 would give better attention tracking than what
ships, and it is available on the Meta Model API, but only as a managed endpoint, and
frames are the one thing that may not go to one. It is implemented, tested, and
deliberately unused. What ships instead measures screen churn locally with no model at
all: the frame reduced to a 64x36 grid of mean luminances, compared against the previous
sample. Only a 2,304-byte digest outlives a sample, never a second frame.

It also knows what it does not know. On a calibration set, unambiguous screens return 0.88
confidence and contentless ones abstain at 0.35, naming the evidence they lack. Those
abstentions reach the user as questions rather than guesses. I measured how stable that
judgement is: five identical runs of one ambiguous screenshot span a 0.53 confidence range
and change category, while an unambiguous one moves 0.06. Variance concentrates exactly
where the system is already unsure, which is why labels carry forward between samples and
session confidence is weighted by duration.

What is not built, stated because a demo that outruns its claims is worth less than a
smaller honest one: SAM region tracking is written and refused on placement grounds; audio
transcription for check-ins is written but its endpoint could not be found, so check-ins
are typed; nothing mobile ships. docs/validation.md records what was tested and what it
showed, including the results that contradicted the design.

429 tests across three suites: 250 agent, 144 server, 35 for a pre-commit credential
scanner.
```

## Public GitHub Repository *

```
https://github.com/whizztimmy/groundtruth
```

## 3-Minute Demo Video *

Paste after upload. Test the link in a private window first. An unlisted video that is
actually private is the commonest way to lose a demo score.

## Describe your use of Meta products to build the submitted project *

```
Muse Spark Open (meta-models/muse-spark-open-1026) is the Tier 2 classifier. It receives a
downscaled screen frame plus a locally computed motion measurement, and returns a category,
a short label, a confidence, and the evidence it rests on. It is served on rented GPU
infrastructure with placement "self_hosted". Open weights are what make the privacy
argument hold: the same model runs on a rented GPU or the user's own, and moving it
on-device is a config change rather than a rewrite.

Muse Spark 1.3 on the Meta Model API is Tier 4, the weekly review, and the only tier that
touches a vendor-managed endpoint. It is handed category names and pre-computed durations,
never a frame, a window title, or a transcript. It also interprets Tier 3 check-ins,
turning "at the gym for 45 minutes, lower body" into a bounded, categorised, backdated
span, and refusing when no duration was stated.

SAM 3.1 is integrated and deliberately unused. perceive/regions.py uses its object
multiplexing to track named screen regions so churn can be attributed to which region
moved. It is served on the Meta Model API via /v1/responses, but that is a managed
endpoint and SAM is the one component that receives raw frames, so routing to it is exactly
what the placement rule exists to refuse.

Two findings worth passing on. Muse Spark Open reasons before it answers, so a tight
max_tokens returns null content with finish_reason "length" rather than a visible error;
the transport now detects truncation-before-answer and retries with a doubled budget. And
the Tier 4 prompt had to be told that restating one figure is not an observation, and then
that two figures which cannot differ are one figure, after it reported that a period total
matched its only contributing day.
```

## Why are Meta's models important to your solution? *

```
Because the central claim is only credible if the weights are open.

A tracker that understands your screen is acceptable only if that understanding never
reaches anyone who could keep it. That is a property of where a model can run, not of a
policy page. Muse Spark Open runs on a rented GPU today and on the user's own hardware with
a config change, so the agent can enforce "a frame never reaches a vendor-managed endpoint"
as an exception that raises rather than a promise in a README.

The division of labour depends on it too. Open weights see the pixels. The managed Meta
Model API sees only category names and pre-computed durations for the weekly review, and
never a frame, a window title, or a transcript. That split is only available because one of
the two models is open.

It cost something, which is how you know the rule is real. SAM 3.1 would give better
attention tracking than what ships, and it is served on the Meta Model API, but only as a
managed endpoint, and frames may not go to one. It is implemented, tested, and refused. A
closed-weights stack would have made this project's argument unmakeable rather than merely
more expensive.
```

## Provide feedback from your experience using partner technologies

```
Baseten served Muse Spark Open for Tier 2. The OpenAI-compatible surface meant the tier
needed configuration and no code, because the transport already spoke it. Inline base64
images worked with no upload step, which the playground's generated code had suggested
would be required.

One thing worth documenting on the model page: Muse Spark Open reasons before it answers,
so a tight max_tokens returns null content with finish_reason "length" rather than an
error. The first failure read as a parser bug and pointed away from the cause. The fix was
mechanical once understood, detect truncation-before-answer and retry with a doubled
budget, but a line in the docs would have saved an hour.

GET /v1/models was accurate and genuinely useful. Listing what a key could actually reach
settled two questions that guesswork had got wrong.
```

---

## Survey questions

These are opinions, so change any that do not match yours. What follows is what this
project actually encountered.

| Question | Answer | Why |
|---|---|---|
| How likely to build with Meta Model API? | **Very likely** | Chat Completions worked first try, inline images worked, Tier 4 produced usable JSON on the first real call |
| Overall opinion of Meta Model API? | **Somewhat favorable** | Model quality was high; discoverability of non-text models cost real hours. "Very favorable" is defensible if you weight the models over the docs |
| Recommend Meta Model API (0 to 5) | **4** | Strong models, documentation friction |
| Recommend Muse Code (0 to 5) | **Leave blank** | This project did not use it, and a rating without use is noise in someone's data |
| Biggest obstacle | **Finding clear documentation or code examples** | See the specific below |
| Compared with other AI models | **We haven't used other models for this kind of work** | True. Do not guess a comparison you did not run |
| What matters most when choosing | **Open weights or self-hosting** | The project does not exist without it |
| Hardest to switch | **Our product is tuned to our current model's behavior and prompts** | The token-budget handling and two Tier 4 prompt rules were written against observed behaviour |
| Production blocker | **How our data and our users' data would be stored or used** | The on-thesis answer: Tier 2 would move on-device, leaving the API carrying only the weekly review |

If the obstacle question offers a "please specify" box:

```
Both non-text models appear in GET /v1/models but are not served on /chat/completions.
sam-3.1 returned a genuinely helpful error naming /v1/responses. muse-voice-transcribe-1.0
returned a bare model_not_found, and /audio/transcriptions and /audio/translations did not
exist, so the audio leg of our voice check-in tier shipped written but unverified. An
endpoint column in the model catalogue would have prevented both detours.
```

## Consent checkbox

Required, and it is a real grant: Meta and Cerebral Valley may publish and promote what you
submit. The repository is already public and the hosted demo shows a sample day, so nothing
here is new exposure. Read it rather than tick past it.

---

## X post, for the Most Viral track

```
Every time tracker tells you "Chrome: 4 hours."

Mine tells you what those four hours were: deep work, research, or two and a half hours of
doomscrolling.

It runs on open weights, so your screen never leaves a machine you control. Sending a frame
to a vendor endpoint doesn't warn. It raises.

Built for #MetaHackathon
https://whizztimmy.github.io/groundtruth/
```

Attach the contrast view screenshot. The two columns side by side are the whole argument
and need no caption.

Second post, if the first lands:

```
The part I'm proudest of is that it refuses to guess.

Contentless screens come back at 0.35 confidence with the model naming the evidence it
lacks, and you get asked instead of told.

I measured the instability too: identical input varies 0.53 on ambiguous screens, 0.06 on
clear ones.
```

The Most Viral prize needs the post link submitted through the platform, not just posted.
