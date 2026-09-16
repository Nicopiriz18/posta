"""Contexto de conversación/corrida (`RunContext`), compartido por tools, runner y API vía contextvar.

Un `RunContext` vive por hilo de conversación (thread). Guarda: el brief (draft y confirmado), el log de
tool outputs (la fuente de trazabilidad de las assertions), los eventos de progreso (CLI/SSE), los verdicts
recibidos, el historial de chat y el último reporte.
"""

from __future__ import annotations

import asyncio
import contextvars
import difflib
import json
import logging
import math
import re
import secrets
import unicodedata
from datetime import UTC, datetime
from typing import Any

from lodging import config
from lodging.schemas import (
    Brief,
    BriefDraft,
    Candidate,
    DiscardedCandidate,
    DiscoveryOutput,
    FinalReport,
    PriceOffer,
    PropertyVerdict,
    RunEvent,
    now_iso,
)

log = logging.getLogger(__name__)

_current: contextvars.ContextVar[RunContext | None] = contextvars.ContextVar(
    "lodging_run_context", default=None
)

# Campos compactos que ve el orquestador en /candidates/hotels.json (una línea por candidato)
_CANDIDATE_FILE_FIELDS = (
    "property_token",
    "source",
    "url",
    "name",
    "type",
    "zone_inside",
    "zone_distance_m",
    "hotel_class",
    "overall_rating",
    "reviews_count",
    "location_rating",
    "total_rate",
    "rate_per_night",
    "currency",
    "price_source",
    "price_verified_at",
    "free_cancellation",
    "essential_info",
    "amenities",
)


_GENERIC_WORDS = (
    r"\b(hotel|pousada|posada|casa|apto|apartamento|apartment|hostel|the|el|la|los|las|de|do|da|del|em|en)\b"
)


