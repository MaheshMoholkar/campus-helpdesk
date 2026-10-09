"""The /chat flow (docs/spec.md section 10.2), as a generator of events.

The HTTP layer turns the events into Server-Sent Events; the eval runner
consumes the same generator directly. Event types, in order:

    meta       {conversation_id, turn_id, language, intent, rewritten_question}
    delta      {text}                      zero or more, the answer as it is written
    citations  {items: [{n, slug, title, issue_date}]}
    done       {outcome, turn_id, pending_action_id?, retrieved?}
"""

import re
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import psycopg
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from apps.api import messages, prompts, telemetry
from apps.api.conversation import classify_intent, detect_language, rewrite_question
from apps.api.erp import ErpClient, ErpCredentials, ErpUnavailable, NotLoggedIn
from apps.api.llm import Models
from apps.api.llm.base import Usage
from apps.api.retrieval import RetrievalConfig, RetrievalResult, retrieve
from apps.api.scope import Claims, Scope, build_scope
from apps.api.tools.loop import run_tool_loop

PENDING_ACTION_TTL = timedelta(minutes=10)
_CITATION = re.compile(r"\[(\d+)\]")


class ConversationNotFound(Exception):
    pass


@dataclass
class ChatRequest:
    question: str
    claims: Claims | None = None
    creds: ErpCredentials | None = None  # the user's CampusERP session, forwarded by the tools
    conversation_id: str | None = None
    pending_action_id: str | None = None


@dataclass
class ChatSettings:
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)
    abstain_threshold: float = 0.0
    history_turns: int = 6
    answer_max_tokens: int = 800
    include_retrieval_debug: bool = False  # the eval runner wants the slugs at every stage


Event = tuple[str, dict]


