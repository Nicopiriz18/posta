"""Tests del backend (sin red): hilos de conversación con modelos falsos, SSE, modo headless."""

from __future__ import annotations

import json

import httpx
import pytest
from asgi_lifespan import LifespanManager

from lodging.api import main as api
from tests.fake_models import FakeAnalyst, FakeDiscovery, FakeOrchestrator

BRIEF = {
    "destination": "Buenos Aires, Argentina",
    "area_preferences": ["Palermo"],
    "check_in": "2026-10-10",
    "check_out": "2026-10-13",
    "adults": 2,
    "dealbreakers": ["hostel"],
    "hard_constraints": ["wifi"],
    "soft_preferences": ["pileta"],
}


@pytest.fixture
async def client(monkeypatch, tmp_path, fake_serpapi):
    from lodging import config
    from lodging.agents import build_agent

    monkeypatch.setenv("LODGING_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("SERPAPI_KEY", "test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    config.reset_settings()
    api.THREADS.clear()
    api.set_agent(
        build_agent(
            orchestrator_model=FakeOrchestrator(n_analysts=2),
            discovery_model=FakeDiscovery(),
            analyst_model=FakeAnalyst(),
        )
    )
    async with LifespanManager(api.app):
        transport = httpx.ASGITransport(app=api.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    api.set_agent(None)
    config.reset_settings()


async def _collect_until(
    client: httpx.AsyncClient, url: str, stop_event: str, occurrence: int = 1
) -> list[tuple[str, dict]]:
    """Lee el SSE (con `once=1`: el servidor cierra al fin del turno; ASGITransport no soporta streams infinitos)
    y corta en la N-ésima aparición de `stop_event` (el historial incluye turnos anteriores)."""
    events: list[tuple[str, dict]] = []
    sep = "&" if "?" in url else "?"
    async with client.stream("GET", url + sep + "once=1") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        buf = ""
        async for chunk in resp.aiter_text():
            buf += chunk
            while "\n\n" in buf:
                block, buf = buf.split("\n\n", 1)
                if block.startswith(":"):
                    continue
                lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
                events.append((lines.get("event", ""), json.loads(lines.get("data", "{}"))))
            if sum(1 for e in events if e[0] == stop_event) >= occurrence:
                break
    return events


async def test_health(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["serpapi_configured"] is True and r.json()["anthropic_configured"] is True


async def test_thread_conversation_and_sse(client):
    r = await client.post("/api/threads")
    assert r.status_code == 201
    tid = r.json()["thread_id"]

    # turno 1
    r = await client.post(f"/api/threads/{tid}/messages", json={"content": "quiero ir a buenos aires"})
    assert r.status_code == 202
    events = await _collect_until(client, f"/api/threads/{tid}/events", "turn_done")
    types = [e[0] for e in events]
    assert "brief_saved" in types and "assistant" in types and types[-2:] == ["turn_done", "done"]
    assert not any(t == "run_started" for t in types)

    # doble envío mientras corre → 409 (simulado: el hilo no está corriendo ahora, así que pasa)
    r = await client.get(f"/api/threads/{tid}")
    body = r.json()
    assert body["status"] == "idle" and body["draft"]["destination"] == "Buenos Aires, Argentina"
    assert body["messages"][0]["role"] == "user" and body["messages"][1]["role"] == "assistant"

    # turno 2 y 3
    r = await client.post(
        f"/api/threads/{tid}/messages", json={"content": "del 10 al 13 de octubre, somos 2"}
    )
    assert r.status_code == 202
    await _collect_until(client, f"/api/threads/{tid}/events", "turn_done", occurrence=2)
    r = await client.post(f"/api/threads/{tid}/messages", json={"content": "dale"})
    assert r.status_code == 202
    events = await _collect_until(client, f"/api/threads/{tid}/events", "turn_done", occurrence=3)
    types = [e[0] for e in events]
    assert "run_started" in types and sum(1 for t in types if t == "verdict") == 2
    finished = next(e for e in events if e[0] == "run_finished")
    report = finished[1]["data"]["report"]
    assert report["ranking"][0]["verdict"]["price"]["source"] == "eDreams"
    # el reporte llega antes del mensaje final del agente y del cierre del turno
    last_assistant = len(types) - 1 - types[::-1].index("assistant")
    assert types.index("run_finished") < last_assistant
    assert types[-2:] == ["turn_done", "done"]
    r = await client.get(f"/api/threads/{tid}")
    body = r.json()
    assert body["status"] == "idle" and body["confirmed"] is True and body["report"]["ranking"]
    assert len(body["messages"]) == 6

    r = await client.get("/api/threads")
    assert r.json()[0]["thread_id"] == tid and r.json()[0]["has_report"] is True
    r = await client.get("/api/runs")
    assert r.json()[0]["fit_top"] is not None


async def test_headless_run_endpoint(client):
    r = await client.post("/api/runs", json=BRIEF)
    assert r.status_code == 202
    run_id = r.json()["run_id"]
    events = await _collect_until(client, f"/api/runs/{run_id}/events", "turn_done")
    types = [e[0] for e in events]
    assert "run_finished" in types
    r = await client.get(f"/api/runs/{run_id}")
    assert r.json()["status"] == "finished" and len(r.json()["report"]["ranking"]) == 2


async def test_guards(client, monkeypatch):
    from lodging import config

    r = await client.post(
        "/api/runs", json={"destination": "x", "check_in": "2026-10-10", "check_out": "2026-10-09"}
    )
    assert r.status_code == 422
    r = await client.get("/api/threads/nope")
    assert r.status_code == 404
    r = await client.post("/api/threads/nope/messages", json={"content": "hola"})
    assert r.status_code == 404
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    config.reset_settings()
    r = await client.post("/api/threads")
    assert r.status_code == 503 and "ANTHROPIC_API_KEY" in r.json()["detail"]


async def test_archived_runs_are_loaded_read_only(client, tmp_path, monkeypatch):
    # una corrida termina y queda en runs/; un proceso nuevo la carga como hilo archivado
    r = await client.post("/api/runs", json=BRIEF)
    run_id = r.json()["run_id"]
    await _collect_until(client, f"/api/runs/{run_id}/events", "turn_done")
    api.THREADS.clear()
    api._load_persisted()
    assert run_id in api.THREADS and api.THREADS[run_id].archived
    r = await client.get(f"/api/threads/{run_id}")
    assert r.json()["status"] == "archived" and r.json()["report"]["ranking"]
    r = await client.post(f"/api/threads/{run_id}/messages", json={"content": "hola"})
    assert r.status_code == 409
    events = await _collect_until(client, f"/api/threads/{run_id}/events", "done")
    assert events[-1][1]["status"] == "archived"


async def test_sse_once_closes_immediately_when_idle(client):
    r = await client.post("/api/threads")
    tid = r.json()["thread_id"]
    events = await _collect_until(client, f"/api/threads/{tid}/events", "done")
    assert events == [("done", {"status": "idle"})]


async def test_delete_thread_and_run_dir(client):
    from lodging import config

    r = await client.post("/api/runs", json=BRIEF)
    run_id = r.json()["run_id"]
    await _collect_until(client, f"/api/runs/{run_id}/events", "turn_done")
    run_dir = config.get_settings().runs_dir / run_id
    assert run_dir.is_dir()
    r = await client.delete(f"/api/threads/{run_id}")
    assert r.status_code == 204
    assert run_id not in api.THREADS and not run_dir.exists()
    r = await client.get(f"/api/threads/{run_id}")
    assert r.status_code == 404
    r = await client.delete("/api/threads/nope")
    assert r.status_code == 404
    # un hilo vacío también se puede borrar (no tiene carpeta)
    tid = (await client.post("/api/threads")).json()["thread_id"]
    assert (await client.delete(f"/api/runs/{tid}")).status_code == 204


async def test_put_zone_updates_draft_and_brief(client):
    tid = (await client.post("/api/threads")).json()["thread_id"]
    zone = {
        "name": "Palermo Soho",
        "polygon": [
            {"lat": -34.5835, "lng": -58.4355},
            {"lat": -34.5835, "lng": -58.4245},
            {"lat": -34.5925, "lng": -58.4245},
        ],
        "buffer_m": 200,
    }
    r = await client.put(f"/api/threads/{tid}/zone", json=zone)
    assert r.status_code == 200
    body = r.json()
    assert body["draft"]["zone"]["name"] == "Palermo Soho" and body["brief"] is None
    ev = await _collect_until(client, f"/api/threads/{tid}/events", "done")
    assert any(e[0] == "brief_saved" and "Zona guardada" in e[1]["message"] for e in ev)
    # polígono inválido (2 vértices) → 422
    r = await client.put(
        f"/api/threads/{tid}/zone", json={"polygon": [{"lat": 0, "lng": 0}, {"lat": 1, "lng": 1}]}
    )
    assert r.status_code == 422
    # borrar
    r = await client.put(f"/api/threads/{tid}/zone", json=None)
    assert r.status_code == 200 and r.json()["draft"]["zone"] is None
