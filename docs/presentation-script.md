# Argus — 10-Minute Stakeholder Presentation Script

> **Audience:** Non-technical stakeholders (leadership, business, compliance).
> **Total time:** 10 minutes (≈7 min talk + 3 min live demo).
> **How to use this:** Each section has a **slide title** and a **say-this** script.
> Read the *italic stage directions* silently — they're for you, not the audience.
> Words in **bold** are where to slow down and land the point.

**Timing map (keep an eye on the clock):**

| # | Slide | Time | Running total |
|---|---|---|---|
| 1 | Title / Intro | 0:45 | 0:45 |
| 2 | The Problem (story) | 1:15 | 2:00 |
| 3 | The Solution: Argus | 0:50 | 2:50 |
| 4 | Where Argus Fits | 1:20 | 4:10 |
| 5 | The AI Pillars | 1:20 | 5:30 |
| 6 | An Incident's Journey | 1:00 | 6:30 |
| 7 | Live Demo | 3:00 | 9:30 |
| 8 | What's Next | 0:15 | 9:45 |
| 9 | Conclusion | 0:15 | 10:00 |

---

## Slide 1 — Title: "Argus: An AI Security Analyst That Never Sleeps"

*(Open confident, warm. Smile. ~45 seconds.)*

> "Good morning, everyone. Thank you for your time.
>
> In Greek mythology, **Argus was a giant with a hundred eyes** — the all-seeing watchman who never fully closed them all. We named our project after him for a reason.
>
> Today I want to show you **Argus**: a system that watches over our digital environment **around the clock**, spots genuine threats in the noise, and — where it's safe to — **acts on them in seconds**, all while keeping a **human in control** of the big decisions.
>
> In the next ten minutes I'll show you the problem we're solving, how Argus solves it, and then a **live demo** of it catching real threats."

---

## Slide 2 — The Problem: "Drowning in Alarms" *(storytelling)*

*(Slow down. This is the emotional hook. ~75 seconds.)*

> "Let me tell you about a typical night for a security analyst — let's call her Sarah.
>
> Sarah's job is to watch the alarms coming from our systems. The trouble is, those systems generate **thousands of alarms every single day**. The vast majority are completely harmless — a backup job running at 2 a.m., an employee logging in from home, a routine scan.
>
> It's like a smoke alarm that goes off every time you make toast. After a while... **you stop running to check.**
>
> And that's the danger. Buried in those thousands of toast-alarms is the **one real fire** — the actual attacker. Sarah is a skilled professional, but she's human. She's tired, she's overwhelmed, and the **average attacker now sits inside a network undetected for weeks** before anyone notices.
>
> So the problem isn't a lack of alarms. It's the **opposite** — too many alarms, too few people, and too little time. **The signal is drowning in the noise.**"

---

## Slide 3 — The Solution: "Meet Argus"

*(Shift to optimistic, brisk. ~50 seconds.)*

> "This is exactly what Argus is built for.
>
> Think of Argus as a **tireless junior analyst** sitting beside Sarah. It reads **every single alarm** — all of them — and does three things:
>
> 1. It **filters out the noise**, so the harmless toast-alarms get closed automatically.
> 2. For the **real threats**, it investigates them the way an experienced analyst would — gathering context and connecting the dots.
> 3. And then it **takes action** — automatically for the routine cases, and for anything serious or destructive, it **stops and asks a human first.**
>
> The result: Sarah's team only ever looks at what **actually matters** — and they look at it with the homework already done."

---

## Slide 4 — Where Argus Fits: "The Security Assembly Line"

*(This is the one "educational" slide. Use a simple left-to-right diagram. ~80 seconds.)*

*(Point to each stage as you name it.)*

> "Let me show you where Argus sits in the bigger picture. Think of it as an **assembly line for security**, with a few stations:
>
> - **First, the Sensors.** These are like **security cameras and motion detectors** all over our systems — they simply notice when *something happens*.
>
> - **Next, the SIEM.** That's the **central control room** where all those camera feeds are collected and recorded in one place. *(SIEM just stands for the system that gathers all the logs together.)*
>
> - **Then, the Detectors.** We have two kinds working together. One follows a **rulebook** — 'if you see exactly this, raise a flag.' The other is our **AI anomaly detector**, which learns what **'normal' looks like** for each user and machine, and flags when something is **out of character** — the way your bank notices a strange purchase. Together they cover each other's blind spots.
>
> - **Then comes Argus — the SOAR.** This is the **decision-maker and responder.** SOAR just means it **orchestrates** the investigation and the response. Everything before this point only *raises a hand and says 'look here.'* **Argus is the part that actually investigates and acts.**
>
> - And finally, the **Feedback Loop.** When Argus handles something, it **remembers the outcome** — so the next time a similar threat appears, it's already smarter. The system **gets better over time, on its own.**"

---

## Slide 5 — The AI Pillars: "How Argus Thinks"

*(Energetic. These are your differentiators. ~80 seconds.)*

> "So what makes Argus *intelligent*? Four things.
>
> **Pillar one — Smart Triage.** For every alarm, Argus makes a fast judgment call: **real threat, harmless noise, or unsure?** Just like a triage nurse in an emergency room deciding who needs a doctor *now*. The noise gets closed; the real cases move forward.
>
> **Pillar two — Investigation & Enrichment.** This is where it shines. For a real threat, Argus pulls in context from **three sources at once**: global threat intelligence *(is this a known bad actor?)*, a knowledge library of attacker techniques, and its **own memory of past incidents** *(have we seen this before?)*. It **connects the dots** the way a seasoned analyst would — but in **seconds, not hours.**
>
> **Pillar three — Guided Response.** Argus picks the right **playbook** — the right set of actions — for the situation. Routine fixes happen automatically. Anything **destructive** — like shutting down a machine or disabling an account — **never happens without a human signing off.**
>
> **Pillar four — Verify and Learn.** After it acts, Argus **double-checks the threat is actually gone**. If it isn't, it escalates to a person. And it **files the outcome away in its memory** — closing that feedback loop I mentioned.
>
> And one promise that runs through all of it: **every step is logged, timed, and stripped of personal data** — so there's a complete, auditable record of every decision."

