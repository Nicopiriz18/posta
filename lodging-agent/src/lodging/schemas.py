"""Contratos del sistema: Brief, Candidate, PropertyDetails, Review, PropertyVerdict, FinalReport.

Cambiar algo acá es un cambio de contrato: actualizar a la vez prompts (`agents/prompts.py`),
assertions (`evals/assertions.py`) y frontend (`frontend/src/types.ts`).

La rúbrica del fit_score vive acá (`FitRubric`) y se aplica en código, no por "vibra" del modelo.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from lodging import config

# ---------------------------------------------------------------------------
# Fase 1: Brief
# ---------------------------------------------------------------------------


class LatLng(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class Zone(BaseModel):
    """Zona dibujada en el mapa por el viajero. La geometría se evalúa en código (`tools/geo.py`), nunca el modelo."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, description="Etiqueta corta, ej. 'Rosa Norte'.")
    polygon: list[LatLng] = Field(min_length=3, max_length=500, description="Vértices del polígono, en orden (a mano alzada, simplificado en el frontend).")
    buffer_m: int = Field(
        default=0, ge=0, le=20000, description="Tolerancia en metros alrededor del polígono."
    )

    def summary(self) -> dict[str, Any]:
        """Lo único que ve el modelo: nombre, cantidad de vértices y tolerancia (sin coordenadas)."""
        return {"name": self.name, "vertices": len(self.polygon), "buffer_m": self.buffer_m}


class Brief(BaseModel):
    """Brief de viaje. Es la entrada del deep agent y el output del chat de brief."""

    model_config = ConfigDict(extra="forbid")

    destination: str = Field(description="Ciudad o lugar de destino, ej. 'Buenos Aires, Argentina'.")
    area_preferences: list[str] = Field(
        default_factory=list, description="Barrios o zonas preferidas, ej. ['Palermo', 'Recoleta']."
    )
    check_in: date
    check_out: date
    adults: int = Field(default=2, ge=1, le=12)
    children: int = Field(default=0, ge=0, le=10)
    children_ages: list[int] = Field(default_factory=list)
    currency: str = Field(default="USD", min_length=3, max_length=3, description="Código ISO 4217.")
    budget_total_max: float | None = Field(
        default=None, ge=0, description="Presupuesto máximo TOTAL de la estadía, impuestos incluidos."
    )
    budget_per_night_max: float | None = Field(default=None, ge=0)
    property_types: list[str] = Field(
        default_factory=list, description="Tipos preferidos: hotel, apartment, hostel, vacation rental, etc."
    )
    dealbreakers: list[str] = Field(
        default_factory=list,
        description="Descartes con evidencia positiva (ej. 'hostel', 'baño compartido'). Nunca por ausencia.",
    )
    hard_constraints: list[str] = Field(
        default_factory=list,
        description="Requisitos imprescindibles que el analista debe verificar (ej. 'wifi', 'pileta').",
    )
    soft_preferences: list[str] = Field(
        default_factory=list, description="Deseables que suman puntaje (ej. 'desayuno incluido', 'vista')."
    )
    travel_purpose: str | None = Field(default=None, description="ej. 'trabajo remoto', 'luna de miel'.")
    notes: str | None = None
    language: str = Field(default="es", description="Idioma del reporte final y de los strings al usuario.")
    gl: str | None = Field(default=None, description="País (código de 2 letras) para SerpAPI, ej. 'ar'.")
    zone: Zone | None = Field(default=None, description="Zona dibujada en el mapa (solo desde la UI).")

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, v: str) -> str:
        return v.upper()

    @model_validator(mode="before")
    @classmethod
    def _drop_computed(cls, data: Any) -> Any:
        """`nights` es computado: al re-validar un JSON serializado (runs/, frontend) se ignora."""
        if isinstance(data, dict) and "nights" in data:
            data = {k: v for k, v in data.items() if k != "nights"}
        return data

    @model_validator(mode="after")
    def _dates_ok(self) -> Brief:
        if self.check_out <= self.check_in:
            raise ValueError("check_out debe ser posterior a check_in")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def nights(self) -> int:
        return (self.check_out - self.check_in).days

    @property
    def hl(self) -> str:
        return (self.language or "es").split("-")[0].lower()

    def to_prompt_json(self) -> str:
        """JSON para el modelo: la zona va resumida (nombre y cantidad de vértices), sin coordenadas (regla 10)."""
        data = self.model_dump(mode="json", exclude_none=True, exclude={"zone"})
        if self.zone is not None:
            data["zone"] = self.zone.summary()
        return json.dumps(data, ensure_ascii=False, indent=2)


