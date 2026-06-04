# EvolvBI — Demo Video Script
# Target length: 2:45 – 3:15 minutes
# Record at: https://evolvbi-3f3swnt3qq-uc.a.run.app
# Track: Arize Phoenix
# Tool: Loom, QuickTime screen capture, or OBS

---

## WHY THIS SCRIPT IS DIFFERENT

EvolvBI's whole point is the **self-improvement loop**. A demo that just asks
questions wastes it. The arc below ends with the agent reading its own failures
in Phoenix and rewriting its own prompt — live. That's the moment that wins the
Arize track, so the script is built backwards from it.

---

## SETUP BEFORE RECORDING (critical — the loop needs failures to learn from)

The "Run improvement loop" button reads **scored failure traces** from Phoenix.
If there are none, it says "no failures found" and your big finish dies. So seed
them first:

- [ ] Open https://evolvbi-3f3swnt3qq-uc.a.run.app and **warm it up** with one
      throwaway question (cold start is the slowest path).
- [ ] Ask 4–5 questions, including **at least one designed to fail** so the loop
      has a real pattern to fix. Good seed failures:
      - `What was the total portfolio revenue in 2019?` (no data — tests the no-volunteer rule)
      - `What is the average dwell time per shopper?` (no such column — should error/decline cleanly)
- [ ] In a terminal, score those traces so Phoenix has labels:
      `python evals/run_evals.py`
      Confirm it logs `sql_success`, `sql_relevance`, and `sql_grounding` annotations.
- [ ] Open the Phoenix project (sidebar "Open Phoenix traces ↗") in a second tab so
      you can cut to it instantly.
- [ ] Click **🗑️ Clear conversation** to start clean on camera.
- [ ] Window at 1280×720 or 1920×1080 — sidebar visible. Close notifications.
- [ ] Dry-run the whole flow once, including the improvement loop, end to end.

> Every showcase question below was verified against the live warehouse.

---

## THE STORY (one line)

EvolvBI answers analytics questions in SQL, traces and grades every answer through
Arize Phoenix, and then **reads its own failures and rewrites its own prompt** to
get better — with a human approving each change.

---

## SCRIPT

### [0:00 – 0:20] Hook

> *"Every team is shipping AI analytics agents. Almost nobody can tell you whether
> theirs is getting better or quietly drifting. EvolvBI is built to close that loop:
> it traces every answer, grades it, and learns from its own mistakes."*

**[Show]** App loaded; scroll the sidebar quick questions.

---

### [0:20 – 0:45] Q1 — Answer + full transparency

**Type:** `Which mall had the highest revenue last month?`

> *"Ask in plain English; the agent writes and runs BigQuery SQL."*

**[Wait, then expand "Show reasoning (SQL + trace)"]**

> *"Here's the exact SQL — and a direct link to the trace in Arize Phoenix.
> Nothing is hidden."*

**[Click the Phoenix trace link → cut to the Phoenix tab briefly]**

> *"Every call is instrumented end to end."*

---

### [0:45 – 1:10] Q2 — Correctness you can't fake

**Type:** `How many unique customers shopped across the entire portfolio in May 2026?`

> *"This is the kind of question agents quietly get wrong — they sum daily uniques
> and double-count."*

**[Wait, expand Show reasoning]**

> *"EvolvBI uses COUNT(DISTINCT customer_id) — the correct ~19,800, not the inflated
> sum. And because the SQL is right there, you can verify it."*

---

### [1:10 – 1:32] Q3 — A real forecast, not arithmetic

**Type:** `Forecast the next 30 days for Stanford Shopping Center, including the total.`

> *"Forward-looking questions call a BigQuery ML ARIMA_PLUS model — a real forecast
> with confidence intervals, not last-year-times-a-fudge-factor."*

**[Wait]**

> *"Daily projections, a 90% band, and a 30-day total."*

---

### [1:32 – 2:05] The eval pipeline — three judges (TRACK CORE)

**[Cut to the Phoenix tab; show the scored traces]**

> *"Here's where Arize Phoenix earns its place. Every trace is scored by three evals.
> `sql_success` checks the query ran. `sql_relevance` — a Gemini judge — checks the
> answer addressed the question. And `sql_grounding` checks that every number in the
> answer actually traces back to the query results."*

**[Point at a grounding annotation]**

> *"That third one matters: an answer can run cleanly and sound on-topic while the
> headline number is fabricated. Grounding is what catches a hallucinated figure
> the other two would wave through."*

---

### [2:05 – 2:45] THE MONEY SHOT — the agent improves itself

**[Back to the app. Click "Run improvement loop" in the sidebar]**

> *"Now the loop. EvolvBI pulls its failure traces from Phoenix, groups them into
> patterns, and proposes a fix — to its own system prompt."*

**[Wait for proposed edits + the red/green diff to render]**

> *"Here are the proposed edits, rendered as a live diff — red is removed, green is
> the agent's new instruction to itself. A human stays in the loop: I review, then
> approve."*

**[Click "✅ Apply & Rebuild Agent"]**

> *"Applied. The new prompt is persisted to BigQuery — so the improvement survives a
> restart — and the agent is rebuilt on the spot."*

---

### [2:45 – 3:05] Before → after + close

**[Re-ask the seeded failure question, e.g.]** `What was the total portfolio revenue in 2019?`

> *"Same question that failed before — now handled correctly. That's the full loop:
> trace, grade, learn, improve — observable at every step through Arize Phoenix.
> EvolvBI is live on Cloud Run, source on GitHub. Thanks for watching."*

**[Show]** The Cloud Run URL as the final frame.

---

## SHOWCASE QUESTIONS (all verified correct)

- `Which mall had the highest revenue last month?`
- `How many unique customers shopped across the entire portfolio in May 2026?` (COUNT DISTINCT)
- `What is the average basket size per category?` (SUM÷SUM, not average-of-averages)
- `Forecast the next 30 days for Stanford Shopping Center, including the total.`
- `What are the top 5 categories by total sales last quarter?`

## SEED-FAILURE QUESTIONS (use before recording to feed the loop)

- `What was the total portfolio revenue in 2019?` (out of range — no-volunteer rule)
- `What is the average dwell time per shopper?` (no such data — clean decline)

---

## TIPS

- Judges watch at 1.5× — measured, clear pace.
- The improvement loop is the climax. Don't rush it; let the diff render fully on
  screen and read one concrete edit aloud.
- If the loop is slow, narrate: *"it's reading the failure traces from Phoenix now…"*
- Always expand **Show reasoning** at least once — SQL + trace transparency is the pitch.
- Keep the Phoenix tab pre-loaded so cutting to it is instant.

---

## AFTER RECORDING

1. Upload to YouTube (unlisted) or Loom.
2. Paste the URL into the Devpost "Demo Video" field.
