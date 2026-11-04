# Demo script

Every command here has been run. Timings are measured, not estimated.

The argument the demo has to make, in one line: **a tracker that classifies by application
name cannot tell apart the things people care about, and this one can — including knowing
when it cannot.**

---

## Before you record

**Clear your screen first.** Tier 2 describes what it sees, and those descriptions are
stored. A real run on 2026-10-31 produced the evidence string
`Chrome shows work.mercor.com/earnings with $10.77 total earnings and payout cards`. That
is `redact.py` working as specified — it scrubs credentials, not personal data — so
earnings, balances, DMs, client names and email subject lines all survive into the
dashboard. Decide what is on screen before the agent looks at it.

Close: banking, payroll and invoicing tabs, password managers, private DMs, anything under
NDA. Keep open what you actually want labelled.

**Checklist:**

```bash
cd agent && ./.venv/Scripts/python.exe -m pytest tests/ -q
```

```bash
cd agent && ./.venv/Scripts/python.exe check_api.py
```

The first prints `276 passed`. The second confirms both endpoints answer before you are
live — do not skip it, because a cold Baseten deployment can take its time on the first
call and you do not want to discover that on camera.

**Seed the dashboard:**

```bash
cd server && ./.venv/Scripts/python.exe -m groundtruth_server.fixtures --db demo.db
```

```bash
cd server && GROUNDTRUTH_DB=demo.db ./.venv/Scripts/python.exe -m uvicorn groundtruth_server.app:app --port 8000
```

```bash
cd web && npm run dev
```

The fixture is a deliberately awkward day: 480 events, 12 sessions, two devices, a session
spanning two processes, genuine idle gaps, and Chrome accounting for 208 minutes while
meaning five different things. That last number is the demo's central comparison and it is
already built.

---

## The three-minute cut

### Beat 1 — the contrast (0:00–0:25)

Open the dashboard's contrast view on the fixture day.

> "Screen Time tells me Chrome, three hours twenty-eight. Here is the same three hours
> twenty-eight: deep work on an auth refactor, reading API documentation, a standup
> thread, and two hours of doomscrolling. Same application. Five different things."

Then the sharper version, which `SCRIPT.md` runs as beat 1b: the activity percentage that
Hubstaff and the Upwork timer actually measure is computed on every event here too, and
the evidence line names where it disagrees. Reading scores 10% and files as idle;
doomscrolling scores near 100%. It only fires on a complete ten minute segment, so take it
from accumulated data rather than waiting for it live.

Do not explain the architecture yet. Let the comparison land first.

### Beat 2 — it labels the work, not the window (0:25–1:10)

Run the agent against your actual screen while you are working on this project.

```bash
cd agent && TIER2_ENABLED=1 ./.venv/Scripts/python.exe -m groundtruth.main
```

Measured from a real run: startup is immediate, the first Tier 2 label lands about nine
seconds later, and subsequent ones every twenty to thirty seconds under the rate limit.
Let it produce three or four.

A real run produced:

```
classifier ready: meta-models/muse-spark-open-1026
tier 2 | claude.exe | deep work | 0.88
tier 2 | claude.exe | deep work | 0.82
```

> "It is not reading my window title. It knows this is debugging a screen agent, for a
> hackathon, and which files are open. A conventional tracker would have recorded
> `claude.exe, ninety seconds`."

**Pacing:** nine seconds of silence is long on camera. Talk through the tier ladder while
the first call is in flight — Tier 1 is free and resolves the unambiguous majority, Tier 2
only fires when Tier 1 cannot tell.

### Beat 3 — it refuses to guess (1:10–1:45)

Open the uncertain queue.

> "Most of these systems are confident about everything. This one abstains. On a
> calibration set, contentless screens come back at 0.35 and the model names the evidence
> it is missing — not a label it invented."

Then the sharper half, if you want the strongest version of this beat:

> "And I know how unstable that judgement is, because I measured it. The same ambiguous
> screenshot, five identical runs, moves across a 0.53 confidence range and flips
> category. The unambiguous one moves 0.06. Variance concentrates exactly where the system
> is already unsure — which is why labels carry forward between samples and session
> confidence is weighted by duration rather than taken from the last reading."

That is the single most technically credible thirty seconds available to you. It is a
measurement nobody else in the competition will have, and admitting instability while
showing the design that absorbs it reads as rigour rather than weakness.

### Beat 4 — the privacy claim is executable (1:45–2:05)

```bash
cd agent && ./.venv/Scripts/python.exe -c "import asyncio; from PIL import Image; from groundtruth.backends import Backend, Placement, vision; asyncio.run(vision(Backend(base_url='https://api.meta.ai/v1', model='muse-spark-1.3', placement=Placement.MANAGED, api_key='x'), system='s', user_text='u', frame=Image.new('RGB', (8, 8))))"
```

It crashes:

