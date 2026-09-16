"""Tools de web abierta (sin red): fetch_page, add_web_candidate, parsing de web_search server-side, trazabilidad."""

from __future__ import annotations

import httpx
from langchain_core.messages import AIMessage

from lodging import config
from lodging.runner import _StreamState, facts_from_tools
from lodging.runtime import RunContext, use_context
from lodging.schemas import Brief, Candidate
from lodging.tools import get_property_details
from lodging.tools.web import (
    WEB_SEARCH_TOOL,
    _price_in_text,
    add_web_candidate,
    fetch_page,
    find_match,
    set_http,
)

PAGE = """<html><head><title>Pousada Vento Sul - Praia do Rosa</title></head><body>
<script>var x = 1;</script>
<h1>Pousada Vento Sul</h1>
<p>Suítes para até 4 pessoas a 300 m da Praia do Rosa Norte. Wi-Fi, café da manhã incluso.</p>
<p>A pousada fica em uma rua tranquila, com jardim, estacionamento e vista para o mar. Cada suíte tem ar-condicionado, frigobar, roupa de cama e toalhas. O café da manhã é servido das 8h às 10h30 com pães, frutas da estação, sucos e café coado. Aceitamos pets de pequeno porte mediante consulta prévia.</p>
<table><tr><td>Diária alta temporada</td><td>R$ 850,00</td></tr><tr><td>Pacote 12 noites</td><td>R$ 9.800</td></tr></table>
<p>Reservas pelo WhatsApp.</p></body></html>"""


