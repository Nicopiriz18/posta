"""Assertions de evals sobre reportes sintéticos y sobre corridas con modelos falsos."""

from __future__ import annotations

from lodging.agents import build_agent
from lodging.evals.assertions import check_run
from lodging.runner import run_brief
from lodging.schemas import (
    AnalystOutput,
    CheckStatus,
    ConstraintCheck,
    FinalReport,
    PriceEvidence,
    PropertyVerdict,
    RankedProperty,
)
from tests.fake_models import FakeAnalyst, FakeDiscovery, FakeOrchestrator

TOOL_OUTPUTS = [
    {
        "tool": "search_hotels",
        "args": {},
        "output": {
            "candidates": [
                {
                    "property_token": "A",
                    "name": "Hotel A",
                    "total_rate": 300.0,
                    "price_source": "Booking.com",
                    "price_verified_at": "2026-09-11T10:00:00+00:00",
                },
                {
                    "property_token": "B",
                    "name": "Hostel B",
                    "total_rate": 90.0,
                    "price_source": "Hostelworld",
                    "price_verified_at": "2026-09-11T10:00:00+00:00",
                },
            ]
        },
    },
    {
        "tool": "get_property_reviews",
        "args": {},
        "output": {
            "property_token": "A",
            "reviews": [
                {"text": "the staff was incredibly kind and the breakfast buffet was generous every morning"}
            ],
        },
    },
]


def _verdict(**over) -> PropertyVerdict:
    base = dict(
        property_token="A",
        name="Hotel A",
        hard_constraints=[
            ConstraintCheck(constraint="wifi", status=CheckStatus.CONFIRMED, evidence="amenities")
        ],
        price=PriceEvidence(
            total=300.0, currency="USD", source="Booking.com", verified_at="2026-09-11T10:00:00+00:00"
        ),
        reviews_summary="La mayoría de las reseñas destacan la atención del personal.",
        summary="Opción sólida para la zona.",
    )
    base.update(over)
    return PropertyVerdict.from_analyst(
        AnalystOutput(**{k: v for k, v in base.items() if k not in ("fit_score", "recommend")})
    )


def _report(brief, verdicts, ranking=None) -> FinalReport:
    ranking = (
        ranking
        if ranking is not None
        else [
            RankedProperty(rank=i + 1, rationale="Buena opción por precio y ubicación.", verdict=v)
            for i, v in enumerate(verdicts)
            if v.recommend
        ]
    )
    return FinalReport(
        run_id="r",
        generated_at="now",
        language="es",
        brief=brief,
        summary="Se analizaron dos propiedades en la zona pedida.",
        ranking=ranking,
        verdicts=verdicts,
    )


def test_all_checks_pass_on_clean_report(brief):
    res = check_run(_report(brief, [_verdict()]), TOOL_OUTPUTS)
    assert res["ok"], res


def test_untraceable_price_and_token(brief):
    v = _verdict(
        price=PriceEvidence(
            total=250.0, currency="USD", source="Booking.com", verified_at="2026-09-11T10:00:00+00:00"
        )
    )
    res = check_run(_report(brief, [v]), TOOL_OUTPUTS)
    assert not res["checks"]["prices_traceable"]["ok"]
    v2 = _verdict(property_token="ZZZ")
    res = check_run(_report(brief, [v2]), TOOL_OUTPUTS)
    assert not res["checks"]["properties_traceable"]["ok"]
    assert not res["checks"]["prices_traceable"]["ok"]


def test_verbatim_review_detected(brief):
    v = _verdict(
        reviews_summary="Un huésped dijo: the staff was incredibly kind and the breakfast buffet was generous"
    )
    res = check_run(_report(brief, [v]), TOOL_OUTPUTS)
    assert not res["checks"]["no_verbatim_reviews"]["ok"]


def test_hard_violation_ranked_is_flagged(brief):
    v = _verdict(
        hard_constraints=[
            ConstraintCheck(constraint="wifi", status=CheckStatus.VIOLATED, evidence="excluded")
        ]
    )
    assert v.recommend is False
    forced = [RankedProperty(rank=1, rationale="x", verdict=v)]
    res = check_run(_report(brief, [v], ranking=forced), TOOL_OUTPUTS)
    assert not res["checks"]["hard_constraints_respected"]["ok"]
    # y tampoco rankeada: pasa
    res = check_run(_report(brief, [v], ranking=[]), TOOL_OUTPUTS)
    assert res["checks"]["hard_constraints_respected"]["ok"]


def test_language_mismatch_and_unknowns(brief):
    v = _verdict(
        summary="This is a solid option for the area with the best staff in the city and it is great."
    )
    rep = _report(brief, [v])
    rep.summary = (
        "We analyzed two properties in the requested area and this is the best of them for the trip."
    )
    rep.ranking[0].rationale = "It is the best option for the price and the location is great for the trip."
    res = check_run(rep, TOOL_OUTPUTS)
    assert not res["checks"]["language"]["ok"]
    v2 = _verdict(pros=["Desayuno incluido"], unknowns=["desayuno incluido"])
    res = check_run(_report(brief, [v2]), TOOL_OUTPUTS)
    assert not res["checks"]["unknowns_classified"]["ok"]
    v3 = _verdict(soft_preferences=[ConstraintCheck(constraint="vista", status=CheckStatus.CONFIRMED)])
    res = check_run(_report(brief, [v3]), TOOL_OUTPUTS)
    assert not res["checks"]["unknowns_classified"]["ok"]


def test_rubric_tampering_detected(brief):
    v = _verdict()
    v.fit_score = 99
    res = check_run(_report(brief, [v]), TOOL_OUTPUTS)
    assert not res["checks"]["fit_score_rubric"]["ok"]


async def test_verbatim_review_caught_in_real_pipeline(fake_serpapi, brief, make_ctx):
    agent = build_agent(
        orchestrator_model=FakeOrchestrator(n_analysts=1),
        discovery_model=FakeDiscovery(),
        analyst_model=FakeAnalyst(verbatim_review=True),
    )
    ctx = make_ctx(brief)
    result = await run_brief(brief, ctx=ctx, agent=agent, timeout=60)
    assert result.assertions is not None
    assert not result.assertions["checks"]["no_verbatim_reviews"]["ok"]
    assert result.assertions["checks"]["prices_traceable"]["ok"]
