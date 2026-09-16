"""Configuración y constantes del sistema.

ÚNICA fuente de verdad de caps, modelos y timeouts. Los prompts repiten
algunos números, pero se generan desde acá (ver `agents/prompts.py`).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Caps duros (regla 9). No hardcodear estos números en otro lado.
# ---------------------------------------------------------------------------
MAX_CANDIDATES = 30  # candidatos máximos que discovery deposita en /candidates/hotels.json
MAX_ANALYSTS = 12  # property-analyst en paralelo por corrida (subido de 8 al sumar fuentes web, 2026-09-12)
MAX_REVIEWS_PER_CALL = 25  # reviews máximas que devuelve get_property_reviews en una llamada
REVIEW_TEXT_MAX_CHARS = 400  # truncado del texto de cada review antes de dárselo al modelo
MAX_SEARCH_CALLS_PER_RUN = 4  # búsquedas de Google Hotels por corrida (protege el cupo de SerpAPI)
MAX_WEB_SEARCHES_PER_RUN = 6  # búsquedas web (tool server-side de Anthropic) por llamada de discovery
MAX_FETCHES_PER_RUN = 20  # páginas leídas con fetch_page por corrida
PAGE_TEXT_MAX_CHARS = (
    4500  # texto legible de una página que ve el modelo (cada página se re-envía en cada llamada)
)
PAGE_LINKS_MAX = 40  # links del mismo sitio que devuelve fetch_page (para llegar a la página propia)
PAGE_TEXT_MIN_CHARS = 300  # menos que esto = página vacía/JS: se devuelve error para que el modelo no insista
FETCH_TIMEOUT_SECONDS = 15.0
FETCH_MAX_BYTES = 2_000_000
# Portales que bloquean lectura automatizada; sus propiedades ya llegan vía Google Hotels.
# Comparadores/redirectores que Google Hotels devuelve como "link" de la propiedad: no son la publicación exacta.
AGGREGATOR_DOMAINS = (
    "vio.com",
    "freecancellations.com",
    "bluepillow.com",
    "bluepillow.ca",
    "kayak.com",
    "trivago.com",
    "hotelscombined.com",
    "agoda.com",
    "zenhotels.com",
    "algotels.com",
)
BLOCKED_FETCH_DOMAINS = (
    "airbnb.com",
    "airbnb.com.br",
    "airbnb.com.ar",
    "booking.com",
    "expedia.com",
    "hotels.com",
    "hoteis.com",
    "vrbo.com",
    "tripadvisor.com",
    "google.com",
    "kayak.com",
    "vio.com",
    "freecancellations.com",
    "bluepillow.com",
    "bluepillow.ca",
    "instagram.com",
    "facebook.com",
)
RANKING_CAP = MAX_ANALYSTS  # el reporte muestra TODAS las recomendadas (no hay top fijo); tope = analizadas
STALE_AFTER_HOURS = 24  # un reporte más viejo que esto muestra 'precios vencidos' y ofrece rebuscar

# ---------------------------------------------------------------------------
# Concurrencia y timeouts (reglas 7 y 8)
# ---------------------------------------------------------------------------
SERPAPI_CONCURRENCY = 5  # semáforo global para TODAS las llamadas a SerpAPI
SERPAPI_TIMEOUT_SECONDS = 30.0  # timeout por llamada HTTP
SERPAPI_RETRIES = 1  # reintentos ante error de red / 5xx
RUN_TIMEOUT_SECONDS = 600  # red de seguridad última de un turno de investigación completo
MODEL_TIMEOUT_SECONDS = 120.0  # timeout por llamada al modelo
MODEL_MAX_RETRIES = 3  # reintentos del SDK de Anthropic (429/5xx)
DISCOVERY_CACHE_TTL_SECONDS = 30 * 60  # cache en memoria de búsquedas (backend)
RECURSION_LIMIT = 200  # tope de pasos del grafo por turno (agente + subagentes)

# ---------------------------------------------------------------------------
# Modelos. Decisión haiku/sonnet en el analista: PENDIENTE de la matriz de evals
# (`uv run python -m lodging.evals --analyst-model ...`). Hasta entonces, haiku.
# ---------------------------------------------------------------------------
ORCHESTRATOR_MODEL = "anthropic:claude-sonnet-5"
DISCOVERY_MODEL = "anthropic:claude-sonnet-5"  # lee páginas web y decide candidatos: sonnet (2026-09-12)
ANALYST_MODEL = "anthropic:claude-haiku-4-5"

# Ruta de artefactos por corrida (no se commitea)
RUNS_DIR = Path("runs")

# Rutas del filesystem virtual de deepagents
VFS_BRIEF_PATH = "/brief.json"
VFS_CANDIDATES_PATH = (
    "/candidates/hotels.json"  # JSONL: un candidato por línea (para que read_file no pagine)
)
VFS_REPORT_PATH = "/report.json"


class Settings(BaseSettings):
    """Variables de entorno (ver `.env.example`)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    serpapi_key: str = Field(default="", alias="SERPAPI_KEY")
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    langsmith_api_key: str = Field(default="", alias="LANGSMITH_API_KEY")
    langsmith_tracing: bool = Field(default=False, alias="LANGSMITH_TRACING")
    langsmith_project: str = Field(default="lodging-agent", alias="LANGSMITH_PROJECT")

    orchestrator_model: str = Field(default=ORCHESTRATOR_MODEL, alias="LODGING_ORCHESTRATOR_MODEL")
    discovery_model: str = Field(default=DISCOVERY_MODEL, alias="LODGING_DISCOVERY_MODEL")
    analyst_model: str = Field(default=ANALYST_MODEL, alias="LODGING_ANALYST_MODEL")
    runs_dir: Path = Field(default=RUNS_DIR, alias="LODGING_RUNS_DIR")


_settings: Settings | None = None


def get_settings() -> Settings:
    """Settings cacheadas (se leen una vez por proceso)."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """Para tests: fuerza relectura del entorno."""
    global _settings
    _settings = None
