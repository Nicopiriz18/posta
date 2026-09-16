"""Tools de web abierta: `fetch_page` (leer una página) y `add_web_candidate` (registrar un alojamiento hallado en la web).

La BÚSQUEDA web la hace la tool server-side de Anthropic (`WEB_SEARCH_TOOL`, variante básica: títulos y URLs
legibles, que el runner loguea). Estas dos tools son client-side para que todo lo que llega al reporte sea
trazable a un output de la misma corrida (regla 12):

- `fetch_page` guarda el texto extraído de la página en `ctx.pages` y en `tool_outputs`.
- `add_web_candidate` solo acepta precios que aparezcan literalmente en una página ya leída, y unifica por nombre
  con candidatos de Google Hotels (una sola ficha, varios precios).
"""

from __future__ import annotations

import difflib
import hashlib
import logging
import math
import re
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from langchain_core.tools import tool

from lodging import config
from lodging.runtime import get_context, norm_name
from lodging.schemas import Candidate, PriceOffer, ToolError, ToolErrorInfo, now_iso

log = logging.getLogger(__name__)

# Búsqueda web server-side de Anthropic (variante básica: devuelve title/url por resultado; la 2026 los cifra).
WEB_SEARCH_TOOL: dict[str, Any] = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": config.MAX_WEB_SEARCHES_PER_RUN,
}

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36 posta-lodging-agent"
_http: httpx.AsyncClient | None = None


def get_http() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(
            timeout=config.FETCH_TIMEOUT_SECONDS,
            follow_redirects=True,
            headers={"User-Agent": _UA, "Accept-Language": "es,pt,en;q=0.8"},
        )
    return _http


def set_http(client: httpx.AsyncClient | None) -> None:
    """Para tests: inyectar un cliente con MockTransport."""
    global _http
    _http = client


def _domain(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _err(tool_name: str, kind: str, message: str, params: dict[str, Any]) -> dict[str, Any]:
    ctx = get_context()
    info = ToolErrorInfo(type=kind, message=message, retryable=False, tool=tool_name, params=params)  # type: ignore[arg-type]
    out = ToolError(error=info).model_dump()
    if ctx is not None:
        ctx.tool_errors += 1
        ctx.log_tool(tool_name, params, out)
        ctx.emit(
            "tool_result",
            f"{tool_name} falló: {message}",
            agent="hotel-discovery",
            data={"tool": tool_name, "ok": False, "error": message, "summary": message},
        )
    return out


def extract_text(html: str) -> tuple[str, str]:
    """(title, texto legible). Usa trafilatura; si no extrae nada, cae a un strip de tags."""
    title = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    if m:
        title = re.sub(r"\s+", " ", m.group(1)).strip()[:200]
    text = ""
    try:
        import trafilatura

        text = trafilatura.extract(html, include_tables=True, include_links=False, favor_recall=True) or ""
    except Exception as e:  # noqa: BLE001
        log.debug("trafilatura failed: %s", e)
    if not text.strip():
        stripped = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", html, flags=re.I | re.S)
        stripped = re.sub(r"<[^>]+>", " ", stripped)
        text = re.sub(r"\s+", " ", stripped).strip()
    # Trafilatura suele podar encabezados y tablas cortas, y los precios viven ahí: se agregan aparte.
    extras: list[str] = []
    headings: list[str] = []
    for h in re.findall(r"<h[1-3][^>]*>(.*?)</h[1-3]>", html, flags=re.I | re.S):
        h_txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h)).strip()
        if h_txt and len(headings) < 8:
            headings.append(h_txt[:120])
        if h_txt and h_txt not in text:
            extras.append(h_txt)
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.I | re.S):
        cells = [
            re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c)).strip()
            for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.I | re.S)
        ]
        line = " | ".join(c for c in cells if c)
        if line and line not in text:
            extras.append(line)
    if extras:
        text = (text + "\n" + "\n".join(extras)).strip()
    return title, text, headings


