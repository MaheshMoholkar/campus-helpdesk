"""Chat API: /ingest, /chat, /feedback, /health (docs/spec.md section 5).

Runs beside CampusERP, whose web app forwards /api/helpdesk/* here. From the repository root:
    uv run uvicorn apps.api.main:app --reload --port 8100
"""

import asyncio
import json
import secrets
import uuid
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from apps.api import telemetry
from apps.api.chat import ChatRequest, ChatService, ChatSettings, ConversationNotFound
from apps.api.config import Settings
from apps.api.db import make_pool, run_migrations
from apps.api.erp import (
    ErpClient,
    ErpUnavailable,
    Identity,
    NotLoggedIn,
    credentials_from_cookie_header,
)
from apps.api.ingest import DocumentIn, UnknownCollege, ingest_document
from apps.api.llm import Models, build_models
from apps.api.retrieval import RetrievalConfig

PING_SECONDS = 15.0


class Services:
    """Everything the endpoints need, built once at startup (or replaced by tests)."""

    def __init__(
        self,
        settings: Settings,
        models: Models | None = None,
        erp: ErpClient | None = None,
    ):
        self.settings = settings
        run_migrations(settings.database_url)
        self.pool = make_pool(settings.database_url)
        self.models = models or build_models(settings)
        self.erp = erp or ErpClient(
            settings.erp_api_url,
            settings.erp_session_cookie,
            settings.erp_csrf_cookie,
            settings.erp_csrf_header,
        )
        self.chat = ChatService(
            self.pool,
            self.models,
            self.erp,
            ChatSettings(
                retrieval=RetrievalConfig(
                    settings.retrieval_mode, settings.candidates_k, settings.final_k, settings.as_of_date
                ),
                abstain_threshold=settings.abstain_threshold,
                history_turns=settings.history_turns,
                answer_max_tokens=settings.answer_max_tokens,
            ),
        )

    def close(self) -> None:
        self.pool.close()