def norm_name(s: str) -> str:
    """Nombre normalizado para unificar el mismo alojamiento entre fuentes."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(_GENERIC_WORDS, " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _distance_m(a: Candidate, b: Candidate) -> float | None:
    if None in (a.latitude, a.longitude, b.latitude, b.longitude):
        return None
    dlat = math.radians(b.latitude - a.latitude)
    dlon = math.radians(b.longitude - a.longitude)
    la1, la2 = math.radians(a.latitude), math.radians(b.latitude)
    h = math.sin(dlat / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlon / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def same_property(a: Candidate, b: Candidate) -> bool:
    """Mismo alojamiento físico: nombre normalizado igual/contenido (o muy parecido) y, si hay coordenadas, a < 150 m.

    Ante la duda no unifica: dos casas distintas con el mismo nombre genérico y sin coordenadas quedan separadas.
    """
    na, nb = norm_name(a.name), norm_name(b.name)
    if len(na) < 4 or len(nb) < 4:
        return False
    close = na == nb or (min(len(na), len(nb)) >= 8 and (na in nb or nb in na))
    if not close:
        close = difflib.SequenceMatcher(None, na, nb).ratio() >= 0.92
    if not close:
        return False
    dist = _distance_m(a, b)
    if dist is None:
        return na == nb  # sin ubicación, solo unifica nombres idénticos
    return dist < 150


def new_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


class RunContext:
    def __init__(self, brief: Brief | None = None, run_id: str | None = None):
        self.run_id = run_id or new_run_id()
        self.thread_id = self.run_id
        self.created_at = now_iso()
        self.events: list[RunEvent] = []
        self.queue: asyncio.Queue[RunEvent | None] | None = None
        self.messages: list[dict[str, str]] = []  # historial de chat [{role, content, ts}]
        self.draft = BriefDraft()
        self.brief: Brief | None = brief
        self.confirmed = brief is not None
        self.report: FinalReport | None = None
        self.reports: list[FinalReport] = []
        self.running = False
        self._reset_research_state()
        if brief is not None:
            self.draft = BriefDraft(**brief.model_dump(exclude={"nights"}))

    def _reset_research_state(self) -> None:
        self.tool_outputs: list[dict[str, Any]] = []
        self.candidates: dict[str, Candidate] = {}  # por property_token, en orden de descubrimiento
        self.discovery: DiscoveryOutput | None = None
        self.verdicts: dict[str, PropertyVerdict] = {}
        self.search_calls = 0  # búsquedas exitosas (cap MAX_SEARCH_CALLS_PER_RUN)
        self.search_attempts = 0  # intentos, incluidos fallidos (tope duro 2x)
        self.tool_attempts: dict[tuple[str, str], int] = {}  # (tool, token) -> intentos fallidos
        self.pages: dict[str, dict[str, Any]] = {}  # url -> página leída (fetch_page)
        self.merged_duplicates: dict[str, str] = {}  # token repetido -> token que lo representa
        self.zone_discards: list[DiscardedCandidate] = []  # fuera de la zona dibujada (geometría en código)
        self.fetch_calls = 0
        self.web_searches = 0
        self.tool_errors = 0
        self.warnings: list[str] = []
        self.research_started_at: float | None = None

    # -- brief -------------------------------------------------------------
    def set_brief(self, brief: Brief, *, confirmed: bool) -> None:
        """Guarda el brief. Si cambió respecto del anterior, arranca una investigación nueva (run_id nuevo)."""
        changed = self.brief is None or brief.model_dump() != self.brief.model_dump()
        self.brief = brief
        self.draft = BriefDraft(**brief.model_dump(exclude={"nights"}))
        self.confirmed = confirmed or (not changed and self.confirmed)
        if changed and (self.tool_outputs or self.report is not None):
            self.run_id = new_run_id()
            self.report = None
            self._reset_research_state()

    # -- eventos ---------------------------------------------------------
    def emit(
        self,
        type_: str,
        message: str = "",
        *,
        agent: str = "orchestrator",
        data: dict[str, Any] | None = None,
    ) -> RunEvent:
        ev = RunEvent(type=type_, run_id=self.run_id, agent=agent, message=message, data=data or {})
        self.events.append(ev)
        if self.queue is not None:
            self.queue.put_nowait(ev)
        log.info("[%s] %s %s", self.run_id, type_, message[:200])
        return ev

    def add_message(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content, "ts": now_iso()})

    # -- trazabilidad ------------------------------------------------------
    def log_tool(self, tool: str, args: dict[str, Any], output: dict[str, Any]) -> None:
        self.tool_outputs.append({"ts": now_iso(), "tool": tool, "args": args, "output": output})

    def add_candidates(self, cands: list[Candidate]) -> tuple[int, int]:
        """Agrega candidatos únicos respetando MAX_CANDIDATES. Devuelve (agregados, descartados_por_cap)."""
        added = dropped = 0
        zone = self.brief.zone if self.brief else None
        for c in cands:
            if c.property_token in self.candidates:
                continue
            if zone is not None and c.latitude is not None and c.longitude is not None:
                from lodging.tools.geo import zone_status

                inside, dist = zone_status(zone, c.latitude, c.longitude)
                c.zone_inside, c.zone_distance_m = inside, dist
                if inside is False:
                    label = zone.name or "la zona dibujada"
                    self.zone_discards.append(
                        DiscardedCandidate(
                            property_token=c.property_token,
                            name=c.name,
                            reason=f"Fuera de {label}: a {int(dist or 0)} m del borde",
                            stage="discovery",
                        )
                    )
                    dropped += 1
                    continue
            twin = next((x for x in self.candidates.values() if same_property(x, c)), None)
            if twin is not None:
                # Mismo alojamiento repetido por otro comparador: queda como oferta extra de la ficha original.
                src = c.price_source or c.source
                if (
                    src
                    and not any(o.source == src for o in twin.offers)
                    and (c.total_rate or c.rate_per_night)
                ):
                    twin.offers.append(
                        PriceOffer(
                            source=src,
                            total=c.total_rate,
                            per_night=c.rate_per_night,
                            free_cancellation=c.free_cancellation,
                            link=c.link,
                        )
                    )
                self.merged_duplicates[c.property_token] = twin.property_token
                continue
            if len(self.candidates) >= config.MAX_CANDIDATES:
                dropped += 1
                continue
            self.candidates[c.property_token] = c
            added += 1
        return added, dropped

    def candidates_file_content(self) -> str:
        """JSONL compacto: una línea por candidato, solo los campos que necesita el orquestador para elegir."""
        lines = []
        for c in self.candidates.values():
            d = c.model_dump(include=set(_CANDIDATE_FILE_FIELDS), exclude_none=True)
            if "amenities" in d:
                d["amenities"] = d["amenities"][:8]
            lines.append(json.dumps(d, ensure_ascii=False, separators=(",", ":")))
        return "\n".join(lines) + ("\n" if lines else "")

    # -- persistencia ------------------------------------------------------
    def run_dir(self):
        d = config.get_settings().runs_dir / self.run_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save_artifacts(self) -> None:
        d = self.run_dir()
        if self.brief is not None:
            (d / "brief.json").write_text(self.brief.model_dump_json(indent=2), encoding="utf-8")
        with (d / "tool_outputs.jsonl").open("w", encoding="utf-8") as f:
            for row in self.tool_outputs:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        with (d / "messages.json").open("w", encoding="utf-8") as f:
            json.dump(self.messages, f, ensure_ascii=False, indent=1)

    def save_events(self) -> None:
        with (self.run_dir() / "events.jsonl").open("w", encoding="utf-8") as f:
            for ev in self.events:
                f.write(ev.model_dump_json() + "\n")


def get_context() -> RunContext | None:
    return _current.get()


def require_context() -> RunContext:
    ctx = _current.get()
    if ctx is None:
        raise RuntimeError("No hay RunContext activo. Usar `with use_context(ctx):`.")
    return ctx


class use_context:
    """Context manager que activa un RunContext en el contextvar."""

    def __init__(self, ctx: RunContext):
        self.ctx = ctx
        self._token: contextvars.Token | None = None

    def __enter__(self) -> RunContext:
        self._token = _current.set(self.ctx)
        return self.ctx

    def __exit__(self, *exc: object) -> None:
        if self._token is not None:
            _current.reset(self._token)