@tool
async def fetch_page(url: str) -> dict[str, Any]:
    """Read a web page (a property's own site, a local rental agency, a pousada listing) and return its readable text.

    Use it after `web_search` to look at a promising result, or to verify a web candidate. Returns
    `{url, final_url, domain, title, text, chars, truncated}`; `text` is the page's readable content (max
    {max_chars} chars). Prices, capacity, amenities and location statements in `text` are evidence; anything not
    in `text` is unknown. Booking portals (Airbnb, Booking, Expedia, Hoteis.com, Tripadvisor) cannot be read:
    their properties are already covered by Google Hotels. Same page twice = cached, free.

    On failure returns `{error: {...}}`: move on to another page.
    """
    tool_name = "fetch_page"
    ctx = get_context()
    params = {"url": url}
    url = (url or "").strip()
    if not re.match(r"^https?://", url):
        return _err(tool_name, "invalid_params", "url must start with http:// or https://", params)
    domain = _domain(url)
    if any(domain == d or domain.endswith("." + d) for d in config.BLOCKED_FETCH_DOMAINS):
        return _err(
            tool_name,
            "invalid_params",
            f"{domain} blocks automated reading; its listings are already covered by Google Hotels. Skip it.",
            params,
        )
    if ctx is not None and url in ctx.pages:
        cached = ctx.pages[url]
        return {**cached, "cached": True}
    if ctx is not None and ctx.fetch_calls >= config.MAX_FETCHES_PER_RUN:
        return _err(
            tool_name,
            "invalid_params",
            f"Page-read cap reached ({config.MAX_FETCHES_PER_RUN} per run). Work with what you have.",
            params,
        )
    if ctx is not None:
        ctx.fetch_calls += 1
        ctx.emit(
            "tool_call",
            f"Leyendo {domain}",
            agent="hotel-discovery",
            data={"tool": tool_name, "args": params},
        )
    try:
        resp = await get_http().get(url)
    except httpx.TimeoutException:
        return _err(
            tool_name, "timeout", f"{domain} did not answer in {config.FETCH_TIMEOUT_SECONDS:.0f}s", params
        )
    except httpx.HTTPError as e:
        return _err(tool_name, "http", f"network error reading {domain}: {e}", params)
    if resp.status_code >= 400:
        return _err(tool_name, "http", f"{domain} returned HTTP {resp.status_code}", params)
    ctype = resp.headers.get("content-type", "")
    if "html" not in ctype and "text" not in ctype:
        return _err(
            tool_name, "invalid_params", f"{domain} returned {ctype or 'unknown content'}, not a page", params
        )
    body = resp.content[: config.FETCH_MAX_BYTES]
    html = body.decode(resp.encoding or "utf-8", errors="replace")
    title, text, headings = extract_text(html)
    if len(text) < config.PAGE_TEXT_MIN_CHARS:
        return _err(
            tool_name,
            "invalid_params",
            f"{domain} has no readable content (JavaScript-only page or empty). Skip it; do not retry.",
            params,
        )
    truncated = len(text) > config.PAGE_TEXT_MAX_CHARS
    text = text[: config.PAGE_TEXT_MAX_CHARS]
    result = {
        "url": url,
        "final_url": str(resp.url),
        "domain": _domain(str(resp.url)),
        "title": title,
        "headings": headings,
        "text": text,
        "chars": len(text),
        "truncated": truncated,
        "links": extract_links(html, str(resp.url)),
        "fetched_at": now_iso(),
    }
    if ctx is not None:
        ctx.pages[url] = result
        ctx.log_tool(tool_name, params, result)
        ctx.emit(
            "tool_result",
            f"{domain}: {len(text)} caracteres leídos",
            agent="hotel-discovery",
            data={"tool": tool_name, "ok": True, "summary": f"{title or domain} ({len(text)} chars)"},
        )
    return result


