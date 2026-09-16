"""Tools de control del agente principal: `save_brief` y `publish_report`.

No son tools de datos (regla 11): no tocan SerpAPI. Solo fijan el brief en el contexto/filesystem
virtual y ensamblan el reporte final en código a partir de outputs trazables.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool
from pydantic import ValidationError

from lodging import config
from lodging.runtime import get_context
from lodging.schemas import (
    BriefDraft,
    DiscardedCandidate,
    OrchestratorOutput,
    RankEntry,
    brief_date_problems,
)

log = logging.getLogger(__name__)


def _write_vfs(path: str, content: str) -> None:
    try:
        from deepagents.backends import StateBackend
        from deepagents.backends.utils import create_file_data

        backend = StateBackend()
        backend._send_files_update({path: backend._prepare_for_storage(create_file_data(content))})  # noqa: SLF001
    except Exception as e:  # noqa: BLE001 — fuera del grafo no hay config
        log.debug("VFS write skipped: %s", e)


@tool(args_schema=BriefDraft)
async def save_brief(**fields: Any) -> dict[str, Any]:
    """Save or update the trip brief with everything the traveler has said so far.

    Call it every time you learn something new (fields you omit keep their previous value). Required to start
    the research: destination, check_in, check_out (YYYY-MM-DD). Also useful: adults/children, budget
    (budget_total_max or budget_per_night_max + currency), area_preferences, property_types, dealbreakers
    (rule a place OUT with positive evidence, e.g. "hostel"), hard_constraints (must-haves to verify, e.g.
    "wifi"), soft_preferences, travel_purpose, language ("es"/"en"/"pt"), gl (destination country code).

    Returns {status: "ready" | "incomplete", missing: [...], brief: {...}, nights}. "ready" means the brief is
    valid, NOT that the traveler confirmed. Research tools only work after the brief is saved and confirmed.
    """
    ctx = get_context()
    if ctx is None:
        return {
            "error": {
                "type": "invalid_params",
                "message": "No active conversation context",
                "tool": "save_brief",
            }
        }
    incoming = BriefDraft(**{k: v for k, v in fields.items() if k in BriefDraft.model_fields and k != "zone"})
    draft = ctx.draft.merged(incoming)
    ctx.draft = draft
    missing = draft.missing_required()
    if missing:
        ctx.emit(
            "brief_saved",
            "Brief incompleto: faltan " + ", ".join(missing),
            data={"draft": draft.model_dump(mode="json"), "status": "incomplete", "missing": missing},
        )
        return {
            "status": "incomplete",
            "missing": missing,
            "brief": draft.model_dump(mode="json", exclude_none=True),
        }
    try:
        brief = draft.to_brief()
    except ValidationError as e:
        problems = [f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()]
        return {
            "status": "incomplete",
            "missing": problems,
            "brief": draft.model_dump(mode="json", exclude_none=True),
        }
    date_problems = brief_date_problems(brief)
    if date_problems:
        ctx.emit(
            "brief_saved",
            "Brief con fechas inválidas: " + "; ".join(date_problems),
            data={"draft": draft.model_dump(mode="json"), "status": "incomplete", "missing": date_problems},
        )
        return {
            "status": "incomplete",
            "missing": date_problems,
            "hint": "Ask the traveler for the correct dates. Relative dates ('enero') mean the NEXT occurrence.",
            "brief": draft.model_dump(mode="json", exclude_none=True),
        }
    ctx.set_brief(brief, confirmed=False)
    confirmed = ctx.confirmed
    _write_vfs(config.VFS_BRIEF_PATH, brief.to_prompt_json())
    ctx.emit(
        "brief_saved",
        f"Brief guardado: {brief.destination}, {brief.nights} noches",
        data={
            "brief": brief.model_dump(mode="json"),
            "status": "ready",
            "confirmed": confirmed,
            "missing": [],
        },
    )
    return {
        "status": "ready",
        "confirmed": confirmed,
        "missing": [],
        "nights": brief.nights,
        "brief": brief.model_dump(mode="json"),
    }


@tool
async def suggest_replies(options: list[str]) -> dict[str, Any]:
    """Offer the traveler 2-4 quick replies (short, in their language) for the question you are about to ask.

    Call it right before asking a question with a small set of likely answers ("4 adultos" / "2 adultos + 2 chicos",
    budget ranges, "sí, buscá" / "cambiar algo"). The traveler can always type freely instead. Do not call it when
    the answer is open-ended (a destination, a date).
    """
    ctx = get_context()
    opts = [str(o).strip() for o in options if str(o).strip()][:4]
    if ctx is not None and opts:
        ctx.emit("suggestions", " · ".join(opts), data={"options": opts})
    return {"ok": True, "options": opts}


@tool
async def confirm_brief() -> dict[str, Any]:
    """Mark the saved brief as confirmed by the traveler. Call it ONLY after the traveler explicitly agreed to
    start the research (e.g. "dale", "buscá", "go", "sí, arrancá") or when the incoming message already states
    the brief is confirmed. After this, delegate discovery with the `task` tool.
    """
    ctx = get_context()
    if ctx is None or ctx.brief is None:
        return {
            "error": {
                "type": "invalid_params",
                "message": "Save a complete brief first with save_brief",
                "tool": "confirm_brief",
            }
        }
    ctx.confirmed = True
    ctx.emit(
        "run_started",
        f"Investigando alojamiento en {ctx.brief.destination}",
        data={"brief": ctx.brief.model_dump(mode="json")},
    )
    return {"confirmed": True, "run_id": ctx.run_id, "brief": ctx.brief.model_dump(mode="json")}


@tool
async def publish_report(
    ranking: list[RankEntry],
    summary: str,
    not_recommended: list[DiscardedCandidate] | None = None,
    caveats: list[str] | None = None,
) -> dict[str, Any]:
    """Publish the final report to the traveler once every analyst has returned.

    `ranking`: EVERY verdict with recommend=true (property_token copied exactly, rank starting at 1, best first,
    rationale in the brief language citing only facts from the verdicts). There is no fixed top N: if 6 fit, list 6;
    if 1 fits, list 1. Never include a verdict with recommend=false: list it in `not_recommended` with the violated
    constraint instead. `summary`: 3-5 sentences for the traveler.
    `caveats`: data gaps, tool errors, things to double check.

    The report itself (prices, pros/cons, review summaries) is assembled by code from the analysts' verdicts, so
    do not repeat them. Returns {ok, run_id, ranked, assertions_ok}. After it, send the traveler a short
    message: the report is already displayed, just point out the top pick and any caveat.
    """
    from lodging.runner import finalize_report

    ctx = get_context()
    if ctx is None or ctx.brief is None:
        return {
            "error": {
                "type": "invalid_params",
                "message": "No brief / no research to report",
                "tool": "publish_report",
            }
        }
    orch = OrchestratorOutput(
        ranking=ranking, summary=summary, not_recommended=not_recommended or [], caveats=caveats or []
    )
    report, assertions = finalize_report(ctx, orch, timed_out=False)
    _write_vfs(config.VFS_REPORT_PATH, report.model_dump_json(indent=1))
    return {
        "ok": True,
        "run_id": ctx.run_id,
        "ranked": len(report.ranking),
        "analyzed": len(report.verdicts),
        "assertions_ok": bool(assertions and assertions.get("ok")),
        "failed_checks": [k for k, v in (assertions or {}).get("checks", {}).items() if not v["ok"]],
    }


CONTROL_TOOLS = [save_brief, suggest_replies, confirm_brief, publish_report]