def create_app(services_factory=None) -> FastAPI:
    settings = Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        telemetry.init_tracing(settings.otel_exporter_otlp_endpoint)
        services = services_factory() if services_factory else Services(settings)
        app.state.services = services
        yield
        services.close()

    app = FastAPI(title="Campus Helpdesk", version="0.1.0", lifespan=lifespan)
    # Inside CampusERP the browser calls this API on CampusERP's own origin (its web app
    # forwards /api/helpdesk/*), so CORS is not involved. CORS is only for the public
    # widget on other sites, which is anonymous and sends no cookies.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    def services(request: Request) -> Services:
        return request.app.state.services

    def claims(request: Request, svc: Annotated[Services, Depends(services)]) -> Identity | None:
        """Who is asking, according to CampusERP.

        No CampusERP session cookie means anonymous. A session that CampusERP rejects is
        an error, not anonymous. Because the session is a cookie, every POST that carries
        one must also pass CampusERP's double-submit CSRF check: the X-CSRF-Token header
        must echo the CSRF cookie, so another site cannot make the browser chat (or
        confirm an action) on the user's behalf.
        """
        settings = svc.settings
        creds = credentials_from_cookie_header(
            request.headers.get("cookie"), settings.erp_session_cookie, settings.erp_csrf_cookie
        )
        if creds is None:
            return None
        if request.method == "POST":
            sent = request.headers.get(settings.erp_csrf_header)
            if not creds.csrf or not sent or not secrets.compare_digest(sent, creds.csrf):
                raise HTTPException(403, "CSRF token missing or does not match")
        try:
            identity = svc.erp.whoami(creds)
        except NotLoggedIn as exc:
            raise HTTPException(401, "CampusERP session is not valid; please log in again") from exc
        except ErpUnavailable as exc:
            raise HTTPException(503, "CampusERP is unavailable") from exc
        return identity

    @app.get("/health")
    def health(svc: Annotated[Services, Depends(services)]):
        with svc.pool.connection() as conn:
            conn.execute("SELECT 1")
        return {"status": "ok", "ai_backend": svc.settings.ai_backend, "llm": svc.models.llm.model}

    @app.post("/ingest", status_code=201)
    def ingest(
        doc: DocumentIn,
        svc: Annotated[Services, Depends(services)],
        x_api_key: Annotated[str | None, Header()] = None,
    ):
        if x_api_key != svc.settings.ingest_api_key:
            raise HTTPException(401, "missing or wrong X-API-Key")
        with svc.pool.connection() as conn:
            try:
                result = ingest_document(conn, svc.models.embedder, doc)
            except UnknownCollege as exc:
                raise HTTPException(422, f"unknown college: {exc}") from exc
        return result.__dict__

    class ChatIn(BaseModel):
        question: str = Field(min_length=1, max_length=2000)
        conversation_id: uuid.UUID | None = None
        pending_action_id: uuid.UUID | None = None

    @app.post("/chat")
    async def chat(
        body: ChatIn,
        svc: Annotated[Services, Depends(services)],
        identity: Annotated[Identity | None, Depends(claims)],
    ):
        request = ChatRequest(
            question=body.question,
            claims=identity.claims if identity else None,
            creds=identity.creds if identity else None,
            conversation_id=str(body.conversation_id) if body.conversation_id else None,
            pending_action_id=str(body.pending_action_id) if body.pending_action_id else None,
        )
        try:
            events = await run_in_threadpool(svc.chat.start, request)
        except ConversationNotFound as exc:
            raise HTTPException(404, "conversation not found") from exc

        async def sse():
            # Server-Sent Events: "event: <type>" and "data: <json>" lines, blank line between events.
            # The chat flow is blocking (database, models), so it runs in a worker thread and hands
            # events over through a queue. While nothing arrives, a ": ping" comment goes out every
            # PING_SECONDS: CampusERP's Next.js proxy drops a connection after 30 s of silence, and a
            # small local model can think that long before its first token.
            loop = asyncio.get_running_loop()
            queue: asyncio.Queue = asyncio.Queue()
            done = object()

            def produce():
                try:
                    for item in events:
                        loop.call_soon_threadsafe(queue.put_nowait, item)
                except Exception as exc:  # surfaced to the client as an error event
                    loop.call_soon_threadsafe(queue.put_nowait, exc)
                finally:
                    loop.call_soon_threadsafe(queue.put_nowait, done)

            worker = loop.run_in_executor(None, produce)
            try:
                while True:
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=PING_SECONDS)
                    except TimeoutError:
                        yield ": ping\n\n"
                        continue
                    if item is done:
                        break
                    if isinstance(item, Exception):
                        yield 'event: error\ndata: {"message": "internal error"}\n\n'
                        break
                    kind, data = item
                    yield f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            finally:
                await worker

        response = StreamingResponse(
            sse(),
            media_type="text/event-stream",
            # no-transform stops CampusERP's Next.js proxy from gzipping the stream, which would
            # hold every event back until the answer is complete.
            headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
        )
        # A session CampusERP rotated during /auth/me goes back to the browser through
        # CampusERP's proxy, or the browser would be signed out 30 s later.
        for value in identity.set_cookie if identity else []:
            response.headers.append("set-cookie", value)
        return response

    class FeedbackIn(BaseModel):
        turn_id: uuid.UUID
        rating: int = Field(description="1 for thumbs up, -1 for thumbs down")
        comment: str | None = Field(default=None, max_length=1000)

    @app.post("/feedback", status_code=201)
    def feedback(body: FeedbackIn, svc: Annotated[Services, Depends(services)]):
        if body.rating not in (1, -1):
            raise HTTPException(422, "rating must be 1 or -1")
        with svc.pool.connection() as conn:
            turn = conn.execute(
                "SELECT 1 FROM turns WHERE id = %s AND speaker = 'assistant'", (body.turn_id,)
            ).fetchone()
            if not turn:
                raise HTTPException(404, "no assistant turn with that id")
            row = conn.execute(
                "INSERT INTO feedback (turn_id, rating, comment) VALUES (%s, %s, %s) RETURNING id",
                (body.turn_id, body.rating, body.comment),
            ).fetchone()
        return {"id": str(row["id"])}

    return app


app = create_app()
