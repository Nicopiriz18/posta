"""Assertions sobre un reporte final contra los tool outputs de SU corrida.

Chequean las reglas de dominio: hard constraints respetadas, precios trazables, sin reviews
verbatim, idioma correcto, caps respetados, tokens trazables, unknowns bien clasificados y
fit_score coherente con la rúbrica.
"""

from __future__ import annotations

import re
from typing import Any

from lodging import config
from lodging.schemas import AnalystOutput, CheckStatus, FinalReport, FitRubric, PropertyVerdict

NGRAM = 7  # palabras consecutivas de una review que, si aparecen en el reporte, cuentan como verbatim

_ES_WORDS = {
    "el",
    "la",
    "los",
    "las",
    "de",
    "del",
    "con",
    "por",
    "para",
    "una",
    "un",
    "que",
    "es",
    "en",
    "y",
    "no",
    "se",
}
_EN_WORDS = {"the", "of", "with", "for", "and", "is", "in", "to", "a", "an", "that", "it", "on", "not", "are"}
_PT_WORDS = {"o", "a", "os", "as", "de", "do", "da", "com", "para", "um", "uma", "que", "é", "em", "e", "não"}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-záéíóúñüçãõ]+", text.lower())


def _ngrams(words: list[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)} if len(words) >= n else set()


def _report_texts(report: FinalReport) -> str:
    parts = [report.summary, *report.caveats]
    for r in report.ranking:
        parts.append(r.rationale)
    for v in report.verdicts:
        parts += [
            v.summary,
            v.reviews_summary,
            v.location_summary,
            *v.pros,
            *v.cons,
            *v.red_flags,
            *v.unknowns,
        ]
        parts += [c.evidence or "" for c in v.hard_constraints + v.soft_preferences]
    for d in report.discarded:
        parts.append(d.reason)
    return "\n".join(p for p in parts if p)


def _detect_language(text: str) -> str | None:
    w = _words(text)
    if len(w) < 8:
        return None
    scores = {
        "es": sum(1 for x in w if x in _ES_WORDS),
        "en": sum(1 for x in w if x in _EN_WORDS),
        "pt": sum(1 for x in w if x in _PT_WORDS),
    }
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else None