def brief_date_problems(brief: Brief, today: date | None = None) -> list[str]:
    """Problemas de fechas que el modelo no detecta solo (nunca buscar fechas pasadas)."""
    today = today or date.today()
    problems: list[str] = []
    if brief.check_in < today:
        problems.append(
            f"check_in {brief.check_in.isoformat()} is in the past (today is {today.isoformat()})"
        )
    if brief.nights > 30:
        problems.append(f"stay of {brief.nights} nights is longer than Google Hotels allows (30)")
    return problems


class BriefDraft(BaseModel):
    """Brief parcial mientras el chat lo va completando. Todos los campos son opcionales."""

    model_config = ConfigDict(extra="forbid")

    destination: str | None = None
    area_preferences: list[str] = Field(default_factory=list)
    check_in: date | None = None
    check_out: date | None = None
    adults: int | None = None
    children: int | None = None
    children_ages: list[int] = Field(default_factory=list)
    currency: str | None = None
    budget_total_max: float | None = None
    budget_per_night_max: float | None = None
    property_types: list[str] = Field(default_factory=list)
    dealbreakers: list[str] = Field(default_factory=list)
    hard_constraints: list[str] = Field(default_factory=list)
    soft_preferences: list[str] = Field(default_factory=list)
    travel_purpose: str | None = None
    notes: str | None = None
    language: str | None = None
    gl: str | None = None
    zone: Zone | None = None

    def missing_required(self) -> list[str]:
        return [f for f in ("destination", "check_in", "check_out") if getattr(self, f) in (None, "")]

    def merged(self, other: BriefDraft) -> BriefDraft:
        """Devuelve un draft con los campos no vacíos de `other` pisando a los de self."""
        data = self.model_dump()
        for k, v in other.model_dump().items():
            if v not in (None, [], ""):
                data[k] = v
        return BriefDraft(**data)

    def to_brief(self) -> Brief:
        data = self.model_dump(exclude_none=True)
        data = {k: v for k, v in data.items() if v not in ([], "")}
        return Brief(**data)


# ---------------------------------------------------------------------------
# Capa de datos: outputs normalizados de las tools (regla 1: nunca JSON crudo)
# ---------------------------------------------------------------------------


class ToolErrorInfo(BaseModel):
    type: Literal["timeout", "http", "serpapi", "rate_limit", "invalid_params", "unknown"]
    message: str
    retryable: bool = False
    tool: str
    params: dict[str, Any] = Field(default_factory=dict)


class ToolError(BaseModel):
    """Error estructurado hacia el modelo (regla 7: degradación parcial, no muerte del run)."""

    error: ToolErrorInfo


class NearbyPlace(BaseModel):
    name: str
    category: str | None = None
    transportations: list[str] = Field(
        default_factory=list, description="ej. ['Taxi 14 min', 'Public transport 18 min']"
    )


class PriceOffer(BaseModel):
    source: str
    total: float | None = Field(
        default=None, description="Total estadía con impuestos, si la fuente lo informa."
    )
    per_night: float | None = None
    free_cancellation: bool | None = None
    link: str | None = None


