"""Zona dibujada en el mapa: geometría en código, filtro de candidatos, prompt sin coordenadas, API y assertion."""

from __future__ import annotations

import json

from lodging.runtime import RunContext
from lodging.schemas import Brief, Candidate, LatLng, Zone
from lodging.tools.geo import ZoneGeometry, zone_status

# Cuadrado de ~1 km de lado alrededor de Palermo Soho (aprox. -34.588, -58.430)
SQUARE = Zone(
    name="Palermo Soho",
    polygon=[
        LatLng(lat=-34.5835, lng=-58.4355),
        LatLng(lat=-34.5835, lng=-58.4245),
        LatLng(lat=-34.5925, lng=-58.4245),
        LatLng(lat=-34.5925, lng=-58.4355),
    ],
)


def test_geometry_inside_outside_buffer_and_area():
    g = ZoneGeometry(SQUARE)
    assert 0.8 < g.area_km2 < 1.2
    inside, dist = g.status(-34.588, -58.430)
    assert inside and dist == 0
    outside, dist = g.status(-34.600, -58.430)  # ~830 m al sur del borde
    assert not outside and 700 < dist < 950
    tolerant = Zone(name="x", polygon=SQUARE.polygon, buffer_m=1000)
    assert ZoneGeometry(tolerant).status(-34.600, -58.430)[0] is True
    assert zone_status(SQUARE, None, None) == (None, None)
    lat, lng = g.centroid
    assert abs(lat + 34.588) < 0.001 and abs(lng + 58.43) < 0.001


def _brief(zone=SQUARE) -> Brief:
    return Brief(destination="Buenos Aires", check_in="2026-10-10", check_out="2026-10-13", zone=zone)


def test_candidates_outside_zone_are_discarded_by_code():
    ctx = RunContext(_brief())
    inside = Candidate(
        property_token="IN", name="Hotel Adentro", latitude=-34.588, longitude=-58.430, price_verified_at="x"
    )
    outside = Candidate(
        property_token="OUT", name="Hotel Lejos", latitude=-34.62, longitude=-58.45, price_verified_at="x"
    )
    unknown = Candidate(property_token="NOGPS", name="Casa sin coordenadas", price_verified_at="x")
    added, dropped = ctx.add_candidates([inside, outside, unknown])
    assert added == 2 and dropped == 1
    assert list(ctx.candidates) == ["IN", "NOGPS"]
    assert ctx.candidates["IN"].zone_inside is True and ctx.candidates["IN"].zone_distance_m == 0
    assert ctx.candidates["NOGPS"].zone_inside is None  # ausencia de evidencia, no descarte
    d = ctx.zone_discards[0]
    assert d.property_token == "OUT" and "Palermo Soho" in d.reason and " m del borde" in d.reason
    assert '"zone_inside":true' in ctx.candidates_file_content()


def test_prompt_json_hides_coordinates():
    data = json.loads(_brief().to_prompt_json())
    assert data["zone"] == {"name": "Palermo Soho", "vertices": 4, "buffer_m": 0}
    assert "polygon" not in json.dumps(data)
    # sin zona, la clave no aparece
    assert "zone" not in json.loads(_brief(zone=None).to_prompt_json())


async def test_save_brief_cannot_set_zone_and_keeps_map_zone():
    from lodging.agents.control_tools import save_brief
    from lodging.runtime import use_context

    ctx = RunContext()
    ctx.draft = ctx.draft.model_copy(update={"zone": SQUARE})
    with use_context(ctx):
        out = await save_brief.ainvoke(
            {
                "destination": "Buenos Aires",
                "check_in": "2026-10-10",
                "check_out": "2026-10-13",
                "zone": {
                    "name": "inventada",
                    "polygon": [{"lat": 0, "lng": 0}, {"lat": 0, "lng": 1}, {"lat": 1, "lng": 1}],
                },
            }
        )
    assert out["status"] == "ready"
    assert ctx.brief is not None and ctx.brief.zone is not None and ctx.brief.zone.name == "Palermo Soho"


def test_zone_assertion_flags_ranked_outside():
    from lodging.evals.assertions import check_run
    from lodging.schemas import AnalystOutput, FinalReport, PropertyFacts, PropertyVerdict, RankedProperty

    v = PropertyVerdict.from_analyst(
        AnalystOutput(property_token="t", name="Hotel Lejos", summary="Lejos de todo.")
    )
    v.facts = PropertyFacts(zone_inside=False, zone_distance_m=830)
    rep = FinalReport(
        run_id="r",
        generated_at="now",
        language="es",
        brief=_brief(),
        summary="Resumen de la búsqueda realizada.",
        ranking=[RankedProperty(rank=1, rationale="x", verdict=v)],
        verdicts=[v],
    )
    res = check_run(rep, [])
    assert not res["checks"]["zone_respected"]["ok"]
    v.facts.zone_inside = True
    assert check_run(rep, [])["checks"]["zone_respected"]["ok"]
