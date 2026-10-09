# Campus Helpdesk

An AI helpdesk that extends **[CampusERP](https://github.com/MaheshMoholkar/campus-erp)**, my multi-college ERP (Next.js 16 web app, FastAPI/Python API, PostgreSQL with row-level security per college).

Students and staff ask questions from an "Assist" panel inside CampusERP. The helpdesk answers from the college's circulars, policies, placement notices and FAQs, with citations. It also reads the user's own CampusERP records (fees, attendance, results, leave) through CampusERP's API, and requests a bonafide certificate in CampusERP once the student confirms.

Full specification: [docs/spec.md](docs/spec.md).

## How it fits with CampusERP

```
browser ──► CampusERP web app (Next.js) ──/api/helpdesk/*──► Campus Helpdesk API (:8100)
                    │                                              │
                    └──/api/*──► CampusERP API (:8000) ◄───────────┘
                                 /auth/me, fees, attendance, results, leave, bonafide
                                 (called with the user's own session)
```

- **Users, colleges and records come from CampusERP.** The helpdesk has no logins of its own: it forwards the user's CampusERP session to `GET /auth/me` to find out who is asking. CampusERP's permission checks and row-level security then decide what every lookup returns.
- **Its own service, its own database.** The helpdesk is a separate FastAPI service with its own Postgres (documents, embeddings, conversations, metrics), so LLM and pgvector dependencies stay out of CampusERP.
- **CampusERP side:** a few changes, made in that repo: `/api/helpdesk/*` forwarding, the Assist panel, a bonafide-request endpoint, and `institute.code` and `person` added to `/auth/me`.

## What it does

- **Grounded answers with citations.** Hybrid search (pgvector + Postgres full-text, merged with Reciprocal Rank Fusion), an optional reranker, and a streamed answer citing numbered sources with their issue dates.
- **Scope before search.** Anonymous users, students and staff see different documents, per college. The scope filter sits inside the same SQL statement as the search, so a restricted chunk never even leaves the database, let alone reaches the model.
- **Newest circular wins.** Circulars come in versioned series, and the database refuses two live versions of one series. Expired notices are skipped.
- **Abstains** when nothing matches well. It gives the right office's contact instead and logs the question to an unanswered queue.
- **Conversation.** Follow-up rewriting, an intent router, and English / Hindi / Marathi / Hinglish.
- **Tools over CampusERP.** My fees, my attendance and my results for students; my leave balance for staff. A bonafide request with confirm-before-doing. The same tools are also available as an MCP server.
- **Public widget.** The same chat as an anonymous one-script-tag widget for a college's public website.

## Layout

```
apps/api/        helpdesk API: CampusERP client, scope, ingest, retrieval, chat flow, tool loop
apps/mcp_server/ the tools over MCP (stdio)
apps/web/        public React chat page and embeddable widget
data/            documents for CampusERP's demo colleges (alpha, beta)
evals/           golden set (88 cases), judge calibration, runner, baselines
tests/           unit and integration tests
```

## Run it

Needs [uv](https://docs.astral.sh/uv/), Docker, and Node 24 with pnpm. CampusERP runs from its own repo (`make bootstrap seed dev`: API on :8000, web on :3000).

```bash
docker compose up -d postgres                  # the helpdesk's own pgvector, on localhost:5433
cp .env.example .env                           # AI_BACKEND, ERP_API_URL
uv sync
uv run python -m apps.api.cli load-data        # documents (add --fake to work offline)
uv run uvicorn apps.api.main:app --port 8100   # CampusERP's web app forwards /api/helpdesk/* here
```

Then log in to CampusERP at http://localhost:3000 (for example `student@alpha.test`, password `campus-demo-password`) and open Assist.

Only CampusERP's backend is needed for the helpdesk itself: `make up db-bootstrap migrate seed dev-api` in the CampusERP repo. Its web app (`make dev-web`) is needed only for the Assist panel.

**Models.** `AI_BACKEND=ollama` uses `qwen3.5:4b` and `bge-m3` through Ollama (`OLLAMA_BASE_URL`, by default the homelab mini). `AI_BACKEND=fake` uses deterministic word-matching stand-ins: everything runs offline, but the answers are quotes, not generated text.

**API.** `POST /ingest` (header `X-API-Key`), `POST /chat` (Server-Sent Events; a `POST` carrying the CampusERP session must also send `X-CSRF-Token`), `POST /feedback`, `GET /health`. OpenAPI docs are at `http://localhost:8100/docs`.

**MCP.** `CAMPUS_ERP_SESSION=<session cookie> CAMPUS_ERP_CSRF=<csrf cookie> uv run python -m apps.mcp_server.server` (client config example in the file).

## Tests and evals

```bash
uv run pytest -q                       # needs the compose Postgres and CampusERP's API (else those tests skip)
uv run python -m evals.run --tier fast # retrieval + scope leaks, no LLM calls, no CampusERP
uv run python -m evals.run --tier slow # full answers, judge, and tools against CampusERP's API
```

CI runs the fast tier on every push with fake models; tests that need CampusERP skip there. The slow tier runs on a self-hosted runner on the homelab when prompts, retrieval, models or data change. Scope-leak cases must pass 100%. Any other metric fails the build if it drops more than 0.05 below [evals/baseline.json](evals/baseline.json).

## Results

| Run | Scope leaks | recall@5 | Fact match | Faithfulness | Abstain recall | p95 latency |
|-----|-------------|----------|------------|--------------|----------------|-------------|
| Fake models (plumbing only) | 0 / 16 | 0.882 | 0.70 | n/a | 0.10 | n/a |
| `qwen3.5:4b` + `bge-m3` | *pending: homelab offline* | | | | | |

The fake-model row proves the pipeline and the scope filter work; it says nothing about answer quality. Its misses are exactly the cross-language cases, which a word-matching embedder cannot handle and a multilingual one should.

## What didn't work

*Filled in from [docs/experiments.md](docs/experiments.md) as experiments are kept or rejected.*