class Candidate(BaseModel):
    """Una propiedad tal como aparece en la búsqueda. Campos normalizados y recortados."""

    property_token: str
    name: str
    type: str | None = Field(default=None, description="'hotel', 'vacation rental', etc.")
    hotel_class: int | None = Field(default=None, description="Estrellas (2-5) si Google las informa.")
    overall_rating: float | None = None
    reviews_count: int | None = None
    location_rating: float | None = None
    rate_per_night: float | None = Field(default=None, description="Mínimo por noche, con impuestos.")
    total_rate: float | None = Field(
        default=None, description="Total de la estadía, CON impuestos (regla 5)."
    )
    total_rate_before_taxes: float | None = None
    currency: str = "USD"
    price_source: str | None = Field(
        default=None, description="Fuente del precio mínimo (ej. 'Booking.com')."
    )
    price_verified_at: str = Field(description="ISO 8601 UTC de cuándo se obtuvo el precio.")
    free_cancellation: bool | None = None
    amenities: list[str] = Field(default_factory=list)
    excluded_amenities: list[str] = Field(default_factory=list)
    essential_info: list[str] = Field(default_factory=list)
    latitude: float | None = None
    longitude: float | None = None
    link: str | None = None
    nearby_places: list[NearbyPlace] = Field(default_factory=list)
    zone_inside: bool | None = Field(
        default=None, description="Dentro de la zona dibujada (+buffer). None = sin coordenadas."
    )
    zone_distance_m: float | None = Field(
        default=None, description="Metros al borde de la zona; 0 si está adentro."
    )
    source: Literal["google_hotels", "web"] = Field(
        default="google_hotels", description="De dónde salió el candidato."
    )
    url: str | None = Field(default=None, description="Página de origen (candidatos web).")
    url_is_property_page: bool | None = Field(
        default=None, description="True si `url` abre exactamente esta propiedad (no un listado)."
    )
    offers: list[PriceOffer] = Field(
        default_factory=list,
        description="Precios adicionales del mismo alojamiento hallados en otras fuentes.",
    )


class SearchResult(BaseModel):
    query: str
    candidates: list[Candidate]
    next_page_token: str | None = None
    total_returned: int
    note: str | None = None


class ReviewCategory(BaseModel):
    name: str
    total: int
    positive: int
    negative: int
    neutral: int


class PropertyDetails(Candidate):
    """Detalle de una propiedad: candidato + dirección, ofertas, desglose de ratings y lugares cercanos."""

    address: str | None = None
    phone: str | None = None
    check_in_time: str | None = None
    check_out_time: str | None = None
    offers: list[PriceOffer] = Field(default_factory=list)
    ratings_histogram: dict[str, int] = Field(default_factory=dict, description="{'5': 95, '4': 21, ...}")
    review_categories: list[ReviewCategory] = Field(default_factory=list)


class Review(BaseModel):
    source: str | None = None
    rating: float | None = Field(default=None, description="Normalizado a escala 0-5.")
    rating_raw: float | None = None
    best_rating: float | None = None
    date_text: str | None = Field(
        default=None, description="Relativo, tal como lo da Google ('Hace 3 semanas')."
    )
    text: str = Field(
        default="",
        description=f"Truncado a {config.REVIEW_TEXT_MAX_CHARS} chars. USO INTERNO: nunca citar verbatim.",
    )
    highlights: list[str] = Field(default_factory=list)
    has_owner_response: bool = False


class ReviewsResult(BaseModel):
    property_token: str
    reviews: list[Review]
    fetched: int
    pages: int
    sort_by: str
    note: str | None = None


# ---------------------------------------------------------------------------
# Rúbrica del fit_score (regla 4). Un solo lugar; prompt y assertions la citan desde acá.
# ---------------------------------------------------------------------------


class FitRubric:
    BASE = 50
    SOFT_PREF_CONFIRMED = 8
    SOFT_PREF_CAP = 40
    CON_RELEVANT = -10
    RED_FLAG = -25
    OVER_BUDGET = -15
    HARD_VIOLATED_MAX = 20
    MIN, MAX = 0, 100

    @classmethod
    def compute(
        cls,
        *,
        soft_confirmed: int,
        cons: int,
        red_flags: int,
        over_budget: bool,
        hard_violated: bool,
    ) -> tuple[int, bool]:
        """Devuelve (fit_score, recommend)."""
        score = cls.BASE
        score += min(soft_confirmed * cls.SOFT_PREF_CONFIRMED, cls.SOFT_PREF_CAP)
        score += cons * cls.CON_RELEVANT
        score += red_flags * cls.RED_FLAG
        if over_budget:
            score += cls.OVER_BUDGET
        recommend = True
        if hard_violated:
            score = min(score, cls.HARD_VIOLATED_MAX)
            recommend = False
        score = max(cls.MIN, min(cls.MAX, score))
        return score, recommend

    @classmethod
    def describe(cls) -> str:
        """Texto de la rúbrica para prompts (en inglés), generado desde las constantes."""
        return (
            f"fit_score rubric (computed by code from your fields, so fill them honestly): "
            f"base {cls.BASE}; +{cls.SOFT_PREF_CONFIRMED} per soft preference with status 'confirmed' "
            f"(cap +{cls.SOFT_PREF_CAP}); {cls.CON_RELEVANT} per relevant con; {cls.RED_FLAG} per red flag; "
            f"{cls.OVER_BUDGET} if over budget; any hard constraint 'violated' => score capped at "
            f"{cls.HARD_VIOLATED_MAX} and recommend=false; clipped to {cls.MIN}-{cls.MAX}."
        )


