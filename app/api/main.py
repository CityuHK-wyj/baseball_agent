"""HTTP interaction adapter; never an alternate Engine, Judge or clarification interpreter."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import TypeAdapter

from app.api.contracts import (APIError, AgentEvent, ConversationCreated, ConversationSnapshot,
                               ClarificationResponse, RunAccepted, SendMessage)
from app.api.presentation import clarification_view, present
from app.runtime.engine import Engine, IngressError, validate_ingress

_adapter = TypeAdapter(AgentEvent)


@dataclass
class Run:
    conversation_id: str
    id: str = field(default_factory=lambda: uuid4().hex)
    message_id: str = field(default_factory=lambda: uuid4().hex)
    events: list[AgentEvent] = field(default_factory=list)
    done: bool = False

    def emit(self, type: str, payload: dict) -> None:
        event = _adapter.validate_python({
            "id": f"{self.id}:{len(self.events) + 1}", "version": 1, "type": type,
            "run_id": self.id, "message_id": self.message_id,
            "conversation_id": self.conversation_id,
            "timestamp": datetime.now(timezone.utc), "payload": payload,
        })
        self.events.append(event)


def error(status: int, code: str, category: str, message: str):
    raise HTTPException(status_code=status, detail=APIError(code=code, category=category, message=message).model_dump())


def create_app(engine: Engine | None = None) -> FastAPI:
    app = FastAPI(title="Baseball Agent Interaction API", version="1.0.0")
    # Deliberately development-only explicit origin; no wildcard/credentialed browser access.
    import os
    app.add_middleware(CORSMiddleware, allow_origins=[os.getenv("FRONTEND_DEV_ORIGIN", "http://localhost:5173")],
                       allow_methods=["GET", "POST"], allow_headers=["Content-Type", "Last-Event-ID"])
    runs: dict[str, Run] = {}
    ids: set[str] = set()
    busy: set[str] = set()
    engine_lock = asyncio.Lock()
    holder: list[Engine] = []

    def runtime() -> Engine:
        if not holder:
            holder.append(engine or Engine())
        return holder[0]

    def exists(cid: str) -> bool:
        if cid in ids:
            return True
        # Do not create a new empty conversation when probing an arbitrary ID.
        return runtime().conversation.store.load_conversation(cid) is not None

    @app.exception_handler(HTTPException)
    async def http_error(_request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else APIError(
            code="HTTP_ERROR", category="transport", message=str(exc.detail)).model_dump()
        return JSONResponse(status_code=exc.status_code, content={"error": detail})

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request, _exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"error": APIError(
            code="INVALID_REQUEST", category="validation", message="Request does not match the API contract"
        ).model_dump()})

    @app.post("/api/conversations", response_model=ConversationCreated)
    def create_conversation():
        cid = uuid4().hex
        ids.add(cid)
        runtime().conversation.store.save_conversation(runtime().conversation.get(cid))
        return ConversationCreated(conversation_id=cid)

    @app.get("/api/conversations/{cid}", response_model=ConversationSnapshot)
    def get_conversation(cid: str):
        if not exists(cid):
            error(404, "NOT_FOUND", "transport", "Conversation not found")
        state = runtime().conversation.get(cid)
        previous = next((r for r in reversed(list(runs.values())) if r.conversation_id == cid), None)
        pending = state.pending_clarification
        return ConversationSnapshot(
            conversation_id=cid,
            messages=[{"role": m.role, "text": m.text, "goal_id": m.goal_id or ""} for m in state.messages],
            pending_clarification=clarification_view(pending, cid, previous.id if previous else "historical") if pending else None,
            events=[event for run in runs.values() if run.conversation_id == cid for event in run.events],
        )

    async def launch(cid: str, text: str, resolved_id: str = "") -> RunAccepted:
        run = Run(cid)
        runs[run.id] = run
        busy.add(cid)
        run.emit("run.started", {"status": "running"})
        run.emit("message.accepted", {"text": text})
        if resolved_id:
            run.emit("clarification.resolved", {"clarification_id": resolved_id})
        run.emit("agent.status", {"label": "interpreting"})

        async def work():
            try:
                # Engine has mutable LLM usage/Judge state; serialize across conversations.
                async with engine_lock:
                    result = await asyncio.to_thread(runtime().ask, cid, text)
                answer = present(result)
                for artifact in answer.artifacts:
                    run.emit("artifact.created", {"artifact": artifact, "goal_id": answer.goal_id})
                if answer.text and result.projection.clarification is None:
                    run.emit("assistant.delta", {"text": answer.text})
                if result.projection.clarification:
                    run.emit("answer.partial", {"answer": answer})
                    run.emit("clarification.required", {"clarification": clarification_view(
                        result.projection.clarification, cid, run.id)})
                else:
                    run.emit("answer.completed", {"answer": answer})
            except Exception as exc:  # noqa: BLE001 — no provider details/secrets on wire
                run.emit("run.failed", {"code": "RUN_FAILED", "category": "run",
                                        "message": f"Run failed ({type(exc).__name__}). Check server diagnostics."})
            finally:
                run.done = True
                busy.discard(cid)

        asyncio.create_task(work())
        return RunAccepted(message_id=run.message_id, run_id=run.id)

    @app.post("/api/conversations/{cid}/messages", response_model=RunAccepted, status_code=202)
    async def send(cid: str, command: SendMessage):
        if not exists(cid):
            error(404, "NOT_FOUND", "transport", "Conversation not found")
        if cid in busy:
            error(409, "RUN_IN_PROGRESS", "validation", "Wait for the current run")
        if runtime().conversation.get(cid).pending_clarification:
            error(409, "CLARIFICATION_PENDING", "semantic", "Answer the pending clarification first")
        try:
            text = validate_ingress(command.text)
        except IngressError as exc:
            error(422, "INVALID_MESSAGE", "validation", str(exc))
        return await launch(cid, text)

    @app.post("/api/conversations/{cid}/clarifications/{clarid}/responses", response_model=RunAccepted, status_code=202)
    async def respond(cid: str, clarid: str, response: ClarificationResponse):
        if not exists(cid):
            error(404, "NOT_FOUND", "transport", "Conversation not found")
        if cid in busy:
            error(409, "RUN_IN_PROGRESS", "validation", "Wait for the current run")
        pending = runtime().conversation.get(cid).pending_clarification
        if pending is None or clarid != f"clar:{pending.goal_id}":
            error(409, "STALE_CLARIFICATION", "semantic", "Clarification is unknown or already resolved")
        if pending.candidate_id:
            from app.semantic.clarification import target_digest
            owner = runtime().conversation.get(cid).goal(pending.goal_id)
            if (owner is None or not pending.requirement_id or not pending.source_span or
                    pending.target_digest != target_digest(owner, pending.requirement_id,
                                                          pending.candidate_id, pending.source_span)):
                error(409, "STALE_CLARIFICATION", "semantic", "Clarification target has changed")
        if bool(response.selected_option_ids) == bool(response.free_text.strip()):
            error(422, "INVALID_SELECTION", "validation", "Select one option or provide free text")
        if response.selected_option_ids:
            option_id = response.selected_option_ids[0]
            choices = {f"choice:{i}": option for i, option in enumerate(pending.options)}
            if option_id not in choices:
                error(422, "INVALID_SELECTION", "validation", "Option is not in this clarification")
            # Resolve opaque ID server-side; Engine interprets original server-provided text.
            text = choices[option_id]
        else:
            try:
                text = validate_ingress(response.free_text)
            except IngressError as exc:
                error(422, "INVALID_SELECTION", "validation", str(exc))
        return await launch(cid, text, clarid)

    @app.get("/api/runs/{rid}/events")
    async def events(rid: str, last_event_id: str | None = Header(default=None), after: str | None = Query(default=None)):
        run = runs.get(rid)
        if run is None:
            error(404, "NOT_FOUND", "transport", "Run not found (possibly after server restart)")
        cursor = after if after is not None else last_event_id
        index = 0
        if cursor:
            try:
                prefix, count = cursor.rsplit(":", 1)
                index = int(count)
                if prefix != rid or index < 0 or index > len(run.events):
                    raise ValueError()
            except ValueError:
                error(400, "INVALID_CURSOR", "validation", "Invalid event cursor")

        async def stream():
            nonlocal index
            while True:
                while index < len(run.events):
                    event = run.events[index]
                    index += 1
                    yield f"id: {event.id}\nevent: message\ndata: {_adapter.dump_json(event).decode()}\n\n"
                if run.done:
                    return
                await asyncio.sleep(0.15)
                # comment heartbeat keeps intermediaries from timing out
                yield ": keepalive\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app


app = create_app()
