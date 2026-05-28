# 🔬 EvolvBI

**Self-Improving SQL Analytics for Retail Data**

EvolvBI is a natural-language SQL assistant that gets smarter by learning from its own mistakes. Ask questions about mall performance data, watch every interaction traced through Arize Phoenix, and trigger a self-improvement loop that reads failure patterns and proposes targeted prompt edits — live, in the browser.

**🔗 Live demo:** https://evolvbi-3f3swnt3qq-uc.a.run.app

---

## What it does

| Feature | Detail |
|---|---|
| **Natural-language SQL** | Ask questions in plain English; agent writes and runs BigQuery SQL |
| **Show Reasoning** | Expand any answer to see the exact SQL query + Phoenix trace link |
| **Eval pipeline** | Every trace scored by Gemini-as-judge: `sql_success` + `sql_relevance` |
| **Improvement Loop** | One click reads failure traces, groups patterns, proposes prompt edits |
| **Live prompt diff** | Proposed edits rendered as red/green diff — the agent rewriting its own instructions |

---

## Architecture

![EvolvBI Architecture](architecture.png)

The self-improvement loop:
1. User asks a question → SQL Agent queries BigQuery
2. Every interaction traced to **Arize Phoenix Cloud**
3. `evals/run_evals.py` scores each trace with a Gemini LLM-as-judge
4. Failed traces accumulate in Phoenix with labels and explanations
5. **Improvement Agent** reads failures, groups into patterns, proposes one prompt edit per pattern
6. User reviews diff and applies — next run is better

---

## Tech stack

| Layer | Technology |
|---|---|
| **AI agents** | Google ADK 1.34, Gemini 2.5 Flash (Vertex AI) |
| **Tracing** | Arize Phoenix Cloud (OpenInference instrumentation) |
| **Evaluation** | Gemini 2.5 Flash as LLM judge (`sql_success`, `sql_relevance`) |
| **Data warehouse** | BigQuery (`mallpulse_core` — shared with MallPulse) |
| **UI** | Streamlit — chat + reasoning panel + prompt diff |
| **Deployment** | Cloud Run |

---

## Dataset

Shared with [MallPulse](https://github.com/heemaniar/mallpulse): Istanbul Mall Customer Shopping dataset, 267K+ transactions across 10 Istanbul malls (2021–present), augmented with synthetic tenants, lease data, weather, and Turkish holidays.

---

## Running locally

**Prerequisites:** Python 3.11+, `gcloud` CLI authenticated, GCP project with BigQuery + Vertex AI enabled, Arize Phoenix Cloud account.

```bash
git clone https://github.com/heemaniar/evolvbi.git
cd evolvbi

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Fill in: PHOENIX_API_KEY, PHOENIX_COLLECTOR_ENDPOINT, GOOGLE_CLOUD_PROJECT

gcloud auth application-default login

streamlit run streamlit_app.py
# → http://localhost:8501
```

---

## Running the eval pipeline

```bash
# Score all traces in Phoenix with Gemini-as-judge
python evals/run_evals.py

# Run the improvement loop (also triggered from the Streamlit UI)
python agents/improver.py
```

---

## Deploying to Cloud Run

```bash
bash deploy_cloudrun.sh
```

---

## Project structure

```
evolvbi/
├── agents/
│   ├── sql_agent.py      # SQL agent + Phoenix instrumentation
│   └── improver.py       # Reads failures, proposes prompt edits
├── evals/
│   └── run_evals.py      # sql_success (CODE) + sql_relevance (LLM) evals
├── tools/
│   └── bigquery_tools.py # query_warehouse tool
├── streamlit_app.py      # Chat UI + reasoning panel + improvement loop
├── app.py                # Headless CLI runner (3 test questions)
├── deploy_cloudrun.sh    # Cloud Run one-command deploy
├── Dockerfile
└── requirements.txt
```

---

## Hackathon

Built for the **[Google Cloud Rapid Agent Hackathon](https://googlecloudagents.devpost.com/)** — Arize Phoenix track.

**Submission deadline:** June 11, 2026

---

## License

MIT — see [LICENSE](LICENSE)
