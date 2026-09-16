"""E2E del pipeline con modelos falsos y SerpAPI mockeada: cableado real de deepagents + tools + runner."""

from __future__ import annotations

import asyncio
import json

import pytest
from langchain_core.messages import HumanMessage

from lodging import config
from lodging.agents import build_agent
from lodging.runner import chat_turn, run_brief
from lodging.runtime import RunContext, use_context
from lodging.schemas import FinalReport
from lodging.tools import search_hotels
from tests.fake_models import FakeAnalyst, FakeDiscovery, FakeOrchestrator


@pytest.fixture
def fake_agent():
    orch, disc, ana = FakeOrchestrator(n_analysts=3), FakeDiscovery(), FakeAnalyst()
    agent = build_agent(orchestrator_model=orch, discovery_model=disc, analyst_model=ana)
    return agent, orch, disc, ana


async def test_headless_run_end_to_end(fake_serpapi, brief, make_ctx, fake_agent):
    agent, orch, disc, ana = fake_agent
    ctx = make_ctx()
    ctx.queue = asyncio.Queue()
    result = await run_brief(brief, ctx=ctx, agent=agent, timeout=60)
    report = result.report
    assert isinstance(report, FinalReport)

    # --- reporte ensamblado desde outputs trazables (publicado por la tool publish_report)
    assert ctx.brief == brief and ctx.confirmed
    assert len(report.verdicts) == 3
    assert len(report.ranking) == 3
    assert report.ranking[0].rank == 1 and report.ranking[0].verdict.name == "Nido @ Palermo Soho Square"
    v = report.ranking[0].verdict
    assert v.price and v.price.total == 176 and v.price.source == "eDreams"
    assert v.fit_score == 50 + 8  # base + pileta confirmada (Pool en amenities), sin cons
    assert v.recommend is True
    assert report.summary.startswith("Se analizaron")
    assert any(d.stage == "discovery" for d in report.discarded)
    assert report.stats.analyzed == 3 and report.stats.candidates_found == 20
    assert report.stats.serpapi_calls >= 1 + 3 * 2
    assert not report.stats.timed_out

    # --- assertions de evals pasan sobre esta corrida
    assert result.assertions and result.assertions["ok"], result.assertions

    # --- artefactos en runs/<id>/
    d = ctx.run_dir()
    for name in (
        "brief.json",
        "final.json",
        "tool_outputs.jsonl",
        "assertions.json",
        "events.jsonl",
        "messages.json",
    ):
        assert (d / name).exists(), name
    assert (d / "events.jsonl").read_text(encoding="utf-8").count("\n") == len(ctx.events)

    # --- eventos: brief guardado, fases en orden, subagentes, verdicts, cierre de turno
    types = [e.type for e in ctx.events]
    assert [t for t in types if t != "model"][0] == "brief_saved"
    assert "run_started" in types and "run_finished" in types
    assert types[-1] == "turn_done" and ctx.events[-1].data["status"] == "ok"
    phases = [e.data["phase"] for e in ctx.events if e.type == "phase"]
    assert phases == ["discovery", "selection", "analysis", "consolidation"]
    assert sum(1 for e in ctx.events if e.type == "verdict") == 3
    assert sum(1 for e in ctx.events if e.type == "subagent_started" and e.agent == "property-analyst") == 3
    assert any(e.type == "tool_call" and e.data["tool"] == "search_hotels" for e in ctx.events)
    assert any(e.type == "model" for e in ctx.events)  # callbacks de timing del modelo
    assistant = [e for e in ctx.events if e.type == "assistant"]
    assert assistant and "publiqué el reporte" in assistant[-1].message
    assert ctx.messages[-1]["role"] == "assistant" and "publiqué" in ctx.messages[-1]["content"]

    # --- regla 11: el agente principal no tiene tools de datos; los subagentes sí
    orch_tools = set(orch.seen_tools[0])
    assert {
        "task",
        "write_todos",
        "read_file",
        "ls",
        "save_brief",
        "confirm_brief",
        "publish_report",
    } <= orch_tools
    assert not ({"search_hotels", "get_property_details", "get_property_reviews"} & orch_tools)
    assert "search_hotels" in set(disc.seen_tools[0]) and "get_property_details" not in set(
        disc.seen_tools[0]
    )
    assert {"get_property_details", "get_property_reviews"} <= set(ana.seen_tools[0])
    assert "search_hotels" not in set(ana.seen_tools[0])
    # el archivo de candidatos es JSONL compacto (una línea por candidato)
    assert ctx.candidates_file_content().count("\n") == 20
    assert [e for e in ctx.events if e.type == "message" and "/candidates/hotels.json" in e.message]