class CheckStatus(StrEnum):
    CONFIRMED = "confirmed"
    VIOLATED = "violated"
    UNKNOWN = "unknown"


class ConstraintCheck(BaseModel):
    constraint: str
    status: CheckStatus = Field(
        description="'confirmed' / 'violated' only with evidence; otherwise 'unknown'."
    )
    evidence: str | None = Field(
        default=None, description="Where the evidence came from (details, amenities, N reviews)."
    )


class PriceEvidence(BaseModel):
    total: float = Field(description="Total for the whole stay, taxes included.")
    currency: str
    per_night: float | None = None
    source: str = Field(description="Booking source as returned by the tool (e.g. 'Booking.com').")
    verified_at: str = Field(
        description="ISO 8601 timestamp copied from the tool output (price_verified_at)."
    )
    free_cancellation: bool | None = None

    def display(self, language: str = "es") -> str:
        """'desde X vía <fuente>, verificado a las HH:MM' (regla 5)."""
        hhmm = _hhmm(self.verified_at)
        amount = f"{self.currency} {self.total:,.0f}"
        if language.startswith("en"):
            return f"from {amount} via {self.source}, verified at {hhmm}"
        if language.startswith("pt"):
            return f"a partir de {amount} via {self.source}, verificado às {hhmm}"
        return f"desde {amount} vía {self.source}, verificado a las {hhmm}"


