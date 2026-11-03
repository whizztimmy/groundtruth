# Recording script

Word for word, with timings. `docs/DEMO.md` is the reference; this is the thing you read
while recording.

Target **2:45–3:00**. Every number below is real and was produced by the commands shown.
If a number differs on the day, say the one on your screen — the point survives.

**Two ways to narrate.** Speak it, or record silent and add the `CAPTION` lines as text.
Captions are faster and remove retakes for stumbles; a spoken track scores better on a
demo rubric. If you are short on time, caption it.

---

## Before you hit record

- [ ] Screen cleared: no banking, payroll, invoices, password manager, private DMs.
      Tier 2 describes what it sees and the description is stored.
- [ ] Server tab running, dashboard tab running (staged for you already)
- [ ] Agent tab showing `classifier ready: meta-models/muse-spark-open-1026`
- [ ] Browser on the hosted demo in one tab, `localhost:5173` in another
- [ ] Notifications off

---

## Beat 1 — the claim (0:00–0:20)

**On screen:** the hosted demo, contrast view.

> "Every activity tracker classifies your time by application name. Mine says Chrome,
> four hours twelve. That is true and it is useless — it does not tell me whether I was
> reading documentation or three hours deep in a feed."

> "Here is the same day. Doomscrolling two thirty-one. Deep work one forty. Research one
> oh nine. Same eight hours, same machine. Only the question changed."

`CAPTION: Chrome 4h 12m — true, and useless.`
`CAPTION: Same day, by activity: doomscrolling 2h 31m, deep work 1h 40m, research 1h 09m.`

---

## Beat 2 — it is really doing this (0:20–1:05)

**On screen:** the agent terminal tab.

> "That is not a mock-up. This is the agent running against my actual screen, right now."

Let two or three Tier 2 lines appear. They take about twenty seconds each.

> "It is not reading my window title. It knows this is a hackathon project, that I am
> debugging it, and which files are open. A conventional tracker would have written
> `claude.exe`, ninety seconds."

`CAPTION: Live. Classifying the screen it is being recorded on.`

**If the pause feels long**, fill it with the ladder:

> "Tier one is free — process name, no model, and it resolves most of a day. Tier two only
> fires when tier one genuinely cannot tell, which is the whole reason this is affordable
> to leave running."

---

## Beat 3 — it refuses to guess (1:05–1:40)

**On screen:** dashboard, Uncertain tab.

> "Most of these systems are confident about everything. This one abstains. These are
> spans it would not label, held as questions instead of guesses."

Point at one evidence line.

> "And it says what it was missing. This one wanted motion data to tell reading from
> scrolling — which I had not built when it asked for it."

> "I also measured how unstable that judgement is. The same ambiguous screenshot, five
> identical runs, moves across a zero point five three confidence range and changes
> category. The unambiguous one moves zero point zero six. Variance concentrates exactly
> where the system is already unsure — which is why labels carry forward between samples
> and session confidence is weighted by duration."

`CAPTION: It declines to guess — and names the evidence it lacks.`
`CAPTION: Measured: identical input varies 0.53 on ambiguous screens, 0.06 on clear ones.`

This is the strongest thirty seconds in the demo. Do not rush it.

---

## Beat 4 — the privacy claim is executable (1:40–2:00)

**On screen:** a terminal. Run the one-liner from `DEMO.md` beat 4.

> "Screen frames go to open-weights models on infrastructure I control. That is not a
> policy, it is an exception."

Let the traceback land.

> "And a backend with no declared placement defaults to the most restrictive one. So
> forgetting to configure it costs me a refused call, not your screen."

`CAPTION: PlacementViolation — frames cannot reach a vendor-managed endpoint.`

---

## Beat 5 — the hours a screen cannot see (2:00–2:25)

**On screen:** terminal.

```
python -m groundtruth.checkin --text "at the gym for 45 minutes, lower body"
```

> "The gym, a commute, a walk. No amount of perception recovers those. So I state it, and
> it is recorded at the confidence a stated fact deserves."

Then the refusal — this is the half that matters:

```
python -m groundtruth.checkin --text "I just got back from the gym"
```

> "It will not guess a duration. Across five phrasings the model never once estimated when
> nothing was said — so there is no inferred path at all. A guessed number becomes a
> figure on your timeline that nobody said, and you would plan around it."

`CAPTION: Off-screen time, stated — not inferred.`
`CAPTION: No duration stated, no duration invented.`

---

## Beat 6 — what is not built (2:25–3:00)

**On screen:** the README, or just your face.

> "What is not here. SAM 3.1 region tracking is written and tested, and I am not using it.
> It is available on Meta's API — but only as a managed endpoint, and frames are the one
> thing that may not go to one. Using it would mean weakening the rule I just showed you,
> so what ships instead is a local frame differ that measures screen churn with no model
> at all."

> "Audio transcription for check-ins is written, and I could not find the endpoint that
> serves it, so check-ins are typed. Nothing mobile ships. The README says all of that."

Then end on the recovery — this is the closing line:

> "And the cheap version found the argument for the real one. On a live run the model saw
> a static page while whole-screen churn said the display was moving, and it abstained
> rather than guess, because a whole-screen measurement cannot say which region moved.
> That is exactly the failure per-region tracking fixes. I have the evidence for the next
> version because the honest version told me where it breaks."

`CAPTION: SAM 3.1 is available — and refused, because frames may not reach a managed endpoint.`
`CAPTION: The privacy claim costs something. That is what makes it a claim.`

---

## If something fails mid-take

Keep rolling and say it. A demo that hits a real error and the presenter explains it reads
as more credible than a cut. The one exception is `shallow work 0.50` on every line — that
is the stub classifier, which means `.env` did not load; stop, restart from `agent/`, and
start the take again.

| Failure | Say this |
|---|---|
| Baseten slow | "That is a cold start on rented inference — it is a real dependency." |
| A label looks wrong | "That one is wrong, and it is in the uncertain queue for that reason." |
| Dashboard empty | Check the date. It opens on today; your data may be on another day. |

---

## After recording

- [ ] Watch it once at 2x, listening for anything you claimed that is not true
- [ ] Check no private content is legible in any frame, including terminal scrollback
- [ ] Upload, make it public or unlisted-with-link, test the link in a private window
- [ ] Paste the link into the submission form, with the repo and
      https://whizztimmy.github.io/groundtruth/
