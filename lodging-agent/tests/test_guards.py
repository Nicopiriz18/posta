"""Guardas aprendidas de corridas reales: fechas pasadas, cap de búsquedas, grupos grandes, borrado de hilos."""

from __future__ import annotations

from datetime import date, timedelta

import httpx

from lodging import config
from lodging.agents.control_tools import save_brief
from lodging.runtime import RunContext, use_context
from lodging.schemas import Brief, brief_date_problems
from lodging.tools import search_hotels


def test_brief_date_problems():
    today = date(2026, 9, 12)
    past = Brief(destination="x", check_in="2026-01-05", check_out="2026-01-17")
    assert any("in the past" in p for p in brief_date_problems(past, today))
    ok = Brief(destination="x", check_in="2027-01-05", check_out="2027-01-17")
    assert brief_date_problems(ok, today) == []
    long = Brief(destination="x", check_in="2027-01-05", check_out="2027-03-05")
    assert any("30" in p for p in brief_date_problems(long, today))


async def test_save_brief_rejects_past_dates():
    ctx = RunContext()
    with use_context(ctx):
        out = await save_brief.ainvoke(
            {"destination": "Praia do Rosa", "check_in": "2020-01-05", "check_out": "2020-01-17"}
        )
    assert out["status"] == "incomplete" and any("past" in m for m in out["missing"])
    assert (
        ctx.brief is None
        and ctx.events[-1].type == "brief_saved"
        and ctx.events[-1].data["status"] == "incomplete"
    )
    start = date.today() + timedelta(days=40)
    with use_context(ctx):
        out = await save_brief.ainvoke(
            {"check_in": start.isoformat(), "check_out": (start + timedelta(days=3)).isoformat()}
        )
    assert out["status"] == "ready" and ctx.brief is not None and ctx.brief.destination == "Praia do Rosa"


async def test_failed_searches_do_not_consume_cap(fake_serpapi, run_ctx):
    for _ in range(3):
        fake_serpapi.fail_next = httpx.Response(400, json={"error": "`check_in_date` cannot be in the past."})
        out = await search_hotels.ainvoke({"query": "x"})
        assert "error" in out
    assert run_ctx.search_calls == 0 and run_ctx.search_attempts == 3
    out = await search_hotels.ainvoke({"query": "Hotels in Palermo"})
    assert out["total_returned"] == 20 and run_ctx.search_calls == 1
    # tope duro de intentos: 2 x MAX_SEARCH_CALLS_PER_RUN
    run_ctx.search_attempts = 2 * config.MAX_SEARCH_CALLS_PER_RUN
    out = await search_hotels.ainvoke({"query": "otra"})
    assert "Too many failed searches" in out["error"]["message"]


async def test_large_groups_switch_to_vacation_rentals(fake_serpapi, make_ctx):
    brief = Brief(
        destination="Praia do Rosa", check_in="2027-01-05", check_out="2027-01-17", adults=9, gl="br"
    )
    ctx = make_ctx(brief)
    with use_context(ctx):
        out = await search_hotels.ainvoke({"query": "Praia do Rosa"})
    assert "error" not in out
    assert fake_serpapi.calls[-1]["vacation_rentals"] == "true"
    assert "switched to vacation rentals" in (out.get("note") or "")


async def test_suggest_replies_emits_event():
    from lodging.agents.control_tools import suggest_replies

    ctx = RunContext()
    with use_context(ctx):
        out = await suggest_replies.ainvoke(
            {"options": ["4 adultos", "2 adultos + 2 chicos", "", "a", "b", "c"]}
        )
    assert out["options"] == ["4 adultos", "2 adultos + 2 chicos", "a", "b"]  # máximo 4, sin vacíos
    ev = ctx.events[-1]
    assert ev.type == "suggestions" and ev.data["options"][0] == "4 adultos"