def _hhmm(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%H:%M")
    except (ValueError, AttributeError):
        return "??:??"


class AnalystOutput(BaseModel):
    """Lo que produce el property-analyst (structured output). El fit_score lo calcula el código."""

    model_config = ConfigDict(extra="forbid")

    property_token: str
    name: str
    hard_constraints: list[ConstraintCheck] = Field(default_factory=list)
    soft_preferences: list[ConstraintCheck] = Field(default_factory=list)
    pros: list[str] = Field(
        default_factory=list, description="Only evidence-backed positives. Paraphrase reviews."
    )
    cons: list[str] = Field(default_factory=list, description="Only evidence-backed, relevant negatives.")
    red_flags: list[str] = Field(
        default_factory=list, description="Serious issues: safety, scams, closures, recurring complaints."
    )
    unknowns: list[str] = Field(
        default_factory=list, description="Things that could not be verified. Never a pro nor a con."
    )
    price: PriceEvidence | None = Field(
        default=None, description="Copied from tool output. None if no price was returned."
    )
    over_budget: bool | None = Field(default=None, description="None when price or budget is unknown.")
    reviews_summary: str = Field(
        default="", description="Aggregated + paraphrased. Never quote a review verbatim."
    )
    reviews_analyzed: int = 0
    location_summary: str = ""
    summary: str = Field(default="", description="2-3 sentences in the brief language.")
    link: str | None = None


class PropertyFacts(BaseModel):
    """Datos de la ficha copiados EN CÓDIGO desde el tool output de get_property_details (trazables)."""

    type: str | None = None
    hotel_class: int | None = None
    overall_rating: float | None = None
    reviews_count: int | None = None
    location_rating: float | None = None
    address: str | None = None
    essential_info: list[str] = Field(default_factory=list)
    amenities: list[str] = Field(default_factory=list)
    excluded_amenities: list[str] = Field(default_factory=list)
    nearby_places: list[NearbyPlace] = Field(default_factory=list)
    offers: list[PriceOffer] = Field(
        default_factory=list, description="El mismo alojamiento en distintas fuentes."
    )
    source: str = "google_hotels"
    url: str | None = None
    zone_inside: bool | None = None
    zone_distance_m: float | None = None
    booking_url: str | None = Field(
        default=None, description="Mejor enlace para abrir/reservar ESTA propiedad (elegido por código)."
    )
    booking_source: str | None = Field(default=None, description="A qué sitio lleva booking_url.")
    google_hotels_url: str | None = Field(
        default=None, description="Búsqueda de la propiedad en Google Hotels (fallback)."
    )
    free_cancellation: bool | None = None
    check_in_time: str | None = None
    check_out_time: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    review_categories: list[ReviewCategory] = Field(default_factory=list)


class PropertyVerdict(AnalystOutput):
    """AnalystOutput + fit_score/recommend calculados por la rúbrica + facts de la ficha (código)."""

    fit_score: int = Field(ge=0, le=100)
    recommend: bool
    facts: PropertyFacts | None = None

    @classmethod
    def from_analyst(cls, out: AnalystOutput) -> PropertyVerdict:
        score, recommend = cls.rubric_inputs(out)
        return cls(**out.model_dump(), fit_score=score, recommend=recommend)

    @staticmethod
    def rubric_inputs(out: AnalystOutput) -> tuple[int, bool]:
        soft_confirmed = sum(1 for c in out.soft_preferences if c.status == CheckStatus.CONFIRMED)
        hard_violated = any(c.status == CheckStatus.VIOLATED for c in out.hard_constraints)
        return FitRubric.compute(
            soft_confirmed=soft_confirmed,
            cons=len(out.cons),
            red_flags=len(out.red_flags),
            over_budget=bool(out.over_budget),
            hard_violated=hard_violated,
        )


# ---------------------------------------------------------------------------
# Salidas estructuradas de discovery y orquestador
# ---------------------------------------------------------------------------


class ShortlistEntry(BaseModel):
    property_token: str
    name: str
    why: str = Field(description="One line: why it is a plausible fit (from search data only).")


class DiscardedCandidate(BaseModel):
    property_token: str | None = None
    name: str
    reason: str = Field(description="Positive evidence of a violation. Absence of data is never a reason.")
    stage: Literal["discovery", "orchestrator", "analyst"] = "discovery"


class DiscoveryOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shortlist: list[ShortlistEntry] = Field(
        description=f"Up to {config.MAX_CANDIDATES} plausible candidates."
    )
    discarded: list[DiscardedCandidate] = Field(default_factory=list)
    searches_run: int = 0
    notes: str = ""


class RankEntry(BaseModel):
    property_token: str
    rank: int = Field(ge=1)
    rationale: str = Field(
        description="Why this rank, in the brief language. Cite evidence, no invented facts."
    )


class OrchestratorOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ranking: list[RankEntry] = Field(description="Every verdict with recommend=true, best first.")
    not_recommended: list[DiscardedCandidate] = Field(default_factory=list)
    summary: str = Field(description="3-5 sentences for the traveler, in the brief language.")
    caveats: list[str] = Field(
        default_factory=list, description="Data gaps, tool errors, things to double check."
    )


# ---------------------------------------------------------------------------
# Reporte final (ensamblado por código a partir de outputs trazables)
# ---------------------------------------------------------------------------


class RankedProperty(BaseModel):
    rank: int
    rationale: str
    verdict: PropertyVerdict


class RunStats(BaseModel):
    candidates_found: int = 0
    shortlisted: int = 0
    analyzed: int = 0
    serpapi_calls: int = 0
    tool_errors: int = 0
    duration_seconds: float = 0.0
    timed_out: bool = False
    orchestrator_model: str = ""
    analyst_model: str = ""


class FinalReport(BaseModel):
    run_id: str
    generated_at: str
    language: str
    brief: Brief
    summary: str
    ranking: list[RankedProperty] = Field(description="Todas las recomendadas, mejor primero.")
    verdicts: list[PropertyVerdict] = Field(description="Todos los verdicts de analistas, rankeados o no.")
    discarded: list[DiscardedCandidate] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    stats: RunStats = Field(default_factory=RunStats)


# ---------------------------------------------------------------------------
# Eventos de progreso (CLI + SSE)
# ---------------------------------------------------------------------------

EventType = Literal[
    "run_started",
    "assistant",
    "suggestions",
    "brief_saved",
    "model",
    "turn_done",
    "phase",
    "tool_call",
    "tool_result",
    "subagent_started",
    "subagent_finished",
    "verdict",
    "message",
    "warning",
    "run_finished",
    "run_failed",
]


class RunEvent(BaseModel):
    type: EventType
    run_id: str
    ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    agent: str = "orchestrator"
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


def now_iso() -> str:
    return datetime.now(UTC).isoformat()