def check_run(report: FinalReport, tool_outputs: list[dict[str, Any]]) -> dict[str, Any]:
    """Devuelve {"ok": bool, "checks": {nombre: {"ok": bool, "details": [...]}}}."""
    checks: dict[str, dict[str, Any]] = {}

    def add(name: str, problems: list[str]) -> None:
        checks[name] = {"ok": not problems, "details": problems}

    # Índices de evidencia de la corrida
    tokens_seen: set[str] = set()
    prices_by_token: dict[str, set[tuple[float, str]]] = {}
    verified_by_token: dict[str, set[str]] = {}
    review_ngrams: set[tuple[str, ...]] = set()
    for row in tool_outputs:
        out = row.get("output") or {}
        if "error" in out:
            continue
        tool = row.get("tool")
        if tool == "search_hotels":
            for c in out.get("candidates", []):
                tokens_seen.add(c["property_token"])
                if c.get("total_rate") is not None and c.get("price_source"):
                    prices_by_token.setdefault(c["property_token"], set()).add(
                        (float(c["total_rate"]), c["price_source"])
                    )
                    verified_by_token.setdefault(c["property_token"], set()).add(c["price_verified_at"])
        elif tool == "get_property_details":
            tok = out.get("property_token")
            if tok:
                tokens_seen.add(tok)
                if out.get("total_rate") is not None and out.get("price_source"):
                    prices_by_token.setdefault(tok, set()).add(
                        (float(out["total_rate"]), out["price_source"])
                    )
                    verified_by_token.setdefault(tok, set()).add(out["price_verified_at"])
                for o in out.get("offers", []):
                    if o.get("total") is not None:
                        prices_by_token.setdefault(tok, set()).add((float(o["total"]), o["source"]))
                        verified_by_token.setdefault(tok, set()).add(out.get("price_verified_at", ""))
        elif tool == "get_property_reviews":
            for r in out.get("reviews", []):
                review_ngrams |= _ngrams(_words(r.get("text", "")), NGRAM)
        elif tool == "add_web_candidate":
            tok = out.get("property_token")
            if tok:
                tokens_seen.add(tok)
                nights = report.brief.nights
                for amount in (out.get("price_total"), out.get("price_per_night")):
                    if amount is not None and out.get("source"):
                        prices_by_token.setdefault(tok, set()).add((float(amount), out["source"]))
                        verified_by_token.setdefault(tok, set()).add(out.get("price_verified_at", ""))
                if out.get("price_per_night") is not None and out.get("source"):
                    # total derivado del precio por noche verificado (candidatos web sin total publicado)
                    prices_by_token.setdefault(tok, set()).add(
                        (float(out["price_per_night"]) * nights, out["source"])
                    )

    # 1. Hard constraints: violada => recommend=false, score <= cap, y no rankeada
    problems: list[str] = []
    ranked_tokens = {r.verdict.property_token for r in report.ranking}
    for v in report.verdicts:
        violated = any(c.status == CheckStatus.VIOLATED for c in v.hard_constraints)
        if violated and (v.recommend or v.fit_score > FitRubric.HARD_VIOLATED_MAX):
            problems.append(
                f"{v.name}: hard constraint violada pero recommend={v.recommend}, fit={v.fit_score}"
            )
        if violated and v.property_token in ranked_tokens:
            problems.append(f"{v.name}: rankeada con hard constraint violada")
        if not v.recommend and v.property_token in ranked_tokens:
            problems.append(f"{v.name}: rankeada con recommend=false")
    add("hard_constraints_respected", problems)

    # 2. Precios trazables a un tool output de la corrida
    problems = []
    for v in report.verdicts:
        if v.price is None:
            continue
        evid = prices_by_token.get(v.property_token, set())
        if not any(abs(v.price.total - t) < 0.01 and v.price.source == s for t, s in evid):
            problems.append(
                f"{v.name}: precio {v.price.total} vía {v.price.source} no coincide con ningún tool output"
            )
        if v.price.verified_at not in verified_by_token.get(v.property_token, set()):
            problems.append(f"{v.name}: price_verified_at no proviene de un tool output")
    add("prices_traceable", problems)

    # 3. Sin reviews verbatim
    problems = []
    if review_ngrams:
        text_ngrams = _ngrams(_words(_report_texts(report)), NGRAM)
        hits = text_ngrams & review_ngrams
        if hits:
            problems.append(
                f"{len(hits)} fragmento(s) de {NGRAM}+ palabras copiados de reviews, ej.: {' '.join(next(iter(hits)))}"
            )
    add("no_verbatim_reviews", problems)

    # 4. Idioma
    problems = []
    want = (report.language or "es").split("-")[0]
    sample = "\n".join(
        [report.summary] + [r.rationale for r in report.ranking] + [v.summary for v in report.verdicts]
    )
    got = _detect_language(sample)
    if got and got != want:
        problems.append(f"idioma detectado '{got}', esperado '{want}'")
    add("language", problems)

    # 5. Caps
    problems = []
    if len(report.verdicts) > config.MAX_ANALYSTS:
        problems.append(f"{len(report.verdicts)} analistas > MAX_ANALYSTS={config.MAX_ANALYSTS}")
    if report.stats.candidates_found > config.MAX_CANDIDATES:
        problems.append(
            f"{report.stats.candidates_found} candidatos > MAX_CANDIDATES={config.MAX_CANDIDATES}"
        )
    if len(report.ranking) > config.RANKING_CAP:
        problems.append(f"ranking de {len(report.ranking)} > RANKING_CAP={config.RANKING_CAP}")
    for row in tool_outputs:
        out = row.get("output") or {}
        if (
            row.get("tool") == "get_property_reviews"
            and len(out.get("reviews", [])) > config.MAX_REVIEWS_PER_CALL
        ):
            problems.append("una llamada de reviews superó MAX_REVIEWS_PER_CALL")
    add("caps_respected", problems)

    # 6. Propiedades trazables (nunca inventadas)
    problems = []
    for v in report.verdicts:
        if v.property_token not in tokens_seen:
            problems.append(f"{v.name}: token {v.property_token} no aparece en ningún tool output")
    for d in report.discarded:
        if d.property_token and d.property_token not in tokens_seen:
            problems.append(f"descartado {d.name}: token no trazable")
    add("properties_traceable", problems)

    # 7. Unknowns nunca como pro/con; evidencia en confirmed/violated
    problems = []
    for v in report.verdicts:
        for c in v.hard_constraints + v.soft_preferences:
            if c.status != CheckStatus.UNKNOWN and not (c.evidence or "").strip():
                problems.append(f"{v.name}: '{c.constraint}' {c.status} sin evidencia")
        low_pros = {p.lower() for p in v.pros} | {p.lower() for p in v.cons}
        for u in v.unknowns:
            if u.lower() in low_pros:
                problems.append(f"{v.name}: '{u}' está en unknowns y también en pros/cons")
    add("unknowns_classified", problems)

    # 8. fit_score coherente con la rúbrica
    problems = []
    for v in report.verdicts:
        score, rec = PropertyVerdict.rubric_inputs(
            AnalystOutput.model_validate(v.model_dump(exclude={"fit_score", "recommend", "facts"}))
        )
        if score != v.fit_score or rec != v.recommend:
            problems.append(f"{v.name}: fit {v.fit_score}/{v.recommend} ≠ rúbrica {score}/{rec}")
    add("fit_score_rubric", problems)

    # 9. Ranking ordenado, completo (toda recomendada está) y sin no-recomendadas
    problems = []
    ranked_all = {r.verdict.property_token for r in report.ranking}
    for v in report.verdicts:
        if v.recommend and v.property_token not in ranked_all and len(report.ranking) < config.RANKING_CAP:
            problems.append(f"{v.name} es recomendada pero no está en el ranking")
    last = None
    last_fit = None
    for r in report.ranking:
        if last is not None and r.rank <= last:
            problems.append("ranks no crecientes")
        if last_fit is not None and r.verdict.fit_score > last_fit:
            problems.append(
                f"{r.verdict.name} (fit {r.verdict.fit_score}) rankeada debajo de una con fit {last_fit}"
            )
        last, last_fit = r.rank, r.verdict.fit_score
    add("ranking_well_formed", problems)

    # 10. Zona dibujada: ninguna rankeada con coordenadas fuera de la zona
    problems = []
    if report.brief.zone is not None:
        for r in report.ranking:
            f = r.verdict.facts
            if f is not None and f.zone_inside is False:
                problems.append(f"{r.verdict.name} está fuera de la zona dibujada ({f.zone_distance_m} m)")
    add("zone_respected", problems)

    return {"ok": all(c["ok"] for c in checks.values()), "checks": checks}
