"""Tests de la capa de datos (sin red)."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from lodging import config
from lodging.schemas import Candidate, PropertyDetails, ReviewsResult, SearchResult
from lodging.tools import get_property_details, get_property_reviews, search_hotels
from lodging.tools.serpapi_client import SerpApiClient, SerpApiError, get_client


async def test_search_normalizes_and_deposits_candidates(fake_serpapi, run_ctx):
    out = await search_hotels.ainvoke({"query": "Hotels in Palermo, Buenos Aires"})
    assert "error" not in out
    res = SearchResult.model_validate(out)
    assert res.total_returned == 20
    assert res.next_page_token == "CBI="
    c = res.candidates[0]
    assert isinstance(c, Candidate)
    assert c.name == "Corazón de Palermo Soho"
    assert c.type == "vacation rental"
    assert c.total_rate == 149 and c.currency == "USD"
    assert c.price_source == "Bluepillow.com"
    assert c.price_verified_at.endswith("+00:00")
    assert "Wi‑Fi gratis" in c.amenities
    # ningún campo crudo de SerpAPI se filtra
    assert "serpapi_property_details_link" not in out["candidates"][0]
    assert "images" not in out["candidates"][0]
    # parámetros de estadía salen del brief
    sent = fake_serpapi.calls[0]
    assert sent["check_in_date"] == "2026-10-10" and sent["check_out_date"] == "2026-10-13"
    assert sent["adults"] == "2" and sent["currency"] == "USD" and sent["gl"] == "ar" and sent["hl"] == "es"
    # candidatos depositados en el contexto (base de /candidates/hotels.json)
    assert len(run_ctx.candidates) == 20
    assert run_ctx.tool_outputs[-1]["tool"] == "search_hotels"
    hotel = next(x for x in res.candidates if x.type == "hotel")
    assert hotel.hotel_class in (2, 3, 4, 5)


async def test_search_uses_cache_and_caps_calls(fake_serpapi, run_ctx):
    await search_hotels.ainvoke({"query": "Hotels in Palermo"})
    await search_hotels.ainvoke({"query": "Hotels in Palermo"})
    assert len(fake_serpapi.calls) == 1  # segunda va a cache
    assert get_client().cache_hits == 1
    for i in range(config.MAX_SEARCH_CALLS_PER_RUN):
        await search_hotels.ainvoke({"query": f"q{i}"})
    out = await search_hotels.ainvoke({"query": "otra"})
    assert out["error"]["type"] == "invalid_params"
    assert "cap" in out["error"]["message"].lower()


async def test_search_candidate_cap(fake_serpapi, run_ctx, monkeypatch):
    monkeypatch.setattr(config, "MAX_CANDIDATES", 5)
    out = await search_hotels.ainvoke({"query": "Hotels"})
    assert len(run_ctx.candidates) == 5
    assert "cap" in (out.get("note") or "")


async def test_details_normalizes(fake_serpapi, run_ctx):
    out = await get_property_details.ainvoke({"property_token": "ChkI-8aA1PXn1pUoGg0vZy8xMWYxMnp6dDE0EAE"})
    d = PropertyDetails.model_validate(out)
    assert d.name == "Nido @ Palermo Soho Square"
    assert d.address and "Thames" in d.address
    assert d.total_rate == 176 and d.price_source == "eDreams"
    assert d.offers[0].source == "eDreams" and d.offers[0].total == 176
    assert d.ratings_histogram["5"] == 95
    assert any(c.name == "Location" for c in d.review_categories)
    assert d.hotel_class == 3
    assert len(d.nearby_places) <= 8 and d.nearby_places[0].transportations
    assert "q" in fake_serpapi.calls[0]


async def test_reviews_truncate_normalize_and_paginate(fake_serpapi, run_ctx):
    out = await get_property_reviews.ainvoke({"property_token": "tok", "max_reviews": 10})
    r = ReviewsResult.model_validate(out)
    assert r.fetched == 10 and r.pages == 1
    assert all(len(x.text) <= config.REVIEW_TEXT_MAX_CHARS for x in r.reviews)
    assert all(x.rating is None or 0 <= x.rating <= 5 for x in r.reviews)
    assert r.reviews[0].date_text == "Hace 4 horas"
    assert fake_serpapi.calls[0]["sort_by"] == "2"

    out2 = await get_property_reviews.ainvoke({"property_token": "tok", "max_reviews": 15})
    r2 = ReviewsResult.model_validate(out2)
    assert r2.pages == 2 and r2.fetched == 15

    out3 = await get_property_reviews.ainvoke({"property_token": "tok", "max_reviews": 500})
    assert ReviewsResult.model_validate(out3).fetched <= config.MAX_REVIEWS_PER_CALL


async def test_errors_are_structured_not_raised(fake_serpapi, run_ctx):
    fake_serpapi.fail_next = httpx.Response(400, json={"error": "Missing query `q` parameter."})
    out = await search_hotels.ainvoke({"query": "x"})
    assert out["error"]["type"] == "invalid_params" and out["error"]["retryable"] is False

    fake_serpapi.fail_next = httpx.Response(429, json={"error": "rate"})
    out = await get_property_details.ainvoke({"property_token": "t"})
    assert out["error"]["type"] == "rate_limit" and out["error"]["retryable"] is True

    fake_serpapi.fail_next = httpx.ReadTimeout("slow")
    out = await get_property_reviews.ainvoke({"property_token": "t"})
    # el cliente reintenta una vez y la segunda pasa
    assert "error" not in out
    assert run_ctx.tool_errors == 2
    assert any(e.type == "tool_result" and e.data.get("ok") is False for e in run_ctx.events)


async def test_semaphore_bounds_concurrency():
    active = 0
    peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return httpx.Response(200, json={"properties": []})

    client = SerpApiClient("k", transport=httpx.MockTransport(handler), concurrency=2)
    await asyncio.gather(*(client.get({"engine": "google_hotels", "q": str(i)}) for i in range(8)))
    assert peak <= 2 and client.calls == 8
    await client.aclose()


async def test_client_without_key_raises():
    client = SerpApiClient("")
    with pytest.raises(SerpApiError) as ei:
        await client.get({"engine": "google_hotels"})
    assert ei.value.kind == "invalid_params"
