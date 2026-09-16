"""Las 3 tools de datos: `search_hotels`, `get_property_details`, `get_property_reviews`.

Los docstrings son PARTE DEL PROMPT: están escritos para el modelo.

Cada tool: normaliza y recorta (regla 1), devuelve solo campos de `schemas.py`, nunca JSON crudo;
ante error devuelve `ToolError` estructurado (regla 7); loguea su output en el `RunContext`
para trazabilidad (regla 12).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Literal

from langchain_core.tools import tool

from lodging import config
from lodging.runtime import get_context
from lodging.schemas import (
    Candidate,
    NearbyPlace,
    PriceOffer,
    PropertyDetails,
    Review,
    ReviewCategory,
    ReviewsResult,
    SearchResult,
    ToolError,
    ToolErrorInfo,
    now_iso,
)
from lodging.tools.serpapi_client import SerpApiError, get_client

log = logging.getLogger(__name__)

SORT_BY = {"relevance": None, "lowest_price": "3", "highest_rating": "8", "most_reviewed": "13"}
MAX_HOTEL_TRAVELERS = 6  # límite de Google Hotels para búsqueda de hoteles (no aplica a vacation rentals)
REVIEWS_SORT = {"helpful": "1", "recent": "2", "highest": "3", "lowest": "4"}
RATING_FILTER = {"3.5": "7", "4.0": "8", "4.5": "9"}


# ---------------------------------------------------------------------------
# Normalización (regla 1)
# ---------------------------------------------------------------------------


def _num(d: dict[str, Any] | None, key: str = "extracted_lowest") -> float | None:
    if not isinstance(d, dict):
        return None
    v = d.get(key)
    return float(v) if isinstance(v, int | float) else None


def _hotel_class(p: dict[str, Any]) -> int | None:
    v = p.get("extracted_hotel_class")
    if isinstance(v, int):
        return v
    hc = p.get("hotel_class")
    if isinstance(hc, str):
        m = re.search(r"\d", hc)
        if m:
            return int(m.group())
    return None


def _nearby(p: dict[str, Any], limit: int = 6) -> list[NearbyPlace]:
    out: list[NearbyPlace] = []
    for np in (p.get("nearby_places") or [])[:limit]:
        if not isinstance(np, dict) or not np.get("name"):
            continue
        trans = [
            f"{t.get('type')} {t.get('duration')}".strip()
            for t in (np.get("transportations") or [])
            if isinstance(t, dict) and t.get("duration")
        ]
        out.append(NearbyPlace(name=np["name"], category=np.get("category"), transportations=trans))
    return out


def _price_source(p: dict[str, Any]) -> str | None:
    for src in (p.get("prices") or []) + (p.get("featured_prices") or []):
        if isinstance(src, dict) and src.get("source"):
            return str(src["source"])
    return None


def _free_cancellation(p: dict[str, Any]) -> bool | None:
    vals = [
        src.get("free_cancellation")
        for src in (p.get("prices") or [])
        if isinstance(src, dict) and "free_cancellation" in src
    ]
    if not vals:
        return None
    return any(bool(v) for v in vals)


def normalize_candidate(p: dict[str, Any], currency: str, verified_at: str) -> Candidate | None:
    token = p.get("property_token")
    name = p.get("name")
    if not token or not name:
        return None
    gps = p.get("gps_coordinates") or {}
    return Candidate(
        property_token=token,
        name=name,
        type=p.get("type"),
        hotel_class=_hotel_class(p),
        overall_rating=p.get("overall_rating") if isinstance(p.get("overall_rating"), int | float) else None,
        reviews_count=p.get("reviews") if isinstance(p.get("reviews"), int) else None,
        location_rating=p.get("location_rating")
        if isinstance(p.get("location_rating"), int | float)
        else None,
        rate_per_night=_num(p.get("rate_per_night")),
        total_rate=_num(p.get("total_rate")),
        total_rate_before_taxes=_num(p.get("total_rate"), "extracted_before_taxes_fees"),
        currency=currency,
        price_source=_price_source(p),
        price_verified_at=verified_at,
        free_cancellation=_free_cancellation(p),
        amenities=[str(a) for a in (p.get("amenities") or [])][:20],
        excluded_amenities=[str(a) for a in (p.get("excluded_amenities") or [])][:10],
        essential_info=[str(a) for a in (p.get("essential_info") or [])][:8],
        latitude=gps.get("latitude"),
        longitude=gps.get("longitude"),
        link=p.get("link"),
        nearby_places=_nearby(p, limit=3),
    )


def normalize_details(
    d: dict[str, Any], property_token: str, currency: str, verified_at: str
) -> PropertyDetails:
    base = normalize_candidate(
        {**d, "property_token": d.get("property_token") or property_token}, currency, verified_at
    )
    if base is None:
        raise ValueError("SerpAPI no devolvió nombre/token para la propiedad")
    offers: list[PriceOffer] = []
    seen: set[str] = set()
    for src in (d.get("prices") or []) + (d.get("featured_prices") or []):
        if not isinstance(src, dict) or not src.get("source") or src["source"] in seen:
            continue
        seen.add(src["source"])
        offers.append(
            PriceOffer(
                source=str(src["source"]),
                total=_num(src.get("total_rate")),
                per_night=_num(src.get("rate_per_night")),
                free_cancellation=src.get("free_cancellation") if "free_cancellation" in src else None,
                link=src.get("link"),
            )
        )
    hist = {
        str(r["stars"]): int(r["count"])
        for r in (d.get("ratings") or [])
        if isinstance(r, dict) and "stars" in r and isinstance(r.get("count"), int)
    }
    cats = [
        ReviewCategory(
            name=str(c.get("description") or c.get("name")),
            total=int(c.get("total_mentioned") or 0),
            positive=int(c.get("positive") or 0),
            negative=int(c.get("negative") or 0),
            neutral=int(c.get("neutral") or 0),
        )
        for c in (d.get("reviews_breakdown") or [])
        if isinstance(c, dict) and (c.get("name") or c.get("description"))
    ]
    return PropertyDetails(
        **base.model_dump(exclude={"nearby_places", "offers"}),
        nearby_places=_nearby(d, limit=8),
        address=d.get("address"),
        phone=d.get("phone"),
        check_in_time=d.get("check_in_time"),
        check_out_time=d.get("check_out_time"),
        offers=offers[:8],
        ratings_histogram=hist,
        review_categories=cats[:10],
    )


def normalize_review(r: dict[str, Any]) -> Review:
    rating = r.get("rating") if isinstance(r.get("rating"), int | float) else None
    best = r.get("best_rating") if isinstance(r.get("best_rating"), int | float) else None
    norm = None
    if rating is not None:
        norm = round(rating * 5 / best, 1) if best and best > 0 else float(rating)
    text = re.sub(r"<br\s*/?>", " ", str(r.get("snippet") or ""))
    text = re.sub(r"\s+", " ", text).strip()[: config.REVIEW_TEXT_MAX_CHARS]
    return Review(
        source=r.get("source"),
        rating=norm,
        rating_raw=float(rating) if rating is not None else None,
        best_rating=float(best) if best is not None else None,
        date_text=(r.get("date") or "").replace("\xa0", " ") or None,
        text=text,
        highlights=[str(h) for h in (r.get("hotel_highlights") or [])][:6],
        has_owner_response=bool(r.get("response")),
    )


# ---------------------------------------------------------------------------
# Helpers de contexto / VFS / errores
# ---------------------------------------------------------------------------


def _stay_params() -> dict[str, Any]:
    ctx = get_context()
    if ctx is None or ctx.brief is None:
        raise SerpApiError("invalid_params", "No brief saved yet: the main agent must call save_brief first.")
    if not ctx.confirmed:
        raise SerpApiError("invalid_params", "The traveler has not confirmed the brief yet (confirm_brief).")
    b = ctx.brief
    p: dict[str, Any] = {
        "check_in_date": b.check_in.isoformat(),
        "check_out_date": b.check_out.isoformat(),
        "adults": b.adults,
        "children": b.children,
        "currency": b.currency,
        "hl": b.hl,
    }
    if b.children_ages:
        p["children_ages"] = ",".join(str(a) for a in b.children_ages)
    if b.gl:
        p["gl"] = b.gl
    return p


MAX_ATTEMPTS_PER_PROPERTY = 3  # fallas seguidas de una tool para una misma propiedad antes de cortar


def _too_many_attempts(tool: str, token: str) -> str | None:
    """Corta el reintento infinito de un analista: tras N fallas, la tool devuelve un error definitivo."""
    ctx = get_context()
    if ctx is None:
        return None
    n = ctx.tool_attempts.get((tool, token), 0)
    if n >= MAX_ATTEMPTS_PER_PROPERTY:
        return f"{tool} already failed {n} times for this property. Do not retry: treat its data as unknown."
    return None


def _count_failure(tool: str, token: str) -> None:
    ctx = get_context()
    if ctx is not None:
        ctx.tool_attempts[(tool, token)] = ctx.tool_attempts.get((tool, token), 0) + 1


def _error(tool: str, e: Exception, params: dict[str, Any]) -> dict[str, Any]:
    ctx = get_context()
    if isinstance(e, SerpApiError):
        info = ToolErrorInfo(type=e.kind, message=e.message, retryable=e.retryable, tool=tool, params=params)  # type: ignore[arg-type]
    else:
        info = ToolErrorInfo(
            type="unknown", message=f"{type(e).__name__}: {e}", retryable=False, tool=tool, params=params
        )
    out = ToolError(error=info).model_dump()
    if ctx:
        ctx.tool_errors += 1
        ctx.log_tool(tool, params, out)
        ctx.emit(
            "tool_result",
            f"{tool} falló: {info.message}",
            agent=_agent_for(tool),
            data={"tool": tool, "ok": False, "error": info.message, "summary": info.message},
        )
    log.warning("tool %s error: %s", tool, info.message)
    return out


def _agent_for(tool: str) -> str:
    return "hotel-discovery" if tool == "search_hotels" else "property-analyst"


def _write_candidates_file(ctx) -> bool:
    """Deposita /candidates/hotels.json en el filesystem virtual de deepagents (StateBackend).

    Usa el helper interno `_send_files_update` porque `write` no permite sobrescribir. Si no hay
    grafo activo (tests, uso directo), no hace nada.
    """
    try:
        from deepagents.backends import StateBackend
        from deepagents.backends.utils import create_file_data

        backend = StateBackend()
        data = backend._prepare_for_storage(create_file_data(ctx.candidates_file_content()))  # noqa: SLF001
        backend._send_files_update({config.VFS_CANDIDATES_PATH: data})  # noqa: SLF001
        return True
    except Exception as e:  # noqa: BLE001 — fuera del grafo no hay config; no es un error
        log.debug("VFS write skipped: %s", e)
        return False


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@tool
async def search_hotels(
    query: str,
    sort_by: Literal["relevance", "lowest_price", "highest_rating", "most_reviewed"] = "relevance",
    max_price: int | None = None,
    min_rating: Literal["3.5", "4.0", "4.5"] | None = None,
    vacation_rentals: bool = False,
    page_token: str | None = None,
) -> dict[str, Any]:
    """Search Google Hotels for lodging matching the trip brief (dates, guests and currency are taken from the brief automatically).

    Use it in the discovery phase. `query` is a natural search like "Hotels in Palermo, Buenos Aires" or
    "apartments near Recoleta, Buenos Aires". Add `max_price` (total per night, in the brief currency) when the
    brief has a budget; use `vacation_rentals=True` to search apartments/houses instead of hotels; use
    `page_token` (from a previous result's `next_page_token`) to get the next page.

    Returns `{query, candidates: [Candidate...], next_page_token, total_returned, note}`. Each Candidate has:
    property_token, name, type, hotel_class, overall_rating, reviews_count, location_rating, rate_per_night,
    total_rate (stay total WITH taxes), currency, price_source, price_verified_at, free_cancellation, amenities,
    excluded_amenities, essential_info, latitude/longitude, link, nearby_places. Amenities listed here are
    incomplete: a missing amenity is NOT evidence that the property lacks it.

    Every candidate returned is also appended to `/candidates/hotels.json` (deduplicated, capped). On failure
    returns `{error: {type, message, retryable}}`; you may retry once if `retryable` is true.
    """
    tool_name = "search_hotels"
    ctx = get_context()
    params: dict[str, Any] = {"engine": "google_hotels", "q": query}
    try:
        params.update(_stay_params())
        if SORT_BY.get(sort_by):
            params["sort_by"] = SORT_BY[sort_by]
        if max_price:
            params["max_price"] = int(max_price)
        if min_rating and min_rating in RATING_FILTER:
            params["rating"] = RATING_FILTER[min_rating]
        travelers = (ctx.brief.adults + ctx.brief.children) if ctx and ctx.brief else 0
        auto_note = None
        if travelers > MAX_HOTEL_TRAVELERS and not vacation_rentals:
            # Google Hotels rechaza >6 viajeros en hoteles; casas/departamentos sí aceptan grupos grandes.
            vacation_rentals = True
            auto_note = (
                f"Group of {travelers} travelers: Google Hotels only searches hotels for up to {MAX_HOTEL_TRAVELERS}, "
                "so this search was switched to vacation rentals (houses/apartments) automatically."
            )
        if vacation_rentals:
            params["vacation_rentals"] = "true"
        if page_token:
            params["next_page_token"] = page_token

        if ctx is not None and ctx.search_calls >= config.MAX_SEARCH_CALLS_PER_RUN:
            raise SerpApiError(
                "invalid_params",
                f"Search cap reached ({config.MAX_SEARCH_CALLS_PER_RUN} successful searches per run). "
                "Work with the candidates you already have.",
            )
        if ctx is not None and ctx.search_attempts >= 2 * config.MAX_SEARCH_CALLS_PER_RUN:
            raise SerpApiError(
                "invalid_params", "Too many failed searches in this run; stop searching and report."
            )
        if ctx is not None:
            ctx.search_attempts += 1
            ctx.emit(
                "tool_call",
                f"Buscando: {query}",
                agent="hotel-discovery",
                data={
                    "tool": tool_name,
                    "args": {
                        "query": query,
                        "sort_by": sort_by,
                        "max_price": max_price,
                        "vacation_rentals": vacation_rentals,
                    },
                },
            )

        raw = await get_client().get(params, use_cache=True)
        if ctx is not None:
            ctx.search_calls += 1  # solo las que SerpAPI respondió cuentan para el cap
        verified_at = now_iso()
        currency = params.get("currency", "USD")
        cands = [
            c
            for c in (normalize_candidate(p, currency, verified_at) for p in raw.get("properties") or [])
            if c
        ]
        notes = [auto_note] if auto_note else []
        if not cands:
            info = raw.get("search_information") or {}
            notes.append(
                "0 results. "
                + (
                    str(info)
                    if info
                    else "Try a shorter query (just the place name) or vacation_rentals=true."
                )
            )
        if ctx is not None:
            before_zone = len(ctx.zone_discards)
            added, dropped = ctx.add_candidates(cands)
            outside = len(ctx.zone_discards) - before_zone
            if outside:
                notes.append(
                    f"{outside} results are outside the zone the traveler drew on the map and were dropped "
                    "(geometry computed by code; they appear as discarded in the report)."
                )
            if dropped - outside > 0:
                notes.append(
                    f"{dropped - outside} candidates not stored: cap of {config.MAX_CANDIDATES} reached."
                )
            _write_candidates_file(ctx)
        note = " ".join(notes) or None
        result = SearchResult(
            query=query,
            candidates=cands,
            next_page_token=(raw.get("serpapi_pagination") or {}).get("next_page_token"),
            total_returned=len(cands),
            note=note,
        ).model_dump(exclude_none=True)
        if ctx is not None:
            ctx.log_tool(tool_name, {k: v for k, v in params.items() if k != "api_key"}, result)
            ctx.emit(
                "tool_result",
                f"{len(cands)} resultados para «{query}»",
                agent="hotel-discovery",
                data={"tool": tool_name, "ok": True, "summary": f"{len(cands)} candidatos"},
            )
        return result
    except Exception as e:  # noqa: BLE001
        return _error(tool_name, e, {k: v for k, v in params.items() if k != "api_key"})


@tool
async def get_property_details(property_token: str) -> dict[str, Any]:
    """Get the full detail page of one property (dates/guests/currency come from the brief).

    Use it once per property you analyze. Returns a PropertyDetails: everything a Candidate has plus address,
    phone, check-in/out times, `offers` (per booking source: total with taxes, per_night, free_cancellation,
    link), `ratings_histogram` ({"5": 95, "4": 21, ...}), `review_categories` (Google's mention counts:
    positive/negative per topic such as Location, Service, Property) and up to 8 `nearby_places` with travel times.

    `total_rate` and `price_source` are the lowest stay total with taxes and its source; `price_verified_at`
    is when it was fetched. Copy them verbatim into your verdict, never estimate a price.

    On failure returns `{error: {...}}`: treat the affected fields as unknown and continue.
    """
    tool_name = "get_property_details"
    ctx = get_context()
    params: dict[str, Any] = {"engine": "google_hotels", "property_token": property_token}
    try:
        stop = _too_many_attempts(tool_name, property_token)
        if stop:
            raise SerpApiError("invalid_params", stop)
        if property_token.startswith("web:"):
            cand = ctx.candidates.get(property_token) if ctx else None
            raise SerpApiError(
                "invalid_params",
                "This is a web candidate, not a Google Hotels property: use fetch_page on its page instead"
                + (f" ({cand.url})" if cand and cand.url else "."),
            )
        params.update(_stay_params())
        params["q"] = ctx.brief.destination if ctx and ctx.brief else "hotels"
        travelers = (ctx.brief.adults + ctx.brief.children) if ctx and ctx.brief else 0
        if travelers > MAX_HOTEL_TRAVELERS:
            params["vacation_rentals"] = "true"  # la ficha también rechaza >6 viajeros salvo en modo casas
        if ctx is not None:
            ctx.emit(
                "tool_call",
                "Leyendo ficha de la propiedad",
                agent="property-analyst",
                data={"tool": tool_name, "args": {"property_token": property_token}},
            )
        raw = await get_client().get(params)
        details = normalize_details(raw, property_token, params.get("currency", "USD"), now_iso())
        result = details.model_dump(exclude_none=True)
        if ctx is not None:
            ctx.log_tool(tool_name, {k: v for k, v in params.items() if k != "api_key"}, result)
            ctx.emit(
                "tool_result",
                f"Ficha de {details.name}: {len(details.offers)} ofertas",
                agent="property-analyst",
                data={
                    "tool": tool_name,
                    "ok": True,
                    "summary": f"{details.name}: {len(details.offers)} ofertas, {len(details.review_categories)} categorías de reviews",
                    "property_name": details.name,
                },
            )
        return result
    except Exception as e:  # noqa: BLE001
        _count_failure(tool_name, property_token)
        return _error(tool_name, e, {k: v for k, v in params.items() if k != "api_key"})


@tool
async def get_property_reviews(
    property_token: str,
    max_reviews: int = 10,
    sort_by: Literal["recent", "helpful", "highest", "lowest"] = "recent",
) -> dict[str, Any]:
    """Get recent guest reviews for one property.

    `max_reviews` is capped at {max_cap}; each page costs one API call (10 reviews per page), so 10 is usually
    enough and 20 is the sensible maximum. `sort_by="recent"` for current state of the property, "lowest" to
    surface complaints.

    Returns `{property_token, reviews: [{source, rating (0-5), date_text, text, highlights, has_owner_response}],
    fetched, pages, sort_by}`. `text` is truncated and for your internal analysis only: NEVER quote it verbatim
    to the user; aggregate and paraphrase ("4 of 10 recent reviews mention street noise").

    On failure returns `{error: {...}}`: report reviews as unknown and continue.
    """
    tool_name = "get_property_reviews"
    ctx = get_context()
    hl = ctx.brief.hl if ctx and ctx.brief else "en"
    n = max(1, min(int(max_reviews), config.MAX_REVIEWS_PER_CALL))
    params: dict[str, Any] = {
        "engine": "google_hotels_reviews",
        "property_token": property_token,
        "hl": hl,
        "sort_by": REVIEWS_SORT.get(sort_by, "2"),
    }
    try:
        stop = _too_many_attempts(tool_name, property_token)
        if stop:
            raise SerpApiError("invalid_params", stop)
        if property_token.startswith("web:"):
            cand = ctx.candidates.get(property_token) if ctx else None
            raise SerpApiError(
                "invalid_params",
                "This is a web candidate, not a Google Hotels property: use fetch_page on its page instead"
                + (f" ({cand.url})" if cand and cand.url else "."),
            )
        if ctx is not None:
            ctx.emit(
                "tool_call",
                f"Leyendo hasta {n} reviews",
                agent="property-analyst",
                data={
                    "tool": tool_name,
                    "args": {"property_token": property_token, "max_reviews": n, "sort_by": sort_by},
                },
            )
        client = get_client()
        reviews: list[Review] = []
        pages = 0
        page_token: str | None = None
        while len(reviews) < n:
            p = dict(params)
            if page_token:
                p["next_page_token"] = page_token
            raw = await client.get(p)
            pages += 1
            batch = [normalize_review(r) for r in raw.get("reviews") or [] if isinstance(r, dict)]
            reviews.extend(batch)
            page_token = (raw.get("serpapi_pagination") or {}).get("next_page_token")
            if not batch or not page_token:
                break
        reviews = reviews[:n]
        result = ReviewsResult(
            property_token=property_token, reviews=reviews, fetched=len(reviews), pages=pages, sort_by=sort_by
        ).model_dump(exclude_none=True)
        if ctx is not None:
            ctx.log_tool(tool_name, params, result)
            ctx.emit(
                "tool_result",
                f"{len(reviews)} reviews leídas",
                agent="property-analyst",
                data={
                    "tool": tool_name,
                    "ok": True,
                    "summary": f"{len(reviews)} reviews en {pages} página(s)",
                },
            )
        return result
    except Exception as e:  # noqa: BLE001
        _count_failure(tool_name, property_token)
        return _error(tool_name, e, params)


# Inyectar el cap real en el docstring (regla 9: el número vive en config)
get_property_reviews.description = get_property_reviews.description.replace(
    "{max_cap}", str(config.MAX_REVIEWS_PER_CALL)
)

DATA_TOOLS = [search_hotels, get_property_details, get_property_reviews]
