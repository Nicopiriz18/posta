"""Runner: ejecuta un turno del deep agent sobre un hilo, emite eventos y ensambla el `FinalReport`.

- `chat_turn(ctx, agent, text)`: un turno de conversación (API). Los eventos van a `ctx`.
- `run_brief(brief)`: modo headless (CLI/evals): manda el brief ya confirmado y espera el reporte.

El reporte se ensambla EN CÓDIGO (`finalize_report`) a partir de outputs trazables: los verdicts vienen
verbatim de los analistas (structured output del `task`), el ranking/resumen del agente vía `publish_report`,
y el log de tools queda guardado en `runs/<id>/` para las assertions.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import date
from typing import Any
from urllib.parse import quote_plus, urlparse

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from lodging import config
from lodging.agents.prompts import ANALYST_NAME, DISCOVERY_NAME
from lodging.callbacks import ModelTimingHandler
from lodging.runtime import RunContext, use_context
from lodging.schemas import (
    AnalystOutput,
    Brief,
    Candidate,
    DiscardedCandidate,
    DiscoveryOutput,
    FinalReport,
    OrchestratorOutput,
    PropertyFacts,
    PropertyVerdict,
    RankedProperty,
    RankEntry,
    RunStats,
    now_iso,
)

log = logging.getLogger(__name__)

_ANALYZE_RE = re.compile(r"Analyze property:\s*(?P<name>.+?)\s*\(token\s+(?P<token>[^)\s]+)\)", re.IGNORECASE)


class RunResult:
    def __init__(self, report: FinalReport, ctx: RunContext, assertions: dict[str, Any] | None = None):
        self.report = report
        self.ctx = ctx
        self.assertions = assertions


# ---------------------------------------------------------------------------
# Parsing del stream
# ---------------------------------------------------------------------------


def _text_of(msg: Any) -> str:
    content = getattr(msg, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return str(content)


def _iter_messages(update: Any):
    if not isinstance(update, dict):
        return
    for _node, delta in update.items():
        if not isinstance(delta, dict):
            continue
        msgs = delta.get("messages")
        if msgs is None:
            continue
        if not isinstance(msgs, list):
            msgs = [msgs]
        yield from msgs


def repair_token(ctx: RunContext, out: AnalystOutput) -> AnalystOutput:
    """Si el analista alteró el property_token pero el nombre coincide con un candidato conocido, se corrige.

    El token es la clave de trazabilidad (regla 12): nunca se acepta uno que no venga de un tool output.
    """
    if out.property_token in ctx.candidates:
        return out
    if out.property_token in ctx.merged_duplicates:
        return out.model_copy(update={"property_token": ctx.merged_duplicates[out.property_token]})
    wanted = out.name.strip().lower()
    for tok, cand in ctx.candidates.items():
        if cand.name.strip().lower() == wanted:
            ctx.warnings.append(
                f"El analista devolvió un token alterado para {cand.name}; se corrigió por nombre."
            )
            return out.model_copy(update={"property_token": tok})
    return out


def _is_aggregator(url: str | None) -> bool:
    if not url:
        return True
    host = urlparse(url).netloc.lower()
    return any(host == d or host.endswith("." + d) for d in config.AGGREGATOR_DOMAINS)


def pick_booking_link(cand: Candidate, facts: PropertyFacts) -> tuple[str | None, str | None]:
    """El enlace que abre ESTA propiedad: página propia (web), oferta de un portal real, o el link de Google.

    Los comparadores (vio, freecancellations, bluepillow...) van último: son redirecciones, no la publicación.
    """
    if cand.source == "web" and cand.url:
        return cand.url, urlparse(cand.url).netloc.lower().removeprefix("www.")
    ranked: list[tuple[int, str, str]] = []
    for o in facts.offers:
        if not o.link:
            continue
        ranked.append((1 if _is_aggregator_source(o.source) else 0, o.link, o.source))
    ranked.sort(key=lambda t: t[0])
    if ranked and ranked[0][0] == 0:
        return ranked[0][1], ranked[0][2]
    if cand.link and not _is_aggregator(cand.link):
        return cand.link, urlparse(cand.link).netloc.lower().removeprefix("www.")
    if ranked:
        return ranked[0][1], ranked[0][2]
    if cand.link:
        return cand.link, urlparse(cand.link).netloc.lower().removeprefix("www.")
    return None, None


def _is_aggregator_source(source: str) -> bool:
    s = (source or "").lower().replace(" ", "")
    return any(d.split(".")[0] in s for d in config.AGGREGATOR_DOMAINS)


def facts_from_tools(ctx: RunContext, token: str) -> PropertyFacts | None:
    """Arma la ficha desde el ÚLTIMO output exitoso de get_property_details para ese token (nunca desde el LLM)."""
    details = None
    for row in reversed(ctx.tool_outputs):
        out = row.get("output") or {}
        if (
            row.get("tool") == "get_property_details"
            and "error" not in out
            and out.get("property_token") == token
        ):
            details = out
            break
    if details is None:
        cand = ctx.candidates.get(token)
        if cand is None:
            return None
        details = cand.model_dump()
    keys = set(PropertyFacts.model_fields)
    try:
        facts = PropertyFacts.model_validate({k: v for k, v in details.items() if k in keys})
    except Exception:  # noqa: BLE001
        return None
    cand = ctx.candidates.get(token)
    if cand is not None:
        facts.source = cand.source
        facts.zone_inside = cand.zone_inside
        facts.zone_distance_m = cand.zone_distance_m
        facts.booking_url, facts.booking_source = pick_booking_link(cand, facts)
        facts.google_hotels_url = "https://www.google.com/travel/search?q=" + quote_plus(
            f"{cand.name} {ctx.brief.destination if ctx.brief else ''}".strip()
        )
        facts.url = cand.url
        have = {o.source for o in facts.offers}
        for o in cand.offers:  # precios hallados en la web para el mismo alojamiento
            if o.source not in have:
                facts.offers.append(o)
                have.add(o.source)
    return facts


class _StreamState:
    def __init__(self, ctx: RunContext):
        self.ctx = ctx
        self.task_types: dict[str, str] = {}  # tool_call_id -> subagent_type
        self.task_names: dict[str, str] = {}  # tool_call_id -> property name
        self.phase: str | None = None
        self.analysts_started = 0
        self.analysts_finished = 0
        self.assistant_texts: list[str] = []
        self.last_web_query = ""

    def set_phase(self, phase: str, message: str) -> None:
        if self.phase != phase:
            self.phase = phase
            self.ctx.emit("phase", message, data={"phase": phase})

    def handle_main_ai(self, msg: AIMessage) -> None:
        text = _text_of(msg).strip()
        if text:
            self.assistant_texts.append(text)
            self.ctx.emit("assistant", text, agent="orchestrator", data={"role": "assistant"})
        for tc in msg.tool_calls or []:
            name, args, tc_id = tc.get("name"), tc.get("args") or {}, tc.get("id") or ""
            if name == "task":
                st = str(args.get("subagent_type") or "")
                desc = str(args.get("description") or "")
                self.task_types[tc_id] = st
                if st == DISCOVERY_NAME:
                    self.ctx.research_started_at = self.ctx.research_started_at or time.monotonic()
                    self.set_phase("discovery", "Buscando candidatos")
                    self.ctx.emit(
                        "subagent_started",
                        "Discovery en marcha",
                        agent=DISCOVERY_NAME,
                        data={"subagent": st, "agent_id": tc_id},
                    )
                elif st == ANALYST_NAME:
                    self.analysts_started += 1
                    m = _ANALYZE_RE.search(desc)
                    pname = (
                        m.group("name").strip()
                        if m
                        else (
                            desc.splitlines()[0][:80]
                            if desc.strip()
                            else f"propiedad #{self.analysts_started}"
                        )
                    )
                    self.task_names[tc_id] = pname
                    self.set_phase("analysis", "Analizando propiedades en paralelo")
                    self.ctx.emit(
                        "subagent_started",
                        f"Analizando {pname}",
                        agent=ANALYST_NAME,
                        data={"subagent": st, "agent_id": tc_id, "property_name": pname},
                    )
                else:
                    self.ctx.emit("warning", f"Subagente inesperado: {st}", data={"subagent": st})
            elif name == "write_todos":
                todos = args.get("todos") or []
                self.ctx.emit(
                    "message", f"Plan: {len(todos)} pasos", agent="orchestrator", data={"todos": todos}
                )
            elif name == "read_file":
                self.ctx.emit("message", f"Leyendo {args.get('file_path', '')}", agent="orchestrator")
            elif name == "publish_report":
                self.set_phase("consolidation", "Consolidando el reporte")

    def handle_sub_ai(self, msg: AIMessage) -> None:
        """Mensajes de subagentes: acá aparecen los bloques de la búsqueda web server-side de Anthropic."""
        content = msg.content if isinstance(msg.content, list) else []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "server_tool_use" and block.get("name") == "web_search":
                last_query = str((block.get("input") or {}).get("query") or "")
                self.last_web_query = last_query
                self.ctx.web_searches += 1
                self.ctx.emit(
                    "tool_call",
                    f"Buscando en la web: {last_query}",
                    agent=DISCOVERY_NAME,
                    data={"tool": "web_search", "args": {"query": last_query}},
                )
            elif btype == "web_search_tool_result":
                last_query = self.last_web_query
                raw = block.get("content")
                if isinstance(raw, list):
                    results = [
                        {"title": r.get("title"), "url": r.get("url"), "page_age": r.get("page_age")}
                        for r in raw
                        if isinstance(r, dict) and r.get("type") == "web_search_result"
                    ]
                    self.ctx.log_tool("web_search", {"query": last_query}, {"results": results})
                    self.ctx.emit(
                        "tool_result",
                        f"{len(results)} resultados web para «{last_query}»",
                        agent=DISCOVERY_NAME,
                        data={
                            "tool": "web_search",
                            "ok": True,
                            "summary": f"{len(results)} resultados",
                            "results": results[:8],
                        },
                    )
                else:
                    err = (raw or {}).get("error_code") if isinstance(raw, dict) else str(raw)
                    self.ctx.tool_errors += 1
                    self.ctx.log_tool(
                        "web_search",
                        {"query": last_query},
                        {"error": {"type": "serpapi", "message": str(err), "tool": "web_search"}},
                    )
                    self.ctx.emit(
                        "tool_result",
                        f"Búsqueda web falló: {err}",
                        agent=DISCOVERY_NAME,
                        data={"tool": "web_search", "ok": False, "error": str(err), "summary": str(err)},
                    )

    def handle_main_tool(self, msg: ToolMessage) -> None:
        if msg.name != "task":
            return
        tc_id = msg.tool_call_id or ""
        st = self.task_types.get(tc_id, "")
        content = _text_of(msg).strip()
        if st == DISCOVERY_NAME:
            try:
                self.ctx.discovery = DiscoveryOutput.model_validate(json.loads(content))
                n = len(self.ctx.discovery.shortlist)
                self.ctx.emit(
                    "subagent_finished",
                    f"Discovery terminó: {n} candidatos plausibles, {len(self.ctx.candidates)} encontrados",
                    agent=DISCOVERY_NAME,
                    data={"subagent": st, "agent_id": tc_id, "shortlist": n},
                )
            except Exception as e:  # noqa: BLE001
                self.ctx.warnings.append(f"Discovery devolvió una salida no estructurada: {e}")
                self.ctx.emit(
                    "subagent_finished",
                    "Discovery terminó (salida no estructurada)",
                    agent=DISCOVERY_NAME,
                    data={"subagent": st, "agent_id": tc_id},
                )
            self.set_phase("selection", "Seleccionando candidatos a analizar")
        elif st == ANALYST_NAME:
            self.analysts_finished += 1
            pname = self.task_names.get(tc_id, "")
            try:
                out = AnalystOutput.model_validate(json.loads(content))
                out = repair_token(self.ctx, out)
                verdict = PropertyVerdict.from_analyst(out)
                verdict.facts = facts_from_tools(self.ctx, verdict.property_token)
                self.ctx.verdicts[verdict.property_token] = verdict
                self.ctx.emit(
                    "verdict",
                    f"{verdict.name}: fit {verdict.fit_score}",
                    agent=ANALYST_NAME,
                    data={"verdict": verdict.model_dump()},
                )
                self.ctx.emit(
                    "subagent_finished",
                    f"Análisis de {verdict.name} listo",
                    agent=ANALYST_NAME,
                    data={"subagent": st, "agent_id": tc_id, "property_name": verdict.name},
                )
            except Exception as e:  # noqa: BLE001
                self.ctx.warnings.append(
                    f"El analista de {pname or 'una propiedad'} no devolvió un verdict válido: {e}"
                )
                self.ctx.emit(
                    "warning",
                    f"Analista de {pname} sin verdict válido",
                    agent=ANALYST_NAME,
                    data={"agent_id": tc_id, "error": str(e)[:300]},
                )
                self.ctx.emit(
                    "subagent_finished",
                    f"Análisis de {pname} terminó sin verdict",
                    agent=ANALYST_NAME,
                    data={"subagent": st, "agent_id": tc_id, "property_name": pname},
                )
            if self.analysts_finished >= self.analysts_started:
                self.set_phase("consolidation", "Consolidando el reporte")


def _with_today(text: str) -> str:
    """El modelo no sabe qué día es: sin esto resuelve 'enero' al enero pasado y SerpAPI rechaza la fecha."""
    return f"(Today is {date.today().isoformat()}.)\n\n{text}"


async def _consume_stream(agent: Any, ctx: RunContext, state: _StreamState, text: str) -> None:
    inputs = {"messages": [HumanMessage(content=_with_today(text))]}
    cfg = {
        "configurable": {"thread_id": ctx.thread_id},
        "recursion_limit": config.RECURSION_LIMIT,
        # OJO: no usar las claves "run_id"/"thread_id" en metadata: langgraph las trata como identidad de la
        # corrida y deduplica turnos posteriores del mismo hilo (el segundo turno no corre).
        "metadata": {"lodging_run_id": ctx.run_id, "lodging_thread_id": ctx.thread_id},
        "run_name": f"lodging-{ctx.thread_id}",
        "callbacks": [ModelTimingHandler(ctx)],
    }
    async for chunk in agent.astream(inputs, stream_mode="updates", subgraphs=True, config=cfg):
        if isinstance(chunk, tuple) and len(chunk) == 2:
            ns, update = chunk
        else:
            ns, update = (), chunk
        is_main = not ns
        for msg in _iter_messages(update):
            if is_main and isinstance(msg, AIMessage):
                state.handle_main_ai(msg)
            elif is_main and isinstance(msg, ToolMessage):
                state.handle_main_tool(msg)
            elif isinstance(msg, AIMessage):
                state.handle_sub_ai(msg)


# ---------------------------------------------------------------------------
# Ensamblado del reporte
# ---------------------------------------------------------------------------


def _fallback_summary(language: str, n: int, timed_out: bool) -> str:
    if language.startswith("en"):
        base = f"{n} properties were analyzed." if n else "No properties could be analyzed."
        return base + (" The run hit the time limit, so this report is partial." if timed_out else "")
    base = f"Se analizaron {n} propiedades." if n else "No se pudo analizar ninguna propiedad."
    return base + (
        " La corrida llegó al límite de tiempo, así que este reporte es parcial." if timed_out else ""
    )


def assemble_report(
    ctx: RunContext,
    orch: OrchestratorOutput | None,
    *,
    duration: float,
    timed_out: bool,
    error: str | None = None,
) -> FinalReport:
    assert ctx.brief is not None
    verdicts = list(ctx.verdicts.values())
    lang = ctx.brief.language or "es"
    caveats: list[str] = list(ctx.warnings)
    ranking: list[RankedProperty] = []
    used: set[str] = set()

    if orch is not None:
        chosen: list[tuple[int, RankEntry, PropertyVerdict]] = []
        for entry in sorted(orch.ranking, key=lambda e: e.rank):
            v = ctx.verdicts.get(entry.property_token)
            if v is None:
                caveats.append(f"El agente rankeó un token sin verdict ({entry.property_token}); se omitió.")
                continue
            if not v.recommend or entry.property_token in used:
                continue
            used.add(entry.property_token)
            chosen.append((len(chosen), entry, v))
        # Regla 4: el orden final lo decide el fit_score de la rúbrica; el agente elige y justifica, no reordena.
        # Sin top fijo: toda recomendada que el agente haya omitido entra igual, con su propio resumen.
        for v in verdicts:
            if v.recommend and v.property_token not in used:
                used.add(v.property_token)
                chosen.append(
                    (
                        len(chosen),
                        RankEntry(
                            property_token=v.property_token, rank=len(chosen) + 1, rationale=v.summary or ""
                        ),
                        v,
                    )
                )
        chosen.sort(key=lambda t: (-t[2].fit_score, t[0]))
        for _, entry, v in chosen[: config.RANKING_CAP]:
            ranking.append(RankedProperty(rank=len(ranking) + 1, rationale=entry.rationale, verdict=v))
    if not ranking and verdicts:
        # Fallback determinista (timeout o salida inválida): por rúbrica.
        for v in sorted((x for x in verdicts if x.recommend), key=lambda x: (-x.fit_score, x.name)):
            ranking.append(RankedProperty(rank=len(ranking) + 1, rationale=v.summary or "", verdict=v))
            if len(ranking) >= config.RANKING_CAP:
                break
        if orch is None:
            caveats.append("Ranking generado por rúbrica sin consolidación del agente.")

    discarded: list[DiscardedCandidate] = list(ctx.zone_discards)
    if ctx.discovery:
        discarded.extend(d.model_copy(update={"stage": "discovery"}) for d in ctx.discovery.discarded)
    if orch is not None:
        discarded.extend(d.model_copy(update={"stage": "orchestrator"}) for d in orch.not_recommended)
    ranked_tokens = {r.verdict.property_token for r in ranking}
    listed = {d.property_token for d in discarded if d.property_token}
    for v in verdicts:
        if not v.recommend and v.property_token not in ranked_tokens and v.property_token not in listed:
            violated = [c.constraint for c in v.hard_constraints if c.status == "violated"]
            reason = ("Incumple: " + ", ".join(violated)) if violated else "No recomendado por el analista"
            discarded.append(
                DiscardedCandidate(
                    property_token=v.property_token, name=v.name, reason=reason, stage="analyst"
                )
            )

    if orch is not None:
        caveats.extend(orch.caveats)
        summary = orch.summary
    else:
        summary = _fallback_summary(lang, len(verdicts), timed_out)
    if timed_out:
        caveats.append(f"La corrida superó el límite de {config.RUN_TIMEOUT_SECONDS} s: reporte parcial.")
    if error:
        caveats.append(f"Error durante la corrida: {error}")
    if ctx.tool_errors:
        caveats.append(
            f"{ctx.tool_errors} llamada(s) a SerpAPI fallaron; los campos afectados quedaron como desconocidos."
        )

    s = config.get_settings()
    stats = RunStats(
        candidates_found=len(ctx.candidates),
        shortlisted=len(ctx.discovery.shortlist) if ctx.discovery else 0,
        analyzed=len(verdicts),
        serpapi_calls=len(ctx.tool_outputs),
        tool_errors=ctx.tool_errors,
        duration_seconds=round(duration, 1),
        timed_out=timed_out,
        orchestrator_model=s.orchestrator_model,
        analyst_model=s.analyst_model,
    )
    seen: set[str] = set()
    caveats = [c for c in caveats if not (c in seen or seen.add(c))]
    return FinalReport(
        run_id=ctx.run_id,
        generated_at=now_iso(),
        language=lang,
        brief=ctx.brief,
        summary=summary,
        ranking=ranking,
        verdicts=verdicts,
        discarded=discarded,
        caveats=caveats,
        stats=stats,
    )


def finalize_report(
    ctx: RunContext,
    orch: OrchestratorOutput | None,
    *,
    timed_out: bool,
    error: str | None = None,
    save: bool = True,
) -> tuple[FinalReport, dict[str, Any] | None]:
    """Ensambla, persiste (runs/<id>/), corre las assertions y emite `run_finished`/`run_failed`."""
    from lodging.evals.assertions import check_run

    duration = (time.monotonic() - ctx.research_started_at) if ctx.research_started_at else 0.0
    report = assemble_report(ctx, orch, duration=duration, timed_out=timed_out, error=error)
    ctx.report = report
    ctx.reports.append(report)
    assertions = None
    if save:
        ctx.save_artifacts()
        d = ctx.run_dir()
        (d / "final.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
        assertions = check_run(report, ctx.tool_outputs)
        (d / "assertions.json").write_text(
            json.dumps(assertions, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if error and not report.verdicts:
        ctx.emit("run_failed", error, data={"error": error, "partial_report": report.model_dump(mode="json")})
    else:
        ctx.emit(
            "run_finished",
            f"Reporte listo: {len(report.ranking)} recomendaciones",
            data={"report": report.model_dump(mode="json"), "assertions": assertions},
        )
    return report, assertions


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


async def chat_turn(
    ctx: RunContext,
    agent: Any,
    text: str,
    *,
    timeout: float = config.RUN_TIMEOUT_SECONDS,
    save: bool = True,
    display_text: str | None = None,
) -> str:
    """Un turno de conversación. Nunca levanta por errores del agente: emite warning y, si había una
    investigación en curso, publica un reporte parcial. Devuelve el texto de respuesta del agente.

    `display_text`: cómo se muestra el mensaje del usuario en el historial (modo headless manda un prompt
    con JSON que no tiene sentido mostrar tal cual)."""
    state = _StreamState(ctx)
    timed_out = False
    error: str | None = None
    reports_before = len(ctx.reports)
    ctx.add_message("user", display_text or text)
    ctx.running = True
    with use_context(ctx):
        try:
            await asyncio.wait_for(_consume_stream(agent, ctx, state, text), timeout=timeout)
        except TimeoutError:
            timed_out = True
            ctx.emit("warning", "Límite de tiempo alcanzado; se consolida lo que hay")
        except Exception as e:  # noqa: BLE001
            error = f"{type(e).__name__}: {e}"
            log.exception("turn on %s failed", ctx.thread_id)
            ctx.emit("warning", f"Error del agente: {error[:300]}")
        research_ran = state.phase is not None and ctx.brief is not None
        if research_ran and len(ctx.reports) == reports_before:
            # La investigación arrancó pero el agente no llegó a publicar: reporte parcial en código.
            finalize_report(ctx, None, timed_out=timed_out, error=error, save=save)
        reply = "\n\n".join(state.assistant_texts).strip()
        if not reply and ctx.report is not None and len(ctx.reports) > reports_before:
            reply = ctx.report.summary
        if not reply and (timed_out or error):
            reply = (
                "La investigación se cortó por tiempo; el reporte es parcial."
                if timed_out
                else f"Hubo un error: {error}"
            )
        if reply:
            ctx.add_message("assistant", reply)
        ctx.running = False
        ctx.emit("turn_done", "", data={"status": "failed" if error else ("timeout" if timed_out else "ok")})
        if save:
            try:
                ctx.save_events()
            except OSError:
                pass
        if ctx.queue is not None:
            ctx.queue.put_nowait(None)
    return reply


def headless_message(brief: Brief) -> str:
    return (
        "Trip brief (JSON), already confirmed by the traveler:\n"
        f"{brief.to_prompt_json()}\n\n"
        "Save it with save_brief, call confirm_brief, and start the research immediately. Do not ask questions."
    )


async def run_brief(
    brief: Brief,
    *,
    ctx: RunContext | None = None,
    agent: Any | None = None,
    timeout: float = config.RUN_TIMEOUT_SECONDS,
    save: bool = True,
) -> RunResult:
    """Modo headless (CLI/evals): corre la investigación completa sobre un brief ya confirmado."""
    from lodging.agents import build_agent

    ctx = ctx or RunContext()
    agent = agent or build_agent()
    shown = (
        f"Brief confirmado: {brief.destination}, {brief.check_in:%d/%m} al {brief.check_out:%d/%m} "
        f"({brief.nights} noches, {brief.adults} adultos). Buscar alojamiento."
    )
    await chat_turn(ctx, agent, headless_message(brief), timeout=timeout, save=save, display_text=shown)
    if ctx.report is None:
        # El agente no llegó ni a confirmar el brief: reporte vacío pero honesto.
        if ctx.brief is None:
            ctx.set_brief(brief, confirmed=True)
        report, assertions = finalize_report(
            ctx, None, timed_out=False, error="El agente no publicó un reporte", save=save
        )
        if ctx.queue is not None:
            ctx.queue.put_nowait(None)
        return RunResult(report, ctx, assertions)
    assertions = None
    if save:
        p = ctx.run_dir() / "assertions.json"
        if p.exists():
            assertions = json.loads(p.read_text(encoding="utf-8"))
    return RunResult(ctx.report, ctx, assertions)