_LINK_RE = re.compile(r"<a\b[^>]*?href\s*=\s*[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
_SKIP_LINK_SCHEMES = ("mailto:", "tel:", "javascript:", "whatsapp:")


def extract_links(html: str, base_url: str, limit: int = config.PAGE_LINKS_MAX) -> list[dict[str, str]]:
    """Links del mismo sitio con su texto: sirven para ir de un listado a la página propia de cada propiedad."""
    base_domain = _domain(base_url)
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for href, inner in _LINK_RE.findall(html):
        href = href.strip()
        if not href or href.lower().startswith(_SKIP_LINK_SCHEMES):
            continue
        full = urljoin(base_url, href)
        if not full.startswith(("http://", "https://")) or _domain(full) != base_domain:
            continue
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", inner)).strip()[:80]
        if not text or full in seen or full.rstrip("/") == base_url.rstrip("/"):
            continue
        seen.add(full)
        out.append({"text": text, "url": full})
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# add_web_candidate
# ---------------------------------------------------------------------------

_NUM_RE = re.compile(r"\d[\d.,\s]*\d|\d")


def _norm_name(s: str) -> str:
    return norm_name(s)


def _name_tokens(name: str) -> list[str]:
    return [t for t in norm_name(name).split() if len(t) >= 3 or t.isdigit()]


def page_mentions(name: str, page: dict[str, Any]) -> tuple[bool, bool]:
    """(la página menciona la propiedad, la página ES la de esa propiedad: nombre en el título o al inicio)."""
    toks = _name_tokens(name)
    if not toks:
        return False, False
    needed = len(toks) if len(toks) <= 2 else math.ceil(len(toks) * 0.7)

    def hits(blob: str) -> int:
        words = set(norm_name(blob).split())
        return sum(1 for t in toks if t in words)

    mentioned = hits(page.get("title", "") + " " + page.get("text", "")) >= needed
    head = page.get("title", "") + " " + " ".join(page.get("headings") or [])
    is_property_page = mentioned and hits(head) >= needed
    return mentioned, is_property_page


def suggest_property_link(name: str, page: dict[str, Any]) -> str | None:
    """Entre los links de un listado, el que mejor coincide con el nombre de la propiedad."""
    toks = set(_name_tokens(name))
    best: tuple[float, str | None] = (0.0, None)
    for link in page.get("links") or []:
        lt = set(_name_tokens(link.get("text", ""))) | set(
            _name_tokens(link.get("url", "").rsplit("/", 1)[-1].replace("-", " "))
        )
        if not lt:
            continue
        score = len(toks & lt) / max(1, len(toks))
        if score > best[0]:
            best = (score, link.get("url"))
    return best[1] if best[0] >= 0.5 else None


def _price_in_text(amount: float, text: str) -> bool:
    """El monto tiene que aparecer literalmente en la página (2237 / 2.237 / 2,237 / 2 237 / 2237,00)."""
    if amount is None:
        return False
    n = int(round(amount))
    digits = str(n)
    for m in _NUM_RE.finditer(text):
        raw = re.sub(r"[.,\s]", "", m.group(0))
        if raw == digits or raw == digits + "00":
            return True
        # decimales: "2.237,50" → 223750
        if raw[:-2] == digits and len(raw) == len(digits) + 2:
            return True
    return False


def _detect_currency(text: str, brief_currency: str) -> str | None:
    t = text.lower()
    if "r$" in t or " brl" in t or "reais" in t:
        return "BRL"
    if "us$" in t or "usd" in t or "u$s" in t or "dólares" in t or "dolares" in t:
        return "USD"
    if "€" in t or " eur" in t or "euros" in t:
        return "EUR"
    if "$u" in t or "uyu" in t or "pesos uruguayos" in t:
        return "UYU"
    if "ars" in t or "pesos argentinos" in t:
        return "ARS"
    if "$" in t:
        return brief_currency
    return None


def find_match(ctx, name: str) -> Candidate | None:
    """Candidato existente con el mismo nombre (normalizado). Ante la duda, no unifica."""
    wanted = _norm_name(name)
    if len(wanted) < 4:
        return None
    best: tuple[float, Candidate | None] = (0.0, None)
    for cand in ctx.candidates.values():
        have = _norm_name(cand.name)
        if not have:
            continue
        if have == wanted or (len(wanted) >= 8 and (wanted in have or have in wanted)):
            return cand
        ratio = difflib.SequenceMatcher(None, have, wanted).ratio()
        if ratio > best[0]:
            best = (ratio, cand)
    return best[1] if best[0] >= 0.9 else None


@tool
async def add_web_candidate(
    name: str,
    url: str,
    price_total: float | None = None,
    price_per_night: float | None = None,
    currency: str | None = None,
    property_type: str | None = None,
    location_text: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Register a lodging found on the open web (a pousada's own site, a local rental agency, a house listing).

    Call it ONLY after `fetch_page(url)` on the page that shows that exact property: prices are accepted only if the
    amount appears literally in that page's text (otherwise they are dropped and marked unverified). Give the stay
    total in `price_total` and/or the nightly rate in `price_per_night`, with `currency` (ISO code as shown on the
    page). `location_text` = how the page describes the location ("a 300 m da praia do Rosa Norte").

    Register it from the property's OWN page (the url that opens exactly that house), not from a listing of many
    properties: the traveler needs the exact link. If you only have a listing, follow its `links` first. The result
    says `is_property_page`; when false, `note` suggests the right link.

    If the same property already exists (from Google Hotels or an earlier listing), this adds the page's price as
    another offer on it (returns `merged_into`) and upgrades its link to the property page. Returns
    `{property_token, stored, url, is_property_page, price_verified, note}`.
    """
    tool_name = "add_web_candidate"
    ctx = get_context()
    params = {
        "name": name,
        "url": url,
        "price_total": price_total,
        "price_per_night": price_per_night,
        "currency": currency,
        "property_type": property_type,
        "location_text": location_text,
    }
    if ctx is None or ctx.brief is None:
        return _err(tool_name, "invalid_params", "No confirmed brief in this conversation", params)
    url = (url or "").strip()
    page = ctx.pages.get(url)
    if page is None:
        return _err(
            tool_name,
            "invalid_params",
            "Call fetch_page(url) first: prices are verified against the page",
            params,
        )
    text = page.get("text", "")
    mentioned, is_property_page = page_mentions(name, page)
    if not mentioned:
        return _err(
            tool_name,
            "invalid_params",
            f"The page {url} does not mention '{name}'. Register a property only from a page that shows it "
            "(follow the listing's `links` to the property's own page).",
            params,
        )
    notes_out: list[str] = []
    if not is_property_page:
        hint = suggest_property_link(name, page)
        notes_out.append(
            "url is a listing page, not this property's own page"
            + (f"; its own page seems to be {hint}: fetch it and register from there" if hint else "")
            + ". The traveler needs the exact link."
        )
    verified_total = price_total if price_total and _price_in_text(price_total, text) else None
    verified_night = price_per_night if price_per_night and _price_in_text(price_per_night, text) else None
    if price_total and verified_total is None:
        notes_out.append(f"price_total {price_total} not found on the page; dropped")
    if price_per_night and verified_night is None:
        notes_out.append(f"price_per_night {price_per_night} not found on the page; dropped")
    cur = (currency or "").upper()[:3] or _detect_currency(text, ctx.brief.currency)
    if (verified_total or verified_night) and not cur:
        notes_out.append("currency unknown; price dropped")
        verified_total = verified_night = None
    domain = page.get("domain") or _domain(url)
    verified_at = page.get("fetched_at") or now_iso()

    match = find_match(ctx, name)
    if match is not None:
        offer = PriceOffer(source=domain, total=verified_total, per_night=verified_night, link=url)
        if not any(o.source == domain for o in match.offers):
            match.offers.append(offer)
        if match.source == "web" and is_property_page and not match.url_is_property_page:
            # Antes se registró desde un listado: ahora tenemos la página propia, que es el link exacto.
            match.url = match.link = url
            match.url_is_property_page = True
            if verified_total and not match.total_rate:
                match.total_rate = verified_total
            if verified_night and not match.rate_per_night:
                match.rate_per_night = verified_night
        _rewrite_candidates(ctx)
        out = {
            "property_token": match.property_token,
            "merged_into": match.name,
            "stored": True,
            "url": match.url or url,
            "is_property_page": bool(match.url_is_property_page)
            if match.source == "web"
            else is_property_page,
            "price_verified": verified_total is not None or verified_night is not None,
            "currency": cur,
            "price_total": verified_total,
            "price_per_night": verified_night,
            "source": domain,
            "price_verified_at": verified_at,
            "note": "; ".join(notes_out) or None,
        }
        ctx.log_tool(tool_name, params, out)
        ctx.emit(
            "tool_result",
            f"{match.name}: precio adicional de {domain}",
            agent="hotel-discovery",
            data={"tool": tool_name, "ok": True, "summary": f"unificado con {match.name}"},
        )
        return out

    token = "web:" + hashlib.sha1(page.get("final_url", url).encode()).hexdigest()[:12]
    cand = Candidate(
        property_token=token,
        name=name.strip(),
        type=property_type,
        rate_per_night=verified_night,
        total_rate=verified_total,
        currency=cur or ctx.brief.currency,
        price_source=domain,
        price_verified_at=verified_at,
        essential_info=[location_text] if location_text else [],
        link=url,
        source="web",
        url=url,
        url_is_property_page=is_property_page,
    )
    added, dropped = ctx.add_candidates([cand])
    if not added and dropped:
        return _err(tool_name, "invalid_params", f"Candidate cap of {config.MAX_CANDIDATES} reached", params)
    _rewrite_candidates(ctx)
    out = {
        "property_token": token,
        "stored": True,
        "url": url,
        "is_property_page": is_property_page,
        "price_verified": verified_total is not None or verified_night is not None,
        "currency": cand.currency,
        "price_total": verified_total,
        "price_per_night": verified_night,
        "source": domain,
        "price_verified_at": verified_at,
        "note": "; ".join(notes_out) or None,
    }
    ctx.log_tool(tool_name, params, out)
    ctx.emit(
        "tool_result",
        f"Candidato web: {cand.name} ({domain})",
        agent="hotel-discovery",
        data={"tool": tool_name, "ok": True, "summary": f"{cand.name} vía {domain}"},
    )
    return out


def _rewrite_candidates(ctx) -> None:
    from lodging.tools.hotels import _write_candidates_file

    _write_candidates_file(ctx)


WEB_TOOLS = [fetch_page, add_web_candidate]

fetch_page.description = fetch_page.description.replace("{max_chars}", str(config.PAGE_TEXT_MAX_CHARS))
