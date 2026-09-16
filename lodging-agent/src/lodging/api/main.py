"""FastAPI: `uv run uvicorn lodging.api.main:app --reload`.

Endpoints (ver `docs/api.md`):
- GET  /api/health
- POST /api/threads                    (nuevo hilo de conversación)
- GET  /api/threads                    (lista)
- GET  /api/threads/{id}               (mensajes, brief, reporte)
- POST /api/threads/{id}/messages      (un turno del agente, en background)
- GET  /api/threads/{id}/events        (SSE: historial + en vivo)
- POST /api/runs                       (modo headless: brief confirmado → hilo + investigación)
- GET  /api/runs, GET /api/runs/{id}   (reportes, incluidos los persistidos en runs/)
- /  (frontend/dist si existe)
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from lodging import __version__, config
from lodging.runtime import RunContext
from lodging.schemas import Brief, FinalReport, RunEvent, Zone, brief_date_problems

log = logging.getLogger(__name__)
load_dotenv()

app = FastAPI(title="lodging-agent", version=__version__)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Estado en memoria: hilos de conversación (más los reportes persistidos en runs/)
# ---------------------------------------------------------------------------


class ThreadRecord:
    def __init__(self, ctx: RunContext, *, archived: bool = False):
        self.ctx = ctx
        self.archived = archived  # cargado de runs/: solo lectura
        self.task: asyncio.Task | None = None
        self.fanout: asyncio.Task | None = None
        self.subscribers: list[asyncio.Queue[RunEvent | None]] = []
        self.error: str | None = None

    @property
    def status(self) -> str:
        if self.archived:
            return "archived"
        return "running" if self.ctx.running else "idle"

    def publish(self, ev: RunEvent | None) -> None:
        for q in list(self.subscribers):
            q.put_nowait(ev)


THREADS: dict[str, ThreadRecord] = {}
_AGENT: Any = None


def get_agent() -> Any:
    """Un solo grafo para todo el proceso; el checkpointer separa los hilos por thread_id."""
    global _AGENT
    if _AGENT is None:
        from lodging.agents import build_agent

        _AGENT = build_agent()
    return _AGENT


def set_agent(agent: Any) -> None:
    """Para tests: inyectar un grafo con modelos falsos."""
    global _AGENT
    _AGENT = agent


async def _fanout(rec: ThreadRecord) -> None:
    """Reparte los eventos del RunContext a los suscriptores SSE. Vive mientras viva el hilo."""
    assert rec.ctx.queue is not None
    while True:
        ev = await rec.ctx.queue.get()
        if ev is None:
            rec.publish(None)  # fin de turno
            continue
        rec.publish(ev)


def _new_thread(ctx: RunContext | None = None) -> ThreadRecord:
    ctx = ctx or RunContext()
    ctx.queue = asyncio.Queue()
    rec = ThreadRecord(ctx)
    rec.fanout = asyncio.create_task(_fanout(rec))
    THREADS[ctx.thread_id] = rec
    return rec


async def _run_turn(rec: ThreadRecord, text: str, display_text: str | None = None) -> None:
    from lodging.runner import chat_turn

    try:
        await chat_turn(rec.ctx, get_agent(), text, display_text=display_text)
    except Exception as e:  # noqa: BLE001
        log.exception("turn on %s crashed", rec.ctx.thread_id)
        rec.error = f"{type(e).__name__}: {e}"
        rec.ctx.running = False
        rec.ctx.emit("turn_done", rec.error, data={"status": "failed", "error": rec.error})
        if rec.ctx.queue is not None:
            rec.ctx.queue.put_nowait(None)


def _load_persisted() -> None:
    """Carga reportes de corridas anteriores desde runs/ como hilos archivados (solo lectura)."""
    runs_dir = config.get_settings().runs_dir
    if not runs_dir.exists():
        return
    for d in sorted(runs_dir.iterdir()):
        if d.name in THREADS or not (d / "final.json").exists():
            continue
        try:
            report = FinalReport.model_validate_json((d / "final.json").read_text(encoding="utf-8"))
            ctx = RunContext(report.brief, run_id=report.run_id)
            ctx.created_at = report.generated_at
            ctx.report = report
            ctx.reports = [report]
            ev_file = d / "events.jsonl"
            if ev_file.exists():
                ctx.events = [
                    RunEvent.model_validate_json(line)
                    for line in ev_file.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
            msg_file = d / "messages.json"
            if msg_file.exists():
                ctx.messages = json.loads(msg_file.read_text(encoding="utf-8"))
            THREADS[report.run_id] = ThreadRecord(ctx, archived=True)
        except Exception as e:  # noqa: BLE001
            log.warning("no se pudo cargar %s: %s", d, e)


@app.on_event("startup")
async def _startup() -> None:
    _load_persisted()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def _require_keys() -> None:
    s = config.get_settings()
    missing = [
        k for k, v in (("SERPAPI_KEY", s.serpapi_key), ("ANTHROPIC_API_KEY", s.anthropic_api_key)) if not v
    ]
    if missing:
        raise HTTPException(503, f"Faltan variables en .env: {', '.join(missing)}")


@app.get("/api/health")
async def health() -> dict[str, Any]:
    s = config.get_settings()
    return {
        "ok": True,
        "serpapi_configured": bool(s.serpapi_key),
        "anthropic_configured": bool(s.anthropic_api_key),
        "version": __version__,
    }


def _thread_summary(rec: ThreadRecord) -> dict[str, Any]:
    ctx = rec.ctx
    # último mensaje del agente (el del usuario en modo headless es el brief JSON, no sirve de resumen)
    last = next((m["content"] for m in reversed(ctx.messages) if m["role"] == "assistant"), "")
    if not last and ctx.messages:
        last = ctx.messages[-1]["content"]
    return {
        "thread_id": ctx.thread_id,
        "run_id": ctx.run_id,
        "status": rec.status,
        "created_at": ctx.created_at,
        "destination": ctx.brief.destination if ctx.brief else ctx.draft.destination,
        "check_in": ctx.brief.check_in.isoformat() if ctx.brief else None,
        "check_out": ctx.brief.check_out.isoformat() if ctx.brief else None,
        "has_report": ctx.report is not None,
        "fit_top": ctx.report.ranking[0].verdict.fit_score if ctx.report and ctx.report.ranking else None,
        "results_count": len(ctx.report.ranking) if ctx.report else None,
        "prices_stale": _prices_stale(ctx.report),
        "last_message": last[:120],
    }


def _prices_stale(report: FinalReport | None) -> bool:
    """Los precios de Google Hotels cambian: pasadas STALE_AFTER_HOURS el reporte se marca vencido."""
    if report is None:
        return False
    try:
        generated = datetime.fromisoformat(report.generated_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=UTC)
    return datetime.now(UTC) - generated > timedelta(hours=config.STALE_AFTER_HOURS)


def _thread_detail(rec: ThreadRecord) -> dict[str, Any]:
    ctx = rec.ctx
    return {
        **_thread_summary(rec),
        "messages": ctx.messages,
        "draft": ctx.draft.model_dump(mode="json"),
        "brief": ctx.brief.model_dump(mode="json") if ctx.brief else None,
        "confirmed": ctx.confirmed,
        "report": ctx.report.model_dump(mode="json") if ctx.report else None,
        "error": rec.error,
        "events_count": len(ctx.events),
    }


@app.post("/api/threads", status_code=201)
async def create_thread() -> dict[str, Any]:
    _require_keys()
    rec = _new_thread()
    return _thread_summary(rec)


@app.get("/api/threads")
async def list_threads() -> list[dict[str, Any]]:
    return [
        _thread_summary(r) for r in sorted(THREADS.values(), key=lambda r: r.ctx.created_at, reverse=True)
    ]


def _get_thread(thread_id: str) -> ThreadRecord:
    rec = THREADS.get(thread_id)
    if rec is None:
        raise HTTPException(404, "Hilo no encontrado")
    return rec


@app.get("/api/threads/{thread_id}")
async def get_thread(thread_id: str) -> dict[str, Any]:
    return _thread_detail(_get_thread(thread_id))


@app.delete("/api/threads/{thread_id}", status_code=204)
async def delete_thread(thread_id: str) -> None:
    """Elimina una conversación del historial (memoria + carpeta runs/<id>/ si existe)."""
    rec = _get_thread(thread_id)
    if rec.ctx.running:
        raise HTTPException(409, "El agente todavía está respondiendo en este hilo; esperá a que termine.")
    for t in (rec.task, rec.fanout):
        if t is not None and not t.done():
            t.cancel()
    rec.publish(None)
    THREADS.pop(thread_id, None)
    run_dir = config.get_settings().runs_dir / rec.ctx.run_id
    if run_dir.is_dir():
        shutil.rmtree(run_dir, ignore_errors=True)
    return None


@app.put("/api/threads/{thread_id}/zone")
async def put_zone(thread_id: str, zone: Zone | None = Body(default=None)) -> dict[str, Any]:  # noqa: B008
    """Guarda (o borra, con body null) la zona dibujada en el mapa. Es dato del brief, no del modelo."""
    rec = _get_thread(thread_id)
    if rec.archived:
        raise HTTPException(409, "Este hilo está archivado; abrí una búsqueda nueva.")
    if rec.ctx.running:
        raise HTTPException(409, "El agente todavía está respondiendo en este hilo.")
    ctx = rec.ctx
    ctx.draft = ctx.draft.model_copy(update={"zone": zone})
    if ctx.brief is not None:
        ctx.set_brief(ctx.brief.model_copy(update={"zone": zone}), confirmed=False)
    ctx.emit(
        "brief_saved",
        f"Zona {'guardada: ' + (zone.name or str(len(zone.polygon)) + ' vértices') if zone else 'borrada'}",
        data={
            "draft": ctx.draft.model_dump(mode="json"),
            "brief": ctx.brief.model_dump(mode="json") if ctx.brief else None,
            "status": "ready" if ctx.brief else "incomplete",
            "missing": ctx.draft.missing_required(),
            "confirmed": ctx.confirmed,
        },
    )
    return _thread_detail(rec)


class MessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=8000)


@app.post("/api/threads/{thread_id}/messages", status_code=202)
async def post_message(thread_id: str, msg: MessageIn) -> dict[str, Any]:
    _require_keys()
    rec = _get_thread(thread_id)
    if rec.archived:
        raise HTTPException(409, "Este hilo está archivado (cargado de runs/); abrí uno nuevo.")
    if rec.ctx.running:
        raise HTTPException(409, "El agente todavía está respondiendo en este hilo.")
    rec.error = None
    rec.ctx.running = True
    rec.task = asyncio.create_task(_run_turn(rec, msg.content))
    return {"thread_id": thread_id, "status": "running"}


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _event_stream(
    rec: ThreadRecord, request: Request, *, follow: bool, once: bool = False
) -> AsyncIterator[str]:
    """Historial + en vivo. `follow=False`: solo historial y `done`. `once=True`: cierra al terminar el turno actual."""
    q: asyncio.Queue[RunEvent | None] | None = None
    if follow:
        q = asyncio.Queue()
        rec.subscribers.append(q)  # suscribirse ANTES de replicar el historial para no perder eventos
    try:
        history = list(rec.ctx.events)
        for ev in history:
            yield _sse(ev.type, ev.model_dump(mode="json"))
        if q is None or (once and not rec.ctx.running):
            yield _sse("done", {"status": rec.status})
            return
        sent = len(history)
        while True:
            if await request.is_disconnected():
                return
            try:
                ev = await asyncio.wait_for(q.get(), timeout=15)
            except TimeoutError:
                yield ": keepalive\n\n"
                continue
            if ev is None:
                if once:
                    yield _sse("done", {"status": rec.status})
                    return
                continue  # fin de turno: el evento turn_done ya salió; el stream sigue abierto
            # evitar duplicados con el historial ya enviado
            if sent < len(rec.ctx.events) and rec.ctx.events[sent] is ev:
                sent += 1
            elif ev in history:
                continue
            yield _sse(ev.type, ev.model_dump(mode="json"))
    finally:
        if q is not None and q in rec.subscribers:
            rec.subscribers.remove(q)


@app.get("/api/threads/{thread_id}/events")
async def thread_events(thread_id: str, request: Request, once: bool = False) -> StreamingResponse:
    rec = _get_thread(thread_id)
    return StreamingResponse(
        _event_stream(rec, request, follow=not rec.archived, once=once),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- modo headless / historial de reportes ---------------------------------


@app.post("/api/runs", status_code=202)
async def create_run(brief: Brief) -> dict[str, str]:
    """Arranca una investigación con un brief ya confirmado (sin conversación)."""
    from lodging.runner import headless_message

    _require_keys()
    if problems := brief_date_problems(brief):
        raise HTTPException(422, "; ".join(problems))
    rec = _new_thread()
    rec.ctx.running = True
    shown = (
        f"Brief confirmado: {brief.destination}, {brief.check_in:%d/%m} al {brief.check_out:%d/%m} "
        f"({brief.nights} noches, {brief.adults} adultos). Buscar alojamiento."
    )
    rec.task = asyncio.create_task(_run_turn(rec, headless_message(brief), display_text=shown))
    return {"run_id": rec.ctx.thread_id, "thread_id": rec.ctx.thread_id, "status": "running"}


@app.get("/api/runs")
async def list_runs() -> list[dict[str, Any]]:
    recs = [r for r in THREADS.values() if r.ctx.report is not None or r.ctx.brief is not None]
    return [_thread_summary(r) for r in sorted(recs, key=lambda r: r.ctx.created_at, reverse=True)]


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str) -> dict[str, Any]:
    rec = THREADS.get(run_id) or next((r for r in THREADS.values() if r.ctx.run_id == run_id), None)
    if rec is None:
        raise HTTPException(404, "Corrida no encontrada")
    d = _thread_detail(rec)
    d["status"] = (
        "running"
        if rec.ctx.running
        else ("finished" if rec.ctx.report else ("failed" if rec.error else "idle"))
    )
    return d


@app.delete("/api/runs/{run_id}", status_code=204)
async def delete_run(run_id: str) -> None:
    rec = THREADS.get(run_id) or next((r for r in THREADS.values() if r.ctx.run_id == run_id), None)
    if rec is None:
        raise HTTPException(404, "Corrida no encontrada")
    return await delete_thread(rec.ctx.thread_id)


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str, request: Request, once: bool = False) -> StreamingResponse:
    rec = THREADS.get(run_id) or next((r for r in THREADS.values() if r.ctx.run_id == run_id), None)
    if rec is None:
        raise HTTPException(404, "Corrida no encontrada")
    return StreamingResponse(
        _event_stream(rec, request, follow=not rec.archived, once=once),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Frontend estático (si está buildeado)
# ---------------------------------------------------------------------------

_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"
if _DIST.exists():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        candidate = _DIST / path
        if path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_DIST / "index.html")
