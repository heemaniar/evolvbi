# EvolvBI — Devpost Submission Draft
# Google Cloud Rapid Agent Hackathon · Arize Phoenix Track
# Deadline: June 11, 2026 @ 10 AM PT
#
# Copy-paste each section into the Devpost form fields.

---

## PROJECT NAME
EvolvBI

## TAGLINE (max 60 chars)
A SQL analytics agent that learns from its own mistakes.

## DEMO URL
https://evolvbi-3f3swnt3qq-uc.a.run.app

## VIDEO URL [REQUIRED — upload to YouTube/Loom, paste link]
[PASTE VIDEO URL HERE]

## GITHUB REPO
https://github.com/heemaniar/evolvbi

---

## INSPIRATION

Every company shipping LLM-powered analytics in 2026 has the same problem: the agent hallucinates SQL, writes bad joins, returns wrong answers, and there is no rigorous way to make it better over time. Observability dashboards show you that failures happened. They don't fix them.

As a BI analyst, I've spent years defining quality metrics for campaigns and business processes. EvolvBI applies that same discipline to AI quality — define what "good" looks like, measure it automatically, and close the feedback loop without a human in the critical path.

---

## WHAT IT DOES

EvolvBI is a natural-language SQL assistant with a built-in self-improvement loop:

1. **Ask** — type a business question in plain English
2. **Answer** — SQL Agent writes and runs a BigQuery query, explains results
3. **Trace** — every interaction is recorded in Arize Phoenix Cloud via OpenInference
4. **Evaluate** — a Gemini-as-judge pipeline scores each trace: did the SQL run? did it answer the question?
5. **Improve** — click "Run improvement loop": the Improvement Agent reads failure traces, groups them into patterns, and proposes one targeted prompt edit per pattern
6. **Visualise** — proposed edits rendered as a live red/green diff — the agent rewriting its own instructions in front of you

The "Show Reasoning" panel exposes the exact SQL behind every answer and links to the live Phoenix trace, so there are no black boxes.

---

## HOW I BUILT IT

### Two Google ADK agents
- **SQL Agent** (`agents/sql_agent.py`) — Gemini 2.5 Flash, instrumented with `openinference-instrumentation-google-adk`. Answers natural-language questions by generating and running BigQuery SQL against the `mallpulse_core` warehouse.
- **Improvement Agent** (`agents/improver.py`) — reads annotated failure traces from the Phoenix Python client, groups them into 2–3 failure patterns, proposes one surgical prompt edit per pattern. Runs on demand, never scheduled.

### Evaluation pipeline
`evals/run_evals.py` runs two evals over every root span:
- `sql_success` (CODE annotator) — rule-based: did any tool output contain an error string?
- `sql_relevance` (LLM annotator) — Gemini 2.5 Flash as judge: does the answer directly address the question with specific data?

Both use Vertex AI — no OpenAI, no Anthropic, in compliance with hackathon rules.

### Streamlit UI
- Chat interface with example question buttons
- "Show Reasoning" expander: SQL + Phoenix trace link
- "Run improvement loop" button: triggers Improvement Agent, shows proposed edits
- Prompt diff rendered with Python `difflib.ndiff()` using HTML `<span>` tags — old text in red strikethrough, new text in green

### Infrastructure
BigQuery (`mallpulse_core`) is shared with the companion submission MallPulse — same warehouse, different analytical lens. Deployed on Cloud Run via Cloud Build.

---

## CHALLENGES I RAN INTO

1. **Defining "failure" precisely enough for a consistent LLM judge** — a vague rubric produces noisy scores. Solved by splitting into two independent evals with explicit pass/fail criteria and a one-sentence explanation required from the judge for each verdict.

2. **Phoenix span annotation API changes in v16** — the `SpanAnnotationData` structure moved label/score into a nested `result` dict between Phoenix versions. No public migration guide; had to read source to find it.

3. **Gemini vs. OpenAI in Phoenix evals** — Phoenix's `LLM()` wrapper defaults its Google provider to the Gemini API key path. For Vertex AI (required for hackathon compliance), had to pass `vertexai=True`, `project=`, and `location=` to bypass the API-key check.

4. **Making improvement suggestions surgical, not vague** — early improver runs produced advice like "be more careful." Constrained the agent to propose exactly ONE sentence per pattern and required a worked example trace ID — that forced specificity.

---

## ACCOMPLISHMENTS I'M PROUD OF

- A genuine self-improvement loop, not observability theater — the agent actually changes its own instructions based on what failed
- The prompt diff visualization is the most memorable moment in the demo: judges see the AI rewriting itself, in real time, with a source-diff-style display
- Built as a complementary second submission in five days, sharing the underlying warehouse with MallPulse — proving the architecture is reusable
- Fully Google-stack evaluation: Gemini judges Gemini, no external AI services

---

## WHAT I LEARNED

AI observability is a real engineering discipline emerging in 2026, and the analytical skills I built defining KPIs for marketing campaigns transfer directly. Define what good looks like. Instrument the system. Measure it. Iterate. The loop is identical — the technology is just newer.

The hardest part of building self-improving AI is not the technology; it's defining "better" precisely enough to measure. That's a BI problem, not an engineering problem.

---

## WHAT'S NEXT FOR EVOLVBI

- **Continuous improvement mode** — run the eval + improvement loop nightly; surface proposed edits as GitHub PRs against the prompt file
- **Multi-turn conversation eval** — current evals score single turns; extend to multi-turn diagnostic conversations
- **A/B testing** — apply a proposed prompt edit to half of incoming queries, compare eval scores between versions automatically

---

## BUILT WITH

google-adk, gemini-2.5-flash, vertex-ai, arize-phoenix, openinference, bigquery, cloud-run, streamlit, python, opentelemetry

---

## TRACKS

- [x] Arize Phoenix Track (Phoenix Cloud tracing + LLM-as-judge eval + Phoenix Python client for failure retrieval)

---

## TEAM

Heema Maniar

---

## CHECKLIST BEFORE SUBMITTING

- [ ] Video uploaded to YouTube/Loom — URL added above
- [ ] GitHub repo is PUBLIC at https://github.com/heemaniar/evolvbi
- [ ] GitHub repo "About" section shows MIT license
- [ ] Cloud Run demo URL is live and publicly accessible
- [ ] Devpost form: all required fields filled
- [ ] Submitted before June 11, 2026 @ 10 AM PT