async def test_verdict_gets_facts_from_tool_output(fake_serpapi, brief, make_ctx):
    from lodging.agents import build_agent
    from lodging.runner import run_brief
    from tests.fake_models import FakeAnalyst, FakeDiscovery, FakeOrchestrator

    agent = build_agent(
        orchestrator_model=FakeOrchestrator(n_analysts=2),
        discovery_model=FakeDiscovery(),
        analyst_model=FakeAnalyst(),
    )
    ctx = make_ctx()
    result = await run_brief(brief, ctx=ctx, agent=agent, timeout=60)
    v = result.report.ranking[0].verdict
    assert v.facts is not None
    assert v.facts.address and "Thames" in v.facts.address
    assert v.facts.offers and v.facts.offers[0].source == "eDreams" and v.facts.offers[0].total == 176
    assert v.facts.overall_rating == 4.2 and v.facts.reviews_count == 150
    # el ranking incluye TODAS las recomendadas (sin top fijo) y respeta el cap
    assert len(result.report.ranking) == 2 == sum(1 for x in result.report.verdicts if x.recommend)
    assert result.assertions and result.assertions["ok"], result.assertions


def test_prices_stale_flag():
    from datetime import UTC, datetime, timedelta

    from lodging.api.main import _prices_stale
    from lodging.schemas import FinalReport

    def report(age_hours: float) -> FinalReport:
        b = Brief(destination="x", check_in="2027-01-05", check_out="2027-01-08")
        gen = (datetime.now(UTC) - timedelta(hours=age_hours)).isoformat()
        return FinalReport(
            run_id="r", generated_at=gen, language="es", brief=b, summary="s", ranking=[], verdicts=[]
        )

    assert _prices_stale(None) is False
    assert _prices_stale(report(1)) is False
    assert _prices_stale(report(config.STALE_AFTER_HOURS + 1)) is True


async def test_details_and_reviews_stop_retrying_after_cap(fake_serpapi, run_ctx):
    from lodging.tools import get_property_details, get_property_reviews
    from lodging.tools.hotels import MAX_ATTEMPTS_PER_PROPERTY

    for _ in range(MAX_ATTEMPTS_PER_PROPERTY):
        fake_serpapi.fail_next = httpx.Response(
            400, json={"error": "Total number of travelers should be less than or equal to 6"}
        )
        out = await get_property_details.ainvoke({"property_token": "tok"})
        assert "error" in out
    calls_before = len(fake_serpapi.calls)
    out = await get_property_details.ainvoke({"property_token": "tok"})
    assert "Do not retry" in out["error"]["message"]
    assert len(fake_serpapi.calls) == calls_before  # no llegó a SerpAPI
    # otra propiedad no está afectada
    out = await get_property_details.ainvoke({"property_token": "otra"})
    assert "error" not in out
    # reviews tienen su propio contador
    out = await get_property_reviews.ainvoke({"property_token": "tok"})
    assert "error" not in out


async def test_details_use_vacation_rentals_for_large_groups(fake_serpapi, make_ctx):
    from lodging.tools import get_property_details

    brief = Brief(
        destination="Praia do Rosa", check_in="2027-01-05", check_out="2027-01-17", adults=9, gl="br"
    )
    ctx = make_ctx(brief)
    with use_context(ctx):
        out = await get_property_details.ainvoke({"property_token": "tok"})
    assert "error" not in out and fake_serpapi.calls[-1]["vacation_rentals"] == "true"


def test_candidates_file_carries_price_verified_at(run_ctx):
    from lodging.schemas import Candidate

    run_ctx.add_candidates(
        [Candidate(property_token="t1", name="X", price_verified_at="2026-09-12T10:00:00+00:00")]
    )
    assert '"price_verified_at":"2026-09-12T10:00:00+00:00"' in run_ctx.candidates_file_content()


def test_repair_token_by_name(run_ctx):
    from lodging.runner import repair_token
    from lodging.schemas import AnalystOutput, Candidate

    run_ctx.add_candidates([Candidate(property_token="REAL", name="Casa Benedet's", price_verified_at="x")])
    out = AnalystOutput(property_token="MANGLED", name="casa benedet's")
    fixed = repair_token(run_ctx, out)
    assert fixed.property_token == "REAL" and run_ctx.warnings
    assert (
        repair_token(run_ctx, AnalystOutput(property_token="ZZ", name="desconocida")).property_token == "ZZ"
    )
