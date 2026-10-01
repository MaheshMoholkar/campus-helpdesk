# Campus Helpdesk

An AI assistant that answers campus questions for **Navrang University**, an invented university with three colleges. It answers from circulars, policies, placement notices and FAQs, with citations. It also looks up a logged-in student's own fees, attendance and timetable, and requests a bonafide certificate after the student confirms.

A personal portfolio project for learning applied AI. All data is invented. Full specification: [docs/spec.md](docs/spec.md).

## What it does

- **Grounded answers with citations.** Hybrid search (pgvector + Postgres full-text, merged with Reciprocal Rank Fusion), optional reranker, streamed answer citing numbered sources with their issue dates.
- **Scope before search.** Anonymous users, students and staff see different documents. The scope filter sits inside the same SQL statement as the search, so a restricted chunk is never loaded, let alone sent to the model.
- **Newest circular wins.** Circulars come in versioned series; the database refuses two live versions of one series. Expired notices are skipped.
- **Abstains** when nothing matches well, gives the right office's contact, and logs the question to an unanswered queue.
- **Conversation.** Follow-up rewriting, an intent router, English / Hindi / Marathi / Hinglish.
- **Tools.** Fee due, attendance, timetable against a mock student API; a confirm-before-doing bonafide request; the same tools as an MCP server.
- **Chat UI** as a page and as a one-script-tag embeddable widget.

## Layout

```
apps/api/          chat API (FastAPI): scope, ingest, retrieval, chat flow, tool loop
apps/student_api/  mock ERP: login tokens (RS256 + JWKS), fees, attendance, timetable
apps/mcp_server/   the level 4 tools over MCP (stdio)
apps/web/          React chat component; page build + widget build
data/              the invented university: plan, 35 documents, student records
evals/             golden set (87 cases), judge calibration, runner, baselines
tests/             unit + integration tests (47)
```

## Run it

Needs [uv](https://docs.astral.sh/uv/), Docker, and Node 24 with pnpm.

```bash
docker compose up -d postgres                 # pgvector on localhost:5433
cp .env.example .env                          # then pick AI_BACKEND (see below)
uv sync
uv run python -m apps.api.cli load-data       # reference data + documents (add --fake offline)

uv run uvicorn apps.student_api.main:app --port 8001
uv run uvicorn apps.api.main:app --port 8000
cd apps/web && pnpm install && pnpm dev       # http://localhost:5173
```

Demo logins (password `password`): `S1001`–`S1004` students, `T2001`–`T2003` staff.

**Models.** `AI_BACKEND=ollama` uses `qwen3.5:4b` and `bge-m3` via Ollama (`OLLAMA_BASE_URL`, default the homelab mini). `AI_BACKEND=fake` uses deterministic word-matching stand-ins: everything runs offline, but answers are quotes, not generated text. With the homelab, `lab up` provisions Postgres and writes `.env.lab`, which the apps read automatically.

**API.** `POST /ingest` (header `X-API-Key`), `POST /chat` (SSE stream; optional `Authorization: Bearer <token>`), `POST /feedback`, `GET /health`. OpenAPI docs at `http://localhost:8000/docs`.

**MCP.** `STUDENT_TOKEN=<token> uv run python -m apps.mcp_server.server` (config example in the file).

## Tests and evals

```bash
uv run pytest -q                                   # needs the compose Postgres
uv run python -m evals.run --tier fast             # retrieval + scope leaks, no LLM calls
uv run python -m evals.run --tier slow             # full answers, judge, tools
uv run python -m evals.run --tier slow --log "add reranker"   # also logs to docs/experiments.md
```

CI runs the fast tier on every push with fake models. The slow tier runs on a self-hosted runner on the homelab when prompts, retrieval, models or data change. Scope-leak cases must pass 100%; other metrics fail the build if they drop more than 0.05 below [evals/baseline.json](evals/baseline.json).

## Results

| Run | Scope leaks | recall@5 | Fact match | Faithfulness | Abstain recall | p95 latency |
|-----|-------------|----------|------------|--------------|----------------|-------------|
| Fake models (plumbing only) | 0 / 16 | 0.885 | 0.702 | n/a | 0.10 | n/a |
| `qwen3.5:4b` + `bge-m3` | *pending: homelab offline* | | | | | |

The fake-model row proves the pipeline and the scope filter; it says nothing about answer quality. Its misses are exactly the cross-language cases (Hindi and Marathi recall@5 0.6 and 0.4), which a word-matching embedder cannot handle and a multilingual one should. Real numbers come from the first run against the homelab. Held-out cases (20) are reported separately once there is a real baseline.

## What didn't work

*Filled in from [docs/experiments.md](docs/experiments.md) as experiments are kept or rejected.*
