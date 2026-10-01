"""Chat API: /ingest, /chat, /feedback, /health (docs/spec.md section 5).

Run from the repository root:
    uv run uvicorn apps.api.main:app --reload --port 8000
"""

import json
import uuid
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from apps.api import telemetry
from apps.api.auth import InvalidToken, TokenVerifier
from apps.api.chat import ChatRequest, ChatService, ChatSettings, ConversationNotFound
from apps.api.config import Settings
from apps.api.db import make_pool, run_migrations
from apps.api.ingest import DocumentIn, UnknownCollege, ingest_document
from apps.api.llm import Models, build_models
from apps.api.retrieval import RetrievalConfig
from apps.api.scope import Claims
from apps.api.tools.student_records import StudentRecords


class Services:
    """Everything the endpoints need, built once at startup (or replaced by tests)."""

    def __init__(
        self,
        settings: Settings,
        models: Models | None = None,
        verifier: TokenVerifier | None = None,
        records: StudentRecords | None = None,
    ):
        self.settings = settings
        run_migrations(settings.database_url)
        self.pool = make_pool(settings.database_url)
        self.models = models or build_models(settings)
        self.verifier = verifier or TokenVerifier(
            settings.jwt_issuer, settings.jwt_audience, jwks_url=settings.jwks_url
        )
        self.records = records or StudentRecords(settings.student_api_url)
        self.chat = ChatService(
            self.pool,
            self.models,
            self.records,
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
    # The widget is embedded on other sites, so browsers need permission to call
    # this API from those origins. Only listed origins get it.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

    def services(request: Request) -> Services:
        return request.app.state.services

    def claims(
        svc: Annotated[Services, Depends(services)],
        authorization: Annotated[str | None, Header()] = None,
    ) -> tuple[Claims | None, str | None]:
        """No Authorization header means anonymous; a bad token is an error, not anonymous."""
        if not authorization:
            return None, None
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(401, "expected 'Authorization: Bearer <token>'")
        try:
            return svc.verifier.verify(token), token
        except InvalidToken as exc:
            raise HTTPException(401, f"invalid token: {exc}") from exc

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
        auth: Annotated[tuple[Claims | None, str | None], Depends(claims)],
    ):
        user_claims, token = auth
        request = ChatRequest(
            question=body.question,
            claims=user_claims,
            token=token,
            conversation_id=str(body.conversation_id) if body.conversation_id else None,
            pending_action_id=str(body.pending_action_id) if body.pending_action_id else None,
        )
        try:
            events = await run_in_threadpool(svc.chat.start, request)
        except ConversationNotFound as exc:
            raise HTTPException(404, "conversation not found") from exc

        def sse():
            # Server-Sent Events: "event: <type>" and "data: <json>" lines, blank line between events.
            try:
                for kind, data in events:
                    yield f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            except Exception:
                yield 'event: error\ndata: {"message": "internal error"}\n\n'
                raise

        return StreamingResponse(
            sse(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

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
