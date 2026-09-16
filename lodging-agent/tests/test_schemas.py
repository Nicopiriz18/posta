"""Contratos y rúbrica."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lodging.schemas import (
    AnalystOutput,
    Brief,
    BriefDraft,
    CheckStatus,
    ConstraintCheck,
    FitRubric,
    PriceEvidence,
    PropertyVerdict,
)


def test_brief_validation_and_computed():
    b = Brief(destination="Madrid", check_in="2026-11-01", check_out="2026-11-04", currency="eur")
    assert b.nights == 3 and b.currency == "EUR" and b.hl == "es"
    with pytest.raises(ValidationError):
        Brief(destination="Madrid", check_in="2026-11-04", check_out="2026-11-04")
    with pytest.raises(ValidationError):
        Brief(destination="Madrid", check_in="2026-11-01", check_out="2026-11-02", foo=1)


def test_brief_roundtrip_ignores_computed_nights():
    b = Brief(destination="Madrid", check_in="2026-11-01", check_out="2026-11-04")
    again = Brief.model_validate_json(b.model_dump_json())
    assert again == b and again.nights == 3


def test_brief_draft_to_brief():
    d = BriefDraft(destination="Lima")
    assert d.missing_required() == ["check_in", "check_out"]
    d = BriefDraft(
        destination="Lima", check_in="2026-12-01", check_out="2026-12-03", soft_preferences=["vista"]
    )
    b = d.to_brief()
    assert b.adults == 2 and b.soft_preferences == ["vista"] and b.language == "es"


@pytest.mark.parametrize(
    "soft,cons,red,over,hard,expected",
    [
        (0, 0, 0, False, False, (50, True)),
        (3, 0, 0, False, False, (74, True)),
        (10, 0, 0, False, False, (90, True)),  # tope +40
        (0, 2, 0, False, False, (30, True)),
        (0, 0, 1, False, False, (25, True)),
        (0, 0, 0, True, False, (35, True)),
        (5, 0, 0, False, True, (20, False)),  # hard violada: tope 20 y no recomendable
        (0, 5, 1, True, False, (0, True)),  # clip 0
    ],
)
def test_rubric(soft, cons, red, over, hard, expected):
    assert (
        FitRubric.compute(soft_confirmed=soft, cons=cons, red_flags=red, over_budget=over, hard_violated=hard)
        == expected
    )


def test_verdict_from_analyst_uses_rubric():
    out = AnalystOutput(
        property_token="t",
        name="Hotel X",
        hard_constraints=[
            ConstraintCheck(constraint="wifi", status=CheckStatus.VIOLATED, evidence="excluded_amenities")
        ],
        soft_preferences=[
            ConstraintCheck(constraint="pileta", status=CheckStatus.CONFIRMED, evidence="amenities")
        ],
        cons=["ruido"],
    )
    v = PropertyVerdict.from_analyst(out)
    assert v.fit_score == 20 and v.recommend is False
    assert v.model_dump()["hard_constraints"][0]["status"] == "violated"


def test_price_display_rule_5():
    p = PriceEvidence(total=176, currency="USD", source="eDreams", verified_at="2026-09-11T18:42:10+00:00")
    assert p.display("es") == "desde USD 176 vía eDreams, verificado a las 18:42"
    assert p.display("en") == "from USD 176 via eDreams, verified at 18:42"
    assert (
        PriceEvidence(total=1, currency="USD", source="x", verified_at="garbage").display().endswith("??:??")
    )


def test_rubric_text_matches_constants():
    text = FitRubric.describe()
    for n in (
        FitRubric.BASE,
        FitRubric.SOFT_PREF_CONFIRMED,
        FitRubric.SOFT_PREF_CAP,
        FitRubric.HARD_VIOLATED_MAX,
    ):
        assert str(n) in text
