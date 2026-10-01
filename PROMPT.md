# Campus Helpdesk — session prompt

I want to write the spec document for "Campus Helpdesk", then build it. It is a personal portfolio project for learning applied AI. The spec below was agreed in an earlier session; items marked **(assumed)** were defaults proposed to me that I have not confirmed yet — ask me about those first. Discuss and confirm before writing the document or any code.

## What it is

A standalone AI assistant that answers campus queries for an invented university with several colleges. Backend-first with a thin chat UI. Built in levels, each one demoable and measured. Not limited to anything that existed at my job.

## Who uses it

- **Anonymous:** general campus, admissions and placement questions. Sees only documents tagged `public`.
- **Student (logged in):** the above plus their own college's circulars and their own data. Sees `public` and `student` documents for their college and for `all`.
- **Staff (logged in):** the above plus staff circulars. Sees `public`, `student` and `staff` documents for their college and for `all`.

## Knowledge

- Document types: circulars, policies (exam, fee, hostel), placement notices, FAQs.
- Every document is tagged with college, audience, category, issue date and expiry.
- Circulars can be college-specific and get superseded: the newest wins, expired ones are skipped, and the issue date is shown in every citation.
- All data is invented: about 150 generated documents and mock student records.

## Capabilities

**v1 = levels 1 to 4 (assumed):**

1. **Grounded answers:** ingest → hybrid search (vector + keyword) → rerank → answer with citations. The scope filter is applied *before* search so restricted documents can never reach the model. On a weak match the assistant abstains, gives the relevant office contact, and logs the question to an "unanswered" queue.
2. **Evals and observability:** golden set including scope-leak and refusal cases; retrieval recall@k; answer faithfulness via LLM-as-judge; CI gate; tracing; cost and latency per answer.
3. **Conversation:** rewrite follow-up questions using history; intent router (FAQ / personal / action / out of scope); English first, then Hindi, Marathi and Hinglish, answering in the user's language **(assumed)**.
4. **Tools:** "my fee due", "my attendance", "my timetable" against a mock student API inside this repo **(assumed)**; one confirm-before-doing action (request a bonafide certificate); the same tools exposed as an MCP server.

**Roadmap, levels 5 to 7:**

5. Scanned circulars, tables, photo upload.
6. Prompt-injection test set, PII redaction in logs, rate limits, prompt caching, small/large model routing.
7. Real WhatsApp channel; admin console (unanswered queue, feedback turned into new eval cases, content-gap report).

## Interfaces

- `POST /ingest` — document + tags.
- `POST /chat` — question and optional login token → streamed answer with citations.
- `POST /feedback` — thumbs up/down.
- `GET /health`.
- **UI:** a single React chat component with WhatsApp-style bubbles, web only for now **(assumed)**. Later it may be embedded in an ERP or behind an "assist" button on a landing page; that is undecided and must not affect the backend.

## Quality bar

- Every change to prompts or retrieval runs the golden set in CI.
- Scope-leak cases must pass 100%; other thresholds are set after the first baseline run.
- The README carries the eval table, cost per answer, p95 latency and a "what didn't work" section.

## Boundaries

- Fully standalone: no link to my other project (Campus Support) and no dependency on any ERP.
- No real institution data of any kind.
- Write retrieval and the tool loop by hand first; bring in a framework only if it is later justified.

## Stack

- Python, FastAPI, PostgreSQL + pgvector, React for the chat UI, GitHub Actions.
- AI stack (model provider, embedding model, reranker, tracing tool) is not chosen yet: propose options with trade-offs and let me decide.
- I have a homelab (Postgres, Redis, S3, Ollama for local models, reachable via a `lab` CLI) that can be used for dev infrastructure.

## What I want from this session

1. Ask me to confirm or correct the four **(assumed)** items.
2. Ask where the spec document should live (I was considering `docs/spec.md` in this repo) and in what format.
3. Then write the document: the agreed spec above, followed by architecture, data model, eval plan and build milestones. Propose each new section for my approval before writing it in full.
4. Do not start coding until I say so.

## How I like to work

- Discuss before building; propose, then wait for my go.
- Short, direct replies.
- Keep token use modest: no multi-agent fan-outs unless I ask.
- I am a .NET/React developer with 3 years' experience; Python/FastAPI and applied AI are newer to me, so briefly explain non-obvious choices as you go.