---

## Slide 6 — An Incident's Journey: "From Alarm to Resolved in 30 Seconds"

*(Use a simple flow graphic. Walk left to right. ~60 seconds.)*

> "Let's put it all together with the **journey of a single alarm.**
>
> An alarm arrives. Argus **cleans it up and removes any sensitive data** immediately. Then:
>
> - It asks: *real, or noise?* **Noise → closed automatically.** Done.
> - If it's real, it **investigates** — gathering all that context.
> - Then it **chooses a response.**
>   - Safe, routine action? **Argus does it itself** and opens a ticket.
>   - Serious, destructive action? Argus **stops and waits for a human** to approve or reject — and respects whichever they choose.
> - Finally, it **verifies the fix worked** and **remembers the outcome.**
>
> The routine cases — the vast majority — go from **alarm to resolved in well under a minute, with no human touch.** The serious cases land on a person's desk **already investigated**, with one clear decision to make. **That's the whole game: machines handle the volume, humans handle the judgment.**"

---

## Slide 7 — Live Demo *(≈3 minutes)*

*(Switch to the live screen. Keep narrating — never let the screen go silent. Have these pre-staged. If anything lags, keep talking; the dashboard updates live.)*

> "Enough slides — let me **show you Argus working** on real attack scenarios."

**Demo beat 1 — Noise gets dismissed (~30s)**
> *(Send a low-severity routine event, e.g. an internal scanner.)*
> "Here's a routine internal scan — the kind of thing that fires all day. Watch... Argus reads it, recognizes it as **authorized activity, and closes it automatically.** No human time spent, **no analyst even woken up.** That's noise handled."

**Demo beat 2 — A real threat, auto-remediated (~50s)**
> *(Send the SSH brute-force / Tor exit-node event.)*
> "Now a real one — repeated login attacks from a known **anonymous network**. Argus triages it as a **genuine threat**, investigates — and you can see it **recognized the source as malicious** — then **takes action and opens a ticket automatically.** From alarm to handled, in **seconds.** A human can review it later, but the response already happened."

**Demo beat 3 — The human-in-the-loop moment (~60s)** *(the showstopper)*
> *(Send the critical lateral-movement / 'impossible travel' event.)*
> "Now the important one. This is a **critical** threat where the right response is **destructive** — isolating a machine or disabling an account.
>
> Notice what Argus does **not** do: **it does not pull the trigger.** It investigates, prepares the recommended action... and then it **stops and waits for a human.** *(Point to the approval panel.)*
>
> As the analyst, I can **approve** — and the action executes instantly — or **reject** it if I know it's a false alarm. *(Approve one, reject one if time allows.)* Either way, **my decision, my name, and my reason are recorded permanently.** Argus did all the heavy lifting; the **human kept control of the consequences.**"

**Demo beat 4 — The dashboard & live feed (~40s)**
> *(Show the queue / KPIs / live update.)*
> "And this is the analyst's view. Noise correctly dismissed, threats auto-handled, and the few decisions waiting for a human — all in one place. **Live metrics** show how fast we're detecting and responding. *(Optionally fire one more event.)* Watch — a **brand-new incident appears in real time**, no refresh needed. **That's Argus, watching, around the clock.**"

*(Return to slides.)*

---

## Slide 8 — What's Next

*(Quick, forward-looking. ~15 seconds.)*

> "Where we're headed: **deeper cross-correlation** across signals to catch multi-stage attacks, and **live threat feeds** so Argus stays current with the latest attacker tactics — making it sharper every week."

---

## Slide 9 — Conclusion: "Machines Handle Volume. Humans Handle Judgment."

*(Slow down. Land it. Make eye contact. ~15 seconds.)*

> "So that's Argus. It turns a **flood of alarms into a short, prioritized list.** It **acts in seconds** on the routine, and it **keeps humans firmly in control** of anything serious — every decision auditable.
>
> The signal no longer drowns in the noise. **Thank you — I'd love to take your questions.**"

---

## Presenter Cheat-Sheet (don't show — for you)

**If you forget a term, use the plain-English version:**

| Term | Say instead |
|---|---|
| Sensors / IDS | "security cameras for our systems" |
| SIEM | "the central control room that collects all the logs" |
| Anomaly detector | "spots when something is out of character, like fraud detection on a credit card" |
| SOAR | "the decision-maker that investigates and responds" |
| Triage | "deciding what's urgent, like an ER nurse" |
| Enrichment | "gathering context / connecting the dots" |
| Playbook | "a standard response recipe" |
| HITL / approval | "a human signs off before anything destructive happens" |
| Feedback loop | "it remembers outcomes and gets smarter" |

**Three numbers to drop if asked:**
- Routine threats resolved in **under a minute**, no human needed.
- The **majority** of medium/high incidents handled automatically.
- **Zero** destructive actions without a recorded human approval.

**If the demo breaks:** stay calm, say *"the beauty of a recorded system is I can show you exactly what it did last time"* — and walk the dashboard of already-processed incidents.
