"""Fixtures compartidas. Los tests NUNCA tocan la red: SerpAPI se mockea con fixtures reales."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from pathlib import Path

import httpx
import pytest

from lodging import config
from lodging.runtime import RunContext, use_context
from lodging.schemas import Brief
from lodging.tools.serpapi_client import SerpApiClient, set_client

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def brief() -> Brief:
    return Brief(
        destination="Buenos Aires, Argentina",
        area_preferences=["Palermo"],
        check_in=date(2026, 10, 10),
        check_out=date(2026, 10, 13),
        adults=2,
        currency="USD",
        budget_total_max=400,
        dealbreakers=["hostel"],
        hard_constraints=["wifi"],
        soft_preferences=["pileta", "desayuno incluido"],
        language="es",
        gl="ar",
    )


class FakeSerpApi:
    """Router de respuestas por engine/params. Cuenta llamadas y permite inyectar fallas."""

    def __init__(self):
        self.calls: list[dict] = []
        self.fail_next: Exception | httpx.Response | None = None
        self.search = load_fixture("google_hotels_search.json")
        self.details = load_fixture("google_hotels_details.json")
        self.reviews = load_fixture("google_hotels_reviews.json")
        self.reviews_page2 = {"reviews": self.reviews["reviews"][:5], "serpapi_pagination": {}}

    def handler(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.calls.append(params)
        assert "api_key" in params
        if self.fail_next is not None:
            f, self.fail_next = self.fail_next, None
            if isinstance(f, Exception):
                raise f
            return f
        engine = params.get("engine")
        if engine == "google_hotels" and "property_token" in params:
            # la ficha real es de un solo hotel: la personalizamos por token para simular N propiedades
            tok = params["property_token"]
            det = dict(self.details)
            det["property_token"] = tok
            match = next((p for p in self.search["properties"] if p.get("property_token") == tok), None)
            if match:
                det["name"] = match["name"]
                det["link"] = match.get("link", det.get("link"))
            return httpx.Response(200, json=det)
        if engine == "google_hotels":
            return httpx.Response(200, json=self.search)
        if engine == "google_hotels_reviews":
            if params.get("next_page_token"):
                return httpx.Response(200, json=self.reviews_page2)
            return httpx.Response(200, json=self.reviews)
        return httpx.Response(400, json={"error": f"engine desconocido {engine}"})


@pytest.fixture
def fake_serpapi() -> FakeSerpApi:
    fake = FakeSerpApi()
    client = SerpApiClient("test-key", transport=httpx.MockTransport(fake.handler), cache_ttl=60)
    set_client(client)
    yield fake
    set_client(None)


@pytest.fixture
def run_ctx(brief: Brief, tmp_path: Path, monkeypatch) -> RunContext:
    """Contexto activo con brief confirmado (para probar tools directamente)."""
    monkeypatch.setenv("LODGING_RUNS_DIR", str(tmp_path / "runs"))
    config.reset_settings()
    ctx = RunContext(brief, run_id="test-run")
    with use_context(ctx):
        yield ctx
    config.reset_settings()


@pytest.fixture
def make_ctx(tmp_path: Path, monkeypatch) -> Callable[..., RunContext]:
    """Fábrica de contextos vacíos (sin brief): el agente lo guarda con save_brief."""
    monkeypatch.setenv("LODGING_RUNS_DIR", str(tmp_path / "runs"))
    config.reset_settings()

    def _make(brief: Brief | None = None) -> RunContext:
        return RunContext(brief)

    yield _make
    config.reset_settings()