```
PlacementViolation: refusing to send screen content to a managed backend
(muse-spark-1.3). Set muse-spark-1.3's placement to 'local' or 'self_hosted',
or route this call to a backend that is.
```

> "Screen frames go to open-weights models on infrastructure I control. That is not a
> promise in a privacy policy, it is an exception. And a backend with no declared
> placement defaults to the most restrictive one, so forgetting to configure it costs me
> a refused call rather than costing you your screen."

### Beat 5 — the weekly review (2:05–2:40)

```bash
cd agent && ./.venv/Scripts/python.exe review_week.py
```

This runs Tier 4 on Meta's own API over a week with patterns planted on purpose, so its
answers can be marked. A real run returned:

> Monday shows deep work 1h 50m with doomscrolling 30m, while Thursday shows deep work 28m
> with doomscrolling 2h

> Deep work occurred in shorter average stretches than doomscrolling
> (deep work: average stretch 48m; doomscrolling: average stretch 1h 07m)

> "Tier 4 is the only tier that touches a vendor-managed endpoint, and it never sees a
> frame, a window title or a transcript — only category names and durations that were
> computed rather than generated. Every figure it cites is one it was handed."

**Caveat worth knowing:** the review is not reproducible. Five runs on identical input
produced a different set of observations each time. The planted trade-off appeared in all
five; anything appearing once is a coin toss. If you demo this live, run it beforehand and
be ready for a different answer.


### Beat 5b — the hours a screen agent cannot see (2:40—2:55)

```bash
cd agent && ./.venv/Scripts/python.exe -m groundtruth.checkin --text "at the gym for 45 minutes, lower body"
```

```
  fitness: lower body at the gym
  17:33 to 18:18 (45 min, as stated)
  confidence 0.97, 23 events
```

> "The gym, the commute, a walk — no amount of perception recovers those, and
> they are the hours people most want back. So Tier 3 inverts it: I state the
> activity and it is recorded at the confidence a stated fact deserves. It
> backdates from now, because you check in on the way back to the desk."

Then show it refusing, which is the better half:

```bash
cd agent && ./.venv/Scripts/python.exe -m groundtruth.checkin --text "I just got back from the gym"
```

```
  not recorded: that did not say how long it was - try "at the gym for 45 minutes"
```

> "It will not guess a duration. Across five phrasings the model never once
> estimated when nothing was said, so there is no inferred path at all — a
> guessed number becomes a figure on your timeline that nobody uttered, and
> you would plan around it."

**Worth saying out loud:** the agent never opens a microphone. There is no listening
path in the codebase at all, so Tier 3 happens only when you run that command.

### Beat 6 — what is not built (2:40–3:00)

> "SAM 3.1 region tracking is implemented and unit-tested, and I am not using it.
> `sam-3.1` is on the Meta API — but only as a managed endpoint, and frames are
> the one thing that may not go to one. So what ships is a local frame differ with
> no model at all. Audio transcription for check-ins is written, but I could not
> find the endpoint that serves it, so check-ins are typed. Nothing mobile ships.
> The README says all of that."

Then the recovery, which is true and is the strongest note to end on:

> "And the fallback found the argument for the real thing. On a live run the model saw a
> static page while whole-screen churn said the display was moving, and it abstained
> rather than guess — because whole-screen measurement cannot say *which* region moved.
> That is the exact failure per-region tracking fixes. I have the evidence for the next
> version because the cheap version told me where it breaks."

---

## Traps

**The fixture's `fitness` session is seeded data from a phone device.** Tier 3 is now
implemented, so you *can* demonstrate a check-in live — but there is still no mobile
client, so the phone handoff in the fixture is illustrative. Say it is seeded data. The
category was renamed from `gym` to `fitness` so the fixture and a live check-in agree.

**Do not demo a single ambiguous sample and treat it as stable.** It may flip between takes.
Demo confident samples live; demo abstention from the recorded calibration set.

**Do not say "Muse Glimmer".** The classifier is Muse Spark Open. The earlier README said
otherwise and it was wrong.

**Do not say "local" unqualified.** It is open weights on rented infrastructure. The
argument — open weights means the operator chooses, and moving it on-device is a config
change — is strong and accurate. "Running locally" is not.

---

## If something fails live

| Failure | Recovery |
|---|---|
| Baseten slow or down | Pre-record beat 2. Keep the recording in the same window geometry so the cut is invisible |
| Meta API fails | `review_week.py` output from a prior run; say it is a prior run |
| Agent labels everything `shallow work 0.50` | It is running `StubClassifier` because `.env` did not load. Check you are launching from `agent/` |
| Dashboard empty | Date range. The fixture day is 2026-10-28, and the API requires an explicit UTC offset |

---

## What the submission needs besides the video

- Public repository link, with the hackathon disclosure in `README.md`
- Official Rules accepted on Cerebral Valley — **this is a disqualification condition, and
  it is not code, so it is the easiest thing on this list to forget**
- A demo video link
- For Most Viral: the post link submitted through the platform, not just posted