def _mock_http(pages: dict[str, str | httpx.Response]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        hit = pages.get(str(request.url))
        if hit is None:
            return httpx.Response(404, text="not found")
        if isinstance(hit, httpx.Response):
            return hit
        return httpx.Response(200, text=hit, headers={"content-type": "text/html; charset=utf-8"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def _ctx() -> RunContext:
    brief = Brief(
        destination="Praia do Rosa",
        check_in="2027-01-05",
        check_out="2027-01-17",
        adults=9,
        currency="USD",
        gl="br",
    )
    ctx = RunContext(brief)
    return ctx


async def test_fetch_page_extracts_text_caches_and_caps():
    ctx = _ctx()
    set_http(
        _mock_http(
            {
                "https://ventosul.com.br/": PAGE,
                "https://ventosul.com.br/pdf": httpx.Response(
                    200, content=b"%PDF", headers={"content-type": "application/pdf"}
                ),
            }
        )
    )
    try:
        with use_context(ctx):
            out = await fetch_page.ainvoke({"url": "https://ventosul.com.br/"})
            assert "error" not in out
            assert out["domain"] == "ventosul.com.br" and "Vento Sul" in out["title"]
            assert "R$ 850" in out["text"] and "var x" not in out["text"]
            assert ctx.fetch_calls == 1 and ctx.tool_outputs[-1]["tool"] == "fetch_page"
            again = await fetch_page.ainvoke({"url": "https://ventosul.com.br/"})
            assert again.get("cached") is True and ctx.fetch_calls == 1
            bad = await fetch_page.ainvoke({"url": "https://ventosul.com.br/pdf"})
            assert bad["error"]["type"] == "invalid_params" and "not a page" in bad["error"]["message"]
            blocked = await fetch_page.ainvoke({"url": "https://www.airbnb.com.br/rooms/123"})
            assert "blocks automated reading" in blocked["error"]["message"]
            missing = await fetch_page.ainvoke({"url": "https://ventosul.com.br/nada"})
            assert missing["error"]["type"] == "http"
            ctx.fetch_calls = config.MAX_FETCHES_PER_RUN
            capped = await fetch_page.ainvoke({"url": "https://otra.com/"})
            assert "cap" in capped["error"]["message"].lower()
    finally:
        set_http(None)


def test_price_in_text_formats():
    assert _price_in_text(850, "Diária R$ 850,00")
    assert _price_in_text(9800, "Pacote R$ 9.800 por 12 noites")
    assert _price_in_text(2237, "total US$ 2,237")
    assert not _price_in_text(999, "Diária R$ 850,00")


async def test_add_web_candidate_requires_page_verifies_price_and_merges():
    ctx = _ctx()
    set_http(_mock_http({"https://ventosul.com.br/": PAGE}))
    try:
        with use_context(ctx):
            out = await add_web_candidate.ainvoke(
                {
                    "name": "Pousada Vento Sul",
                    "url": "https://ventosul.com.br/",
                    "price_total": 9800,
                    "currency": "BRL",
                }
            )
            assert out["error"]["type"] == "invalid_params" and "fetch_page" in out["error"]["message"]
            await fetch_page.ainvoke({"url": "https://ventosul.com.br/"})
            out = await add_web_candidate.ainvoke(
                {
                    "name": "Pousada Vento Sul",
                    "url": "https://ventosul.com.br/",
                    "price_total": 9800,
                    "price_per_night": 850,
                    "currency": "BRL",
                    "property_type": "pousada",
                    "location_text": "300 m da Praia do Rosa Norte",
                }
            )
            assert (
                out["stored"] and out["property_token"].startswith("web:") and out["price_verified"] is True
            )
            cand = ctx.candidates[out["property_token"]]
            assert (
                cand.source == "web"
                and cand.total_rate == 9800
                and cand.currency == "BRL"
                and cand.price_source == "ventosul.com.br"
            )
            assert '"source":"web"' in ctx.candidates_file_content()
            # precio inventado (no está en la página) → se descarta, con nota (misma propiedad: se unifica)
            out2 = await add_web_candidate.ainvoke(
                {
                    "name": "Pousada Vento Sul",
                    "url": "https://ventosul.com.br/",
                    "price_total": 1234,
                    "currency": "BRL",
                }
            )
            assert out2["merged_into"] == "Pousada Vento Sul"
            assert (
                out2["price_verified"] is False
                and out2["price_total"] is None
                and "not found" in out2["note"]
            )
            # una propiedad que la página no menciona → error
            out_nm = await add_web_candidate.ainvoke(
                {
                    "name": "Casa Azul del Mar",
                    "url": "https://ventosul.com.br/",
                    "price_total": 9800,
                    "currency": "BRL",
                }
            )
            assert (
                out_nm["error"]["type"] == "invalid_params"
                and "does not mention" in out_nm["error"]["message"]
            )
            # el mismo alojamiento ya conocido por Google Hotels → se unifica como oferta extra
            ctx.add_candidates(
                [
                    Candidate(
                        property_token="G1",
                        name="Vento Sul Pousada",
                        price_verified_at="x",
                        total_rate=2000,
                        currency="USD",
                        price_source="Booking.com",
                    )
                ]
            )
            out3 = await add_web_candidate.ainvoke(
                {
                    "name": "Pousada Vento Sul",
                    "url": "https://ventosul.com.br/",
                    "price_total": 9800,
                    "currency": "BRL",
                }
            )
            # ojo: el primer web candidate ya se llama "Pousada Vento Sul" y es el match exacto
            assert out3.get("merged_into") in ("Pousada Vento Sul", "Vento Sul Pousada")
            assert (
                find_match(ctx, "vento sul") is not None and find_match(ctx, "Hotel Totalmente Otro") is None
            )
    finally:
        set_http(None)


async def test_web_candidate_facts_and_serpapi_rejection(fake_serpapi):
    ctx = _ctx()
    set_http(_mock_http({"https://ventosul.com.br/": PAGE}))
    try:
        with use_context(ctx):
            await fetch_page.ainvoke({"url": "https://ventosul.com.br/"})
            out = await add_web_candidate.ainvoke(
                {
                    "name": "Pousada Vento Sul",
                    "url": "https://ventosul.com.br/",
                    "price_total": 9800,
                    "currency": "BRL",
                }
            )
            tok = out["property_token"]
            rej = await get_property_details.ainvoke({"property_token": tok})
            assert "web candidate" in rej["error"]["message"] and "ventosul" in rej["error"]["message"]
            facts = facts_from_tools(ctx, tok)
            assert facts is not None and facts.source == "web" and facts.url == "https://ventosul.com.br/"
            # oferta web sumada a una propiedad de Google Hotels con el mismo nombre (otro contexto)
    finally:
        set_http(None)
    ctx2 = _ctx()
    set_http(_mock_http({"https://ventosul.com.br/": PAGE}))
    try:
        with use_context(ctx2):
            ctx2.add_candidates(
                [
                    Candidate(
                        property_token="G2",
                        name="Vento Sul Pousada",
                        price_verified_at="x",
                        total_rate=2000,
                        currency="USD",
                        price_source="Booking.com",
                    )
                ]
            )
            await fetch_page.ainvoke({"url": "https://ventosul.com.br/"})
            merged = await add_web_candidate.ainvoke(
                {
                    "name": "Pousada Vento Sul",
                    "url": "https://ventosul.com.br/",
                    "price_total": 9800,
                    "currency": "BRL",
                }
            )
            assert merged["merged_into"] == "Vento Sul Pousada"
            facts2 = facts_from_tools(ctx2, "G2")
            assert facts2 is not None and [o.source for o in facts2.offers] == ["ventosul.com.br"]
    finally:
        set_http(None)


def test_server_web_search_blocks_are_logged():
    ctx = _ctx()
    state = _StreamState(ctx)
    msg = AIMessage(
        content=[
            {
                "type": "server_tool_use",
                "id": "s1",
                "name": "web_search",
                "input": {"query": "pousada praia do rosa"},
            },
            {
                "type": "web_search_tool_result",
                "tool_use_id": "s1",
                "content": [
                    {
                        "type": "web_search_result",
                        "title": "Pousada Vento Sul",
                        "url": "https://ventosul.com.br/",
                        "page_age": None,
                        "encrypted_content": "xx",
                    },
                    {
                        "type": "web_search_result",
                        "title": "Guia",
                        "url": "https://guia.com/",
                        "encrypted_content": "yy",
                    },
                ],
            },
            {"type": "text", "text": "listo"},
        ]
    )
    state.handle_sub_ai(msg)
    assert ctx.web_searches == 1
    assert ctx.tool_outputs[-1]["tool"] == "web_search" and [
        r["url"] for r in ctx.tool_outputs[-1]["output"]["results"]
    ] == ["https://ventosul.com.br/", "https://guia.com/"]
    types = [e.type for e in ctx.events]
    assert types[-2:] == ["tool_call", "tool_result"] and ctx.events[-1].data["ok"] is True
    err = AIMessage(
        content=[
            {"type": "server_tool_use", "id": "s2", "name": "web_search", "input": {"query": "x"}},
            {
                "type": "web_search_tool_result",
                "tool_use_id": "s2",
                "content": {"type": "web_search_tool_result_error", "error_code": "max_uses_exceeded"},
            },
        ]
    )
    state.handle_sub_ai(err)
    assert ctx.events[-1].data["ok"] is False and ctx.tool_errors == 1


def test_web_search_tool_spec():
    assert (
        WEB_SEARCH_TOOL["type"] == "web_search_20250305"
        and WEB_SEARCH_TOOL["max_uses"] == config.MAX_WEB_SEARCHES_PER_RUN
    )


def test_duplicate_google_listings_are_merged_as_offers():
    from lodging.runtime import same_property

    ctx = _ctx()
    a = Candidate(
        property_token="A",
        name="Casa Praia do Rosa",
        latitude=-28.1234,
        longitude=-48.6400,
        total_rate=2828,
        currency="USD",
        price_source="Booking.com",
        price_verified_at="x",
    )
    b = Candidate(  # mismo alojamiento vía otro comparador, a 40 m
        property_token="B",
        name="Casa Praia do Rosa",
        latitude=-28.12345,
        longitude=-48.6402,
        total_rate=2700,
        currency="USD",
        price_source="Bluepillow.com",
        price_verified_at="x",
    )
    c = Candidate(  # mismo nombre pero a 3 km: otra casa
        property_token="C",
        name="Casa Praia do Rosa",
        latitude=-28.15,
        longitude=-48.66,
        total_rate=1500,
        currency="USD",
        price_source="Booking.com",
        price_verified_at="x",
    )
    assert same_property(a, b) and not same_property(a, c)
    added, _ = ctx.add_candidates([a, b, c])
    assert added == 2 and list(ctx.candidates) == ["A", "C"]
    assert [o.source for o in ctx.candidates["A"].offers] == ["Bluepillow.com"]
    assert ctx.merged_duplicates == {"B": "A"}


async def test_fetch_page_rejects_empty_js_pages():
    ctx = _ctx()
    set_http(_mock_http({"https://deals.example.com/": "<html><body><div id='app'></div></body></html>"}))
    try:
        with use_context(ctx):
            out = await fetch_page.ainvoke({"url": "https://deals.example.com/"})
            assert (
                out["error"]["type"] == "invalid_params" and "no readable content" in out["error"]["message"]
            )
            blocked = await fetch_page.ainvoke({"url": "https://www.vio.com/Hotel/Search?hotelId=1"})
            assert "blocks automated reading" in blocked["error"]["message"]
    finally:
        set_http(None)


def test_web_search_query_survives_split_chunks():
    ctx = _ctx()
    state = _StreamState(ctx)
    state.handle_sub_ai(
        AIMessage(
            content=[
                {
                    "type": "server_tool_use",
                    "id": "s1",
                    "name": "web_search",
                    "input": {"query": "casas praia do rosa"},
                }
            ]
        )
    )
    state.handle_sub_ai(
        AIMessage(
            content=[
                {
                    "type": "web_search_tool_result",
                    "tool_use_id": "s1",
                    "content": [
                        {
                            "type": "web_search_result",
                            "title": "t",
                            "url": "https://x.com/",
                            "encrypted_content": "e",
                        }
                    ],
                }
            ]
        )
    )
    assert ctx.tool_outputs[-1]["args"]["query"] == "casas praia do rosa"


def test_assertion_accepts_total_derived_from_verified_nightly_rate():
    from lodging.evals.assertions import check_run
    from lodging.schemas import AnalystOutput, FinalReport, PriceEvidence, PropertyVerdict, RankedProperty

    brief = Brief(destination="Praia do Rosa", check_in="2027-01-05", check_out="2027-01-17", adults=9)
    tool_outputs = [
        {
            "tool": "add_web_candidate",
            "args": {},
            "output": {
                "property_token": "web:abc",
                "stored": True,
                "price_verified": True,
                "price_total": None,
                "price_per_night": 1000.0,
                "currency": "BRL",
                "source": "alexia.com",
                "price_verified_at": "2026-09-13T00:00:00+00:00",
            },
        }
    ]
    v = PropertyVerdict.from_analyst(
        AnalystOutput(
            property_token="web:abc",
            name="Casa 3 quartos",
            price=PriceEvidence(
                total=12000.0,
                currency="BRL",
                per_night=1000.0,
                source="alexia.com",
                verified_at="2026-09-13T00:00:00+00:00",
            ),
            summary="Casa amplia cerca de la playa para el grupo.",
        )
    )
    report = FinalReport(
        run_id="r",
        generated_at="now",
        language="es",
        brief=brief,
        summary="Se analizó una casa hallada en una inmobiliaria local.",
        ranking=[RankedProperty(rank=1, rationale="Única con capacidad confirmada.", verdict=v)],
        verdicts=[v],
    )
    res = check_run(report, tool_outputs)
    assert res["checks"]["prices_traceable"]["ok"] and res["checks"]["properties_traceable"]["ok"], res


LISTING = """<html><head><title>Imobiliária Neia - Casas para temporada na Praia do Rosa</title></head><body>
<h1>Casas para alugar na Praia do Rosa</h1>
<p>Selecionamos as melhores casas de temporada para grupos e famílias. Todas com cozinha equipada, Wi-Fi e
estacionamento. Consulte disponibilidade para janeiro e fevereiro, alta temporada, com pacotes de 7 a 14 noites.</p>
<ul>
<li><a href="/imobiliaria-neia/casa-rosa-14">Casa Rosa 14 - 5 dormitórios, piscina</a> R$ 2.500 a diária</li>
<li><a href="/imobiliaria-neia/casa-rosa-62">Casa Rosa 62 - 4 dormitórios</a> R$ 1.900 a diária</li>
<li><a href="https://www.instagram.com/neia">Instagram</a></li>
<li><a href="mailto:x@y.com">Contato</a></li>
</ul></body></html>"""

DETAIL = """<html><head><title>Casa Rosa 14 - Imobiliária Neia</title></head><body>
<h1>Casa Rosa 14</h1>
<p>Casa com 5 dormitórios, piscina aquecida e churrasqueira, a 400 m da Praia do Rosa Norte. Acomoda até 12 pessoas
com conforto. Cozinha completa, Wi-Fi fibra, ar-condicionado em todos os quartos, estacionamento para 4 carros.</p>
<p>A casa fica em rua asfaltada e tranquila, a poucos minutos de carro do Centrinho, com padaria e mercado perto. Ideal para
grupos de amigos e famílias grandes que querem ficar juntos. Roupa de cama e toalhas inclusas, limpeza final incluída.</p>
<table><tr><td>Diária alta temporada</td><td>R$ 2.500</td></tr><tr><td>Mínimo</td><td>7 noites</td></tr></table>
</body></html>"""


async def test_fetch_page_returns_links_and_add_requires_property_page():
    ctx = _ctx()
    set_http(
        _mock_http(
            {
                "https://neia.com.br/imobiliaria-neia/": LISTING,
                "https://neia.com.br/imobiliaria-neia/casa-rosa-14": DETAIL,
            }
        )
    )
    try:
        with use_context(ctx):
            listing = await fetch_page.ainvoke({"url": "https://neia.com.br/imobiliaria-neia/"})
            assert "error" not in listing
            links = listing["links"]
            assert {
                "text": "Casa Rosa 14 - 5 dormitórios, piscina",
                "url": "https://neia.com.br/imobiliaria-neia/casa-rosa-14",
            } in links
            assert not any("instagram" in lk["url"] or lk["url"].startswith("mailto") for lk in links)
            # la página no menciona esta propiedad → error
            out = await add_web_candidate.ainvoke(
                {
                    "name": "Casa Azul del Mar",
                    "url": "https://neia.com.br/imobiliaria-neia/",
                    "price_total": 2500,
                    "currency": "BRL",
                }
            )
            assert out["error"]["type"] == "invalid_params" and "does not mention" in out["error"]["message"]
            # registrar desde el listado: se acepta pero avisa que no es la página propia y sugiere el link
            out = await add_web_candidate.ainvoke(
                {
                    "name": "Casa Rosa 14",
                    "url": "https://neia.com.br/imobiliaria-neia/",
                    "price_per_night": 2500,
                    "currency": "BRL",
                }
            )
            assert out["stored"] and out["is_property_page"] is False
            assert "casa-rosa-14" in out["note"]
            # registrar desde la página propia: es la página de la propiedad
            await fetch_page.ainvoke({"url": "https://neia.com.br/imobiliaria-neia/casa-rosa-14"})
            out2 = await add_web_candidate.ainvoke(
                {
                    "name": "Casa Rosa 14",
                    "url": "https://neia.com.br/imobiliaria-neia/casa-rosa-14",
                    "price_per_night": 2500,
                    "currency": "BRL",
                }
            )
            assert out2["is_property_page"] is True
            # el mismo alojamiento: se unificó y el candidato ahora apunta a la página propia
            assert out2["property_token"] == out["property_token"]
            assert (
                ctx.candidates[out["property_token"]].url
                == "https://neia.com.br/imobiliaria-neia/casa-rosa-14"
            )
    finally:
        set_http(None)


def test_booking_url_prefers_real_portals_over_aggregators():
    from lodging.runner import facts_from_tools

    ctx = _ctx()
    ctx.add_candidates(
        [
            Candidate(
                property_token="G",
                name="Pousada Natribus",
                link="https://deals.vio.com?sig=abc",
                price_verified_at="x",
                offers=[],
            )
        ]
    )
    ctx.log_tool(
        "get_property_details",
        {},
        {
            "property_token": "G",
            "name": "Pousada Natribus",
            "link": "https://deals.vio.com?sig=abc",
            "price_verified_at": "x",
            "offers": [
                {"source": "Vio.com", "total": 1500, "link": "https://deals.vio.com?sig=abc"},
                {
                    "source": "Booking.com",
                    "total": 1600,
                    "link": "https://www.google.com/travel/clk?pc=booking",
                },
            ],
        },
    )
    facts = facts_from_tools(ctx, "G")
    assert facts is not None
    assert facts.booking_url == "https://www.google.com/travel/clk?pc=booking"
    assert facts.booking_source == "Booking.com"
    assert "google.com/travel/search" in facts.google_hotels_url
    web = Candidate(
        property_token="web:1",
        name="Casa Rosa 14",
        source="web",
        url="https://neia.com.br/casa-rosa-14",
        price_verified_at="x",
    )
    ctx.add_candidates([web])
    f2 = facts_from_tools(ctx, "web:1")
    assert (
        f2 is not None
        and f2.booking_url == "https://neia.com.br/casa-rosa-14"
        and f2.booking_source == "neia.com.br"
    )