class ChatService:
    def __init__(self, pool: ConnectionPool, models: Models, erp: ErpClient, settings: ChatSettings) -> None:
        self._pool = pool
        self._models = models
        self._erp = erp
        self._settings = settings

    def start(self, request: ChatRequest) -> Iterator[Event]:
        """Check the conversation before streaming begins, so a bad id can still be a 404.

        Returns the event generator; nothing else runs until it is iterated.
        """
        if request.conversation_id:
            with self._pool.connection() as conn:
                self._load_conversation(conn, request)
        return self.stream(request)

    # --- main flow ----------------------------------------------------------

    def stream(self, request: ChatRequest) -> Iterator[Event]:
        started = time.perf_counter()
        scope = build_scope(request.claims)
        usage = Usage()
        language = detect_language(request.question)
        root = telemetry.get_tracer().start_span("chat")
        root.set_attribute("openinference.span.kind", "CHAIN")
        root.set_attribute("input.value", request.question)
        root.set_attribute("user.role", scope.role)

        try:
            with self._pool.connection() as conn:
                conversation_id = self._conversation(conn, request, scope)
                history = self._history(conn, conversation_id)
                self._add_turn(conn, conversation_id, "user", request.question, language=language)
                conn.commit()

                if request.pending_action_id:
                    yield from self._confirm_action(
                        conn, request, conversation_id, language, usage, started, root
                    )
                    return

                rewritten = rewrite_question(self._models.llm, history, request.question, usage)
                intent = classify_intent(self._models.llm, rewritten, usage)
                self._annotate_user_turn(conn, conversation_id, rewritten, intent)
                conn.commit()

                turn_id = str(uuid.uuid4())
                yield (
                    "meta",
                    {
                        "conversation_id": conversation_id,
                        "turn_id": turn_id,
                        "language": language,
                        "intent": intent,
                        "rewritten_question": rewritten,
                    },
                )

                if intent == "out_of_scope":
                    reply = messages.text("out_of_scope", language)
                    yield from self._fixed_reply(
                        conn, conversation_id, turn_id, reply, "refused", usage, started, root
                    )
                elif intent in ("personal", "action"):
                    yield from self._personal(
                        conn,
                        request,
                        scope,
                        history,
                        rewritten,
                        conversation_id,
                        turn_id,
                        language,
                        usage,
                        started,
                        root,
                    )
                else:
                    yield from self._grounded(
                        conn, scope, rewritten, conversation_id, turn_id, language, usage, started, root
                    )
        finally:
            root.end()

    # --- branches -----------------------------------------------------------

    def _grounded(self, conn, scope, question, conversation_id, turn_id, language, usage, started, root):
        span = telemetry.child_span(root, "retrieve", "RETRIEVER")
        result = retrieve(
            conn, scope, question, self._models.embedder, self._models.reranker, self._settings.retrieval
        )
        telemetry.set_retrieved_documents(span, result.final)
        span.end()
        conn.commit()

        debug = (
            {"retrieved": result.stages, "sources": [c.text for c in result.final]}
            if self._settings.include_retrieval_debug
            else {}
        )
        timings = {"retrieval_ms": result.retrieval_ms, "rerank_ms": result.rerank_ms}

        if not result.final or (result.top_score or 0.0) < self._settings.abstain_threshold:
            yield from self._abstain(
                conn,
                scope,
                question,
                conversation_id,
                turn_id,
                language,
                result,
                usage,
                started,
                root,
                debug,
                timings,
            )
            return

        sources = [
            {"title": c.title, "issue_date": c.issue_date.isoformat(), "text": c.text} for c in result.final
        ]
        llm_messages = prompts.answer_messages(question, sources)
        llm_span = telemetry.child_span(root, "answer", "LLM")

        # Hold back the first few characters: if the model says NO_ANSWER, that must
        # become an abstention, not be streamed to the user.
        pieces: list[str] = []
        first_token_ms = None
        held = ""
        released = False
        for piece in self._models.llm.stream(llm_messages, usage, self._settings.answer_max_tokens):
            if first_token_ms is None:
                first_token_ms = int((time.perf_counter() - started) * 1000)
            pieces.append(piece)
            if released:
                yield ("delta", {"text": piece})
                continue
            held += piece
            head = held.lstrip()
            if head.startswith(prompts.NO_ANSWER):
                break  # the model declined; this becomes an abstention below
            if prompts.NO_ANSWER.startswith(head):
                continue  # could still turn into NO_ANSWER; keep holding
            released = True
            yield ("delta", {"text": held})
        answer = "".join(pieces).strip()
        telemetry.set_llm_io(llm_span, self._models.llm.model, llm_messages, answer)
        llm_span.end()

        if not answer or answer.startswith(prompts.NO_ANSWER):
            yield from self._abstain(
                conn,
                scope,
                question,
                conversation_id,
                turn_id,
                language,
                result,
                usage,
                started,
                root,
                debug,
                timings,
            )
            return
        if not released:
            yield ("delta", {"text": held})  # a reply shorter than the marker itself

        cited = []
        for number in dict.fromkeys(int(n) for n in _CITATION.findall(answer)):
            if 1 <= number <= len(result.final):
                chunk = result.final[number - 1]
                cited.append((number, chunk))
        yield (
            "citations",
            {
                "items": [
                    {"n": n, "slug": c.slug, "title": c.title, "issue_date": c.issue_date.isoformat()}
                    for n, c in cited
                ]
            },
        )

        self._add_turn(conn, conversation_id, "assistant", answer, turn_id=turn_id)
        self._record_answer(
            conn,
            turn_id,
            "answered",
            usage,
            started,
            root,
            result.top_score,
            first_token_ms=first_token_ms,
            **timings,
        )
        with conn.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO citations (turn_id, position, document_id, chunk_id, score)"
                " VALUES (%s, %s, %s, %s, %s)",
                [(turn_id, n, c.document_id, c.chunk_id, c.score) for n, c in cited],
            )
        conn.commit()
        yield ("done", {"outcome": "answered", "turn_id": turn_id, **debug})

    def _abstain(
        self,
        conn,
        scope,
        question,
        conversation_id,
        turn_id,
        language,
        result: RetrievalResult,
        usage,
        started,
        root,
        debug,
        timings,
    ):
        category = result.final[0].category if result.final else "general"
        office = self._find_office(conn, scope.college, category)
        name, contact = messages.office_contact(office)
        reply = messages.text("abstain", language, office=name, contact=contact)
        yield ("delta", {"text": reply})
        yield ("citations", {"items": []})

        self._add_turn(conn, conversation_id, "assistant", reply, turn_id=turn_id)
        self._record_answer(conn, turn_id, "abstained", usage, started, root, result.top_score, **timings)
        user_turn = conn.execute(
            "SELECT id FROM turns WHERE conversation_id = %s AND speaker = 'user' ORDER BY seq DESC LIMIT 1",
            (conversation_id,),
        ).fetchone()
        conn.execute(
            "INSERT INTO unanswered (turn_id, question, language, role, college_code, top_score, office_id)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                user_turn["id"],
                question,
                language,
                scope.role,
                scope.college,
                result.top_score,
                office["id"] if office else None,
            ),
        )
        conn.commit()
        yield ("done", {"outcome": "abstained", "turn_id": turn_id, **debug})

    def _personal(
        self,
        conn,
        request,
        scope: Scope,
        history,
        question,
        conversation_id,
        turn_id,
        language,
        usage,
        started,
        root,
    ):
        if scope.role == "anonymous" or request.creds is None:
            reply = messages.text("login_required", language)
            yield from self._fixed_reply(
                conn, conversation_id, turn_id, reply, "refused", usage, started, root
            )
            return
        if request.claims.person_kind not in ("student", "staff"):
            reply = messages.text("students_only", language)
            yield from self._fixed_reply(
                conn, conversation_id, turn_id, reply, "refused", usage, started, root
            )
            return

        span = telemetry.child_span(root, "tools", "AGENT")
        try:
            result = run_tool_loop(
                self._models.llm,
                self._erp,
                request.creds,
                request.claims.person_kind,
                history,
                question,
                usage,
            )
        except (ErpUnavailable, NotLoggedIn):
            span.end()
            reply = messages.text("records_unavailable", language)
            yield from self._fixed_reply(conn, conversation_id, turn_id, reply, "tool", usage, started, root)
            return
        span.set_attribute("tools.called", ",".join(result.tools_called))
        span.end()

        if result.proposed_action:
            action_id = conn.execute(
                "INSERT INTO pending_actions (conversation_id, user_sub, tool_name, arguments, expires_at)"
                " VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (
                    conversation_id,
                    scope.user_sub,
                    result.proposed_action.tool_name,
                    Jsonb(result.proposed_action.arguments),
                    datetime.now(UTC) + PENDING_ACTION_TTL,
                ),
            ).fetchone()["id"]
            reply = messages.text(
                "confirm_bonafide", language, purpose=result.proposed_action.arguments["purpose"]
            )
            yield ("delta", {"text": reply})
            yield ("citations", {"items": []})
            self._add_turn(conn, conversation_id, "assistant", reply, turn_id=turn_id)
            self._record_answer(conn, turn_id, "tool", usage, started, root, None)
            conn.commit()
            yield (
                "done",
                {
                    "outcome": "tool",
                    "turn_id": turn_id,
                    "pending_action_id": str(action_id),
                    "tools_called": result.tools_called,
                },
            )
            return

        reply = result.text or messages.text("records_unavailable", language)
        yield ("delta", {"text": reply})
        yield ("citations", {"items": []})
        self._add_turn(conn, conversation_id, "assistant", reply, turn_id=turn_id)
        self._record_answer(conn, turn_id, "tool", usage, started, root, None)
        conn.commit()
        yield ("done", {"outcome": "tool", "turn_id": turn_id, "tools_called": result.tools_called})

    def _confirm_action(self, conn, request, conversation_id, language, usage, started, root):
        turn_id = str(uuid.uuid4())
        yield (
            "meta",
            {
                "conversation_id": conversation_id,
                "turn_id": turn_id,
                "language": language,
                "intent": "action",
                "rewritten_question": request.question,
            },
        )

        # Lock the row so a double click cannot submit the request twice.
        action = conn.execute(
            "SELECT * FROM pending_actions WHERE id = %s AND conversation_id = %s FOR UPDATE",
            (_as_uuid(request.pending_action_id), conversation_id),
        ).fetchone()
        valid = (
            action is not None
            and request.claims is not None
            and action["user_sub"] == request.claims.sub
            and action["status"] == "pending"
            and action["expires_at"] > datetime.now(UTC)
        )
        if not valid:
            conn.rollback()
            reply = messages.text("action_invalid", language)
            yield from self._fixed_reply(
                conn, conversation_id, turn_id, reply, "refused", usage, started, root
            )
            return

        try:
            created = self._erp.request_bonafide(request.creds, action["arguments"]["purpose"])
        except (ErpUnavailable, NotLoggedIn):
            conn.rollback()
            reply = messages.text("records_unavailable", language)
            yield from self._fixed_reply(conn, conversation_id, turn_id, reply, "tool", usage, started, root)
            return
        if not isinstance(created, dict) or "error" in created or "id" not in created:
            conn.rollback()  # CampusERP said no (e.g. not linked to a student); nothing was submitted
            reply = messages.text("action_failed", language)
            yield from self._fixed_reply(conn, conversation_id, turn_id, reply, "tool", usage, started, root)
            return
        conn.execute(
            "UPDATE pending_actions SET status = 'executed', executed_at = now() WHERE id = %s",
            (action["id"],),
        )
        conn.commit()
        reply = messages.text("action_done", language, reference=str(created["id"]))
        yield from self._fixed_reply(conn, conversation_id, turn_id, reply, "tool", usage, started, root)

    def _fixed_reply(self, conn, conversation_id, turn_id, reply, outcome, usage, started, root):
        yield ("delta", {"text": reply})
        yield ("citations", {"items": []})
        self._add_turn(conn, conversation_id, "assistant", reply, turn_id=turn_id)
        self._record_answer(conn, turn_id, outcome, usage, started, root, None)
        conn.commit()
        yield ("done", {"outcome": outcome, "turn_id": turn_id})

    # --- storage helpers ----------------------------------------------------

    def _load_conversation(self, conn: psycopg.Connection, request: ChatRequest) -> dict:
        row = conn.execute(
            "SELECT id, user_sub FROM conversations WHERE id = %s", (_as_uuid(request.conversation_id),)
        ).fetchone()
        user_sub = request.claims.sub if request.claims else None
        # Someone else's conversation looks exactly like a missing one.
        if row is None or row["user_sub"] != user_sub:
            raise ConversationNotFound(request.conversation_id)
        return row

    def _conversation(self, conn, request: ChatRequest, scope: Scope) -> str:
        if request.conversation_id:
            return str(self._load_conversation(conn, request)["id"])
        return str(
            conn.execute(
                "INSERT INTO conversations (user_sub, role, college_code) VALUES (%s, %s, %s) RETURNING id",
                (scope.user_sub, scope.role, scope.college),
            ).fetchone()["id"]
        )

    def _history(self, conn, conversation_id: str) -> list[dict]:
        rows = conn.execute(
            "SELECT speaker, text FROM turns WHERE conversation_id = %s ORDER BY seq DESC LIMIT %s",
            (conversation_id, self._settings.history_turns),
        ).fetchall()
        return list(reversed(rows))

    @staticmethod
    def _add_turn(conn, conversation_id, speaker, text, turn_id=None, language=None):
        conn.execute(
            "INSERT INTO turns (id, conversation_id, seq, speaker, text, language)"
            " VALUES (coalesce(%s, gen_random_uuid()), %s,"
            "         (SELECT coalesce(max(seq), 0) + 1 FROM turns WHERE conversation_id = %s), %s, %s, %s)",
            (turn_id, conversation_id, conversation_id, speaker, text, language),
        )

    @staticmethod
    def _annotate_user_turn(conn, conversation_id, rewritten, intent):
        conn.execute(
            "UPDATE turns SET rewritten_question = %s, intent = %s WHERE id ="
            " (SELECT id FROM turns WHERE conversation_id = %s AND speaker = 'user' ORDER BY seq DESC LIMIT 1)",
            (rewritten, intent, conversation_id),
        )

    def _record_answer(
        self,
        conn,
        turn_id,
        outcome,
        usage: Usage,
        started,
        root,
        top_score,
        retrieval_ms=None,
        rerank_ms=None,
        first_token_ms=None,
    ):
        conn.execute(
            """
            INSERT INTO answers (turn_id, outcome, model, prompt_version, prompt_tokens, completion_tokens,
                                 retrieval_ms, rerank_ms, first_token_ms, total_ms, top_score, trace_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                turn_id,
                outcome,
                self._models.llm.model,
                prompts.ANSWER_VERSION,
                usage.prompt_tokens,
                usage.completion_tokens,
                retrieval_ms,
                rerank_ms,
                first_token_ms,
                int((time.perf_counter() - started) * 1000),
                top_score,
                telemetry.trace_id_of(root),
            ),
        )
        root.set_attribute("chat.outcome", outcome)

    @staticmethod
    def _find_office(conn, college: str | None, category: str) -> dict | None:
        """Most specific office first: the user's college and the topic, then university-wide."""
        return conn.execute(
            """
            SELECT id, name, email, phone, hours FROM offices
            WHERE college_code IN (%(college)s, 'all') AND category IN (%(category)s, 'general')
            ORDER BY (college_code = %(college)s) DESC, (category = %(category)s) DESC
            LIMIT 1
            """,
            {"college": college or "all", "category": category},
        ).fetchone()


def _as_uuid(value: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def collect(events: Iterator[Event]) -> dict:
    """Run a chat to completion and gather its events into one result (used by evals and tests)."""
    result: dict = {"answer": "", "citations": []}
    for kind, data in events:
        if kind == "delta":
            result["answer"] += data["text"]
        elif kind == "citations":
            result["citations"] = data["items"]
        else:
            result.update(data)
    return result
