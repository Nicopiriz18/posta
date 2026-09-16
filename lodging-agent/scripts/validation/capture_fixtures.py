"""Captura fixtures REALES de SerpAPI para tests (consume 3 búsquedas del cupo).

Uso:  uv run python scripts/validation/capture_fixtures.py [--query "Hotels in Palermo, Buenos Aires"]
Escribe tests/fixtures/google_hotels_{search,details,reviews}.json sin imágenes/logos (para que pesen poco).
Nunca correrlo desde tests ni CI.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

from lodging import config
from lodging.tools.serpapi_client import SerpApiClient

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"
STRIP = {"images", "thumbnail", "logo", "source_icon", "ads"}


def strip(o):
    if isinstance(o, dict):
        return {k: strip(v) for k, v in o.items() if k not in STRIP}
    if isinstance(o, list):
        return [strip(x) for x in o]
    return o


async def main() -> None:
    load_dotenv()
    p = argparse.ArgumentParser()
    p.add_argument("--query", default="Hotels in Palermo, Buenos Aires")
    p.add_argument("--gl", default="ar")
    p.add_argument("--hl", default="es")
    args = p.parse_args()
    check_in = date.today() + timedelta(days=30)
    common = {
        "check_in_date": check_in.isoformat(),
        "check_out_date": (check_in + timedelta(days=3)).isoformat(),
        "adults": 2,
        "currency": "USD",
        "gl": args.gl,
        "hl": args.hl,
    }
    client = SerpApiClient(config.get_settings().serpapi_key)
    try:
        search = await client.get({"engine": "google_hotels", "q": args.query, **common})
        (FIXTURES / "google_hotels_search.json").write_text(
            json.dumps(strip(search), ensure_ascii=False, indent=1), encoding="utf-8"
        )
        hotel = next(p for p in search["properties"] if p.get("type") == "hotel")
        details = await client.get(
            {"engine": "google_hotels", "q": args.query, "property_token": hotel["property_token"], **common}
        )
        (FIXTURES / "google_hotels_details.json").write_text(
            json.dumps(strip(details), ensure_ascii=False, indent=1), encoding="utf-8"
        )
        reviews = await client.get(
            {
                "engine": "google_hotels_reviews",
                "property_token": hotel["property_token"],
                "hl": args.hl,
                "sort_by": "2",
            }
        )
        (FIXTURES / "google_hotels_reviews.json").write_text(
            json.dumps(strip(reviews), ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(
            f"OK: {len(search['properties'])} propiedades, ficha de {hotel['name']}, {len(reviews.get('reviews', []))} reviews. Llamadas: {client.calls}"
        )
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