async def test_conversation_multi_turn(fake_serpapi, make_ctx, fake_agent):
    agent, *_ = fake_agent
    ctx = make_ctx()

    reply = await chat_turn(ctx, agent, "quiero ir a buenos aires", timeout=60)
    assert "fechas" in reply
    assert ctx.draft.destination == "Buenos Aires, Argentina" and ctx.brief is None
    assert ctx.events[-1].type == "turn_done"

    reply = await chat_turn(ctx, agent, "del 10 al 13 de octubre, somos 2", timeout=60)
    assert "¿Arranco?" in reply
    assert ctx.brief is not None and ctx.brief.nights == 3 and ctx.confirmed is False
    assert ctx.report is None and not ctx.tool_outputs  # no se investigó sin confirmación

    reply = await chat_turn(ctx, agent, "dale", timeout=60)
    assert ctx.confirmed and ctx.report is not None
    assert len(ctx.report.ranking) == 3 and "publiqué" in reply
    assert [m["role"] for m in ctx.messages] == ["user", "assistant"] * 3
    # el checkpointer mantuvo el hilo: el agente vio los 3 mensajes humanos
    state = await agent.aget_state({"configurable": {"thread_id": ctx.thread_id}})
    humans = [m for m in state.values["messages"] if isinstance(m, HumanMessage)]
    assert len(humans) == 3


async def test_data_tools_refuse_without_confirmed_brief(fake_serpapi, brief, make_ctx):
    ctx = make_ctx()
    with use_context(ctx):
        out = await search_hotels.ainvoke({"query": "x"})
        assert out["error"]["type"] == "invalid_params" and "save_brief" in out["error"]["message"]
        ctx.set_brief(brief, confirmed=False)
        out = await search_hotels.ainvoke({"query": "x"})
        assert "confirm" in out["error"]["message"]
        ctx.confirmed = True
        out = await search_hotels.ainvoke({"query": "x"})
        assert out["total_returned"] == 20


async def test_general_purpose_subagent_is_disabled():
    tools_desc = None

    class Spy(FakeOrchestrator):
        def bind_tools(self, tools, **kwargs):  # type: ignore[override]
            nonlocal tools_desc
            for t in tools:
                name = getattr(t, "name", None) or (t.get("name") if isinstance(t, dict) else None)
                if name == "task":
                    tools_desc = getattr(t, "description", None) or (
                        t.get("description") if isinstance(t, dict) else ""
                    )
            return super().bind_tools(tools, **kwargs)

    agent = build_agent(
        orchestrator_model=Spy(), discovery_model=FakeDiscovery(), analyst_model=FakeAnalyst()
    )
    ctx = RunContext()
    with use_context(ctx):
        await chat_turn(ctx, agent, "hola", timeout=30, save=False)
    assert tools_desc is not None
    available = tools_desc.split("Available agent types", 1)[1].split("When using the Task tool", 1)[0]
    assert "general-purpose" not in available
    assert "hotel-discovery" in available and "property-analyst" in available


async def test_timeout_yields_partial_report(fake_serpapi, brief, make_ctx):
    class SlowAnalyst(FakeAnalyst):
        def decide(self, messages):
            import time

            time.sleep(0.4)
            return super().decide(messages)

    agent = build_agent(
        orchestrator_model=FakeOrchestrator(n_analysts=2),
        discovery_model=FakeDiscovery(),
        analyst_model=SlowAnalyst(),
    )
    ctx = make_ctx()
    result = await run_brief(brief, ctx=ctx, agent=agent, timeout=0.6)
    assert result.report.stats.timed_out is True
    assert any("límite" in c for c in result.report.caveats)
    assert ctx.events[-1].type == "turn_done" and ctx.events[-1].data["status"] == "timeout"
    assert any(e.type == "run_finished" for e in ctx.events)


async def test_tool_error_is_degraded_not_fatal(fake_serpapi, brief, make_ctx):
    import httpx

    agent = build_agent(
        orchestrator_model=FakeOrchestrator(n_analysts=1),
        discovery_model=FakeDiscovery(),
        analyst_model=FakeAnalyst(),
    )
    fake_serpapi.fail_next = httpx.Response(500, json={"error": "boom"})
    ctx = make_ctx()
    result = await run_brief(brief, ctx=ctx, agent=agent, timeout=60)
    assert ctx.events[-1].type == "turn_done"
    final = json.loads((ctx.run_dir() / "final.json").read_text(encoding="utf-8"))
    assert final["run_id"] == ctx.run_id and result.report.run_id == ctx.run_id


async def test_caps_in_config():
    assert config.MAX_ANALYSTS == 12 and config.MAX_CANDIDATES == 30 and config.RUN_TIMEOUT_SECONDS >= 600
