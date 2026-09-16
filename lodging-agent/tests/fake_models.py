"""Modelos falsos, deterministas y sin red, que "actúan" como agente principal / discovery / analista.

Deciden qué hacer mirando los mensajes que reciben (stateless), así soportan analistas en paralelo y
conversaciones multi-turno. Sirven para probar el cableado completo (deepagents + tools + runner) sin
ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Sequence
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from lodging.agents.prompts import ANALYST_NAME, DISCOVERY_NAME
from lodging.schemas import (
    AnalystOutput,
    CheckStatus,
    ConstraintCheck,
    DiscoveryOutput,
    PriceEvidence,
    ShortlistEntry,
)

_TOKEN_RE = re.compile(r"\(token\s+(?P<token>[^)\s]+)\)")


def _tc(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "args": args, "id": f"call_{uuid.uuid4().hex[:12]}", "type": "tool_call"}


def _content(m: BaseMessage) -> str:
    c = m.content
    if isinstance(c, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in c)
    return str(c)


def _json(m: BaseMessage) -> Any:
    try:
        return json.loads(_content(m))
    except Exception:  # noqa: BLE001
        return None


def _after_last_human(messages: Sequence[BaseMessage]) -> tuple[HumanMessage | None, list[BaseMessage]]:
    idx = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1)
    human = messages[idx] if idx >= 0 else None
    return human, list(messages[idx + 1 :]) if idx >= 0 else list(messages)


def _tool_msgs(messages: Sequence[BaseMessage], name: str) -> list[ToolMessage]:
    return [m for m in messages if isinstance(m, ToolMessage) and m.name == name]


class ScriptedModel(BaseChatModel):
    """Base: registra las tools bindeadas y delega en `decide()`."""

    role: str = "orchestrator"
    bound_tools: list[str] = Field(default_factory=list)
    calls: int = 0
    seen_tools: list[list[str]] = Field(default_factory=list)  # por invocación

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _get_ls_params(self, stop: list[str] | None = None, **kwargs: Any) -> dict:  # type: ignore[override]
        return {"ls_provider": "fake", "ls_model_name": f"fake-{self.role}", "ls_model_type": "chat"}

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> Any:  # type: ignore[override]
        names = []
        for t in tools:
            if isinstance(t, dict):
                names.append(t.get("name") or (t.get("function") or {}).get("name") or str(t))
            else:
                names.append(getattr(t, "name", getattr(t, "__name__", str(t))))
        return self.model_copy(update={"bound_tools": names})

    def structured_tool_name(self, schema_name: str) -> str:
        for n in self.bound_tools:
            if n == schema_name:
                return n
        raise AssertionError(f"tool estructurada {schema_name} no está bindeada: {self.bound_tools}")

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.calls += 1
        self.seen_tools.append(list(self.bound_tools))
        msg = self.decide(messages)
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def decide(self, messages: list[BaseMessage]) -> AIMessage:  # pragma: no cover - abstract
        raise NotImplementedError


class FakeDiscovery(ScriptedModel):
    role: str = "discovery"

    def decide(self, messages: list[BaseMessage]) -> AIMessage:
        searches = _tool_msgs(messages, "search_hotels")
        if not searches:
            return AIMessage(
                content="", tool_calls=[_tc("search_hotels", {"query": "Hotels in Palermo, Buenos Aires"})]
            )
        res = _json(searches[-1]) or {}
        shortlist, discarded = [], []
        for c in res.get("candidates", []):
            if c.get("type") == "vacation rental":
                discarded.append(
                    {
                        "property_token": c["property_token"],
                        "name": c["name"],
                        "reason": "type=vacation rental, brief prefers hotels",
                        "stage": "discovery",
                    }
                )
            else:
                shortlist.append(
                    ShortlistEntry(
                        property_token=c["property_token"], name=c["name"], why="hotel with rating"
                    ).model_dump()
                )
        out = DiscoveryOutput(
            shortlist=shortlist[:10], discarded=discarded, searches_run=len(searches), notes="fake"
        )
        return AIMessage(
            content="",
            tool_calls=[_tc(self.structured_tool_name("DiscoveryOutput"), out.model_dump(mode="json"))],
        )


class FakeAnalyst(ScriptedModel):
    role: str = "analyst"
    verbatim_review: bool = False  # para probar la assertion de reviews verbatim

    def decide(self, messages: list[BaseMessage]) -> AIMessage:
        human = next(m for m in messages if isinstance(m, HumanMessage))
        m = _TOKEN_RE.search(_content(human))
        token = m.group("token") if m else "unknown-token"
        details = _tool_msgs(messages, "get_property_details")
        reviews = _tool_msgs(messages, "get_property_reviews")
        if not details:
            return AIMessage(content="", tool_calls=[_tc("get_property_details", {"property_token": token})])
        if not reviews:
            return AIMessage(
                content="",
                tool_calls=[_tc("get_property_reviews", {"property_token": token, "max_reviews": 10})],
            )
        d = _json(details[-1]) or {}
        r = _json(reviews[-1]) or {}
        price = None
        if d.get("total_rate") is not None and d.get("price_source"):
            price = PriceEvidence(
                total=d["total_rate"],
                currency=d.get("currency", "USD"),
                per_night=d.get("rate_per_night"),
                source=d["price_source"],
                verified_at=d["price_verified_at"],
            )
        amen = " ".join(d.get("amenities", [])).lower()
        wifi = CheckStatus.CONFIRMED if "wi" in amen else CheckStatus.UNKNOWN
        pool = CheckStatus.CONFIRMED if "pool" in amen or "piscina" in amen else CheckStatus.UNKNOWN
        n_rev = len(r.get("reviews", []))
        summary_reviews = f"{n_rev} reviews recientes leídas; la mayoría destaca la ubicación."
        if self.verbatim_review and r.get("reviews"):
            summary_reviews = r["reviews"][0]["text"]
        out = AnalystOutput(
            property_token=d.get("property_token", token),
            name=d.get("name", "?"),
            hard_constraints=[
                ConstraintCheck(
                    constraint="wifi",
                    status=wifi,
                    evidence="amenities" if wifi == CheckStatus.CONFIRMED else None,
                )
            ],
            soft_preferences=[
                ConstraintCheck(
                    constraint="pileta",
                    status=pool,
                    evidence="amenities" if pool == CheckStatus.CONFIRMED else None,
                ),
                ConstraintCheck(constraint="desayuno incluido", status=CheckStatus.UNKNOWN),
            ],
            pros=["Buena ubicación según la ficha"],
            cons=[],
            unknowns=["desayuno incluido"],
            price=price,
            over_budget=(price.total > 400) if price else None,
            reviews_summary=summary_reviews,
            reviews_analyzed=n_rev,
            location_summary="Cerca del Jardín Botánico.",
            summary="Hotel correcto para la zona de Palermo.",
            link=d.get("link"),
        )
        return AIMessage(
            content="",
            tool_calls=[_tc(self.structured_tool_name("AnalystOutput"), out.model_dump(mode="json"))],
        )


class FakeOrchestrator(ScriptedModel):
    """Agente principal scripted.

    - Mensaje headless ("already confirmed" + brief JSON): save_brief → confirm_brief → discovery → read_file →
      analistas en paralelo → publish_report → texto final.
    - Conversación: turno 1 guarda destino y pregunta fechas; turno 2 guarda fechas y pide confirmación;
      turno 3 ("dale") confirma y corre la investigación.
    """

    role: str = "orchestrator"
    n_analysts: int = 3
    read_candidates: bool = True

    def decide(self, messages: list[BaseMessage]) -> AIMessage:
        human, after = _after_last_human(messages)
        text = _content(human) if human else ""
        n_turn = sum(1 for m in messages if isinstance(m, HumanMessage))
        saved = _tool_msgs(after, "save_brief")
        confirmed = _tool_msgs(after, "confirm_brief")
        headless = "already confirmed" in text

        if headless:
            if not saved:
                brief = json.loads(text.split("traveler:\n", 1)[1].split("\n\nSave it", 1)[0])
                brief.pop("nights", None)
                return AIMessage(content="", tool_calls=[_tc("save_brief", brief)])
            if not confirmed:
                return AIMessage(content="", tool_calls=[_tc("confirm_brief", {})])
            return self._research(after, saved)

        # --- conversación scripted
        if n_turn == 1:
            if not saved:
                return AIMessage(
                    content="",
                    tool_calls=[
                        _tc("save_brief", {"destination": "Buenos Aires, Argentina", "language": "es"})
                    ],
                )
            return AIMessage(content="¿Qué fechas viajás y cuántas personas son?")
        if n_turn == 2:
            if not saved:
                return AIMessage(
                    content="",
                    tool_calls=[
                        _tc(
                            "save_brief",
                            {
                                "check_in": "2026-10-10",
                                "check_out": "2026-10-13",
                                "adults": 2,
                                "dealbreakers": ["hostel"],
                                "hard_constraints": ["wifi"],
                                "soft_preferences": ["pileta"],
                                "gl": "ar",
                            },
                        )
                    ],
                )
            return AIMessage(
                content="Resumen: Buenos Aires, 10 al 13 de octubre, 2 adultos, con wifi. ¿Arranco?"
            )
        if not confirmed:
            return AIMessage(content="", tool_calls=[_tc("confirm_brief", {})])
        return self._research(after, saved)

    def _research(self, after: list[BaseMessage], saved: list[ToolMessage]) -> AIMessage:
        brief: dict = {}
        confirmed = _tool_msgs(after, "confirm_brief")
        if confirmed:
            brief = (_json(confirmed[-1]) or {}).get("brief") or {}
        if not brief and saved:
            brief = (_json(saved[-1]) or {}).get("brief") or {}
        brief_json = json.dumps(brief)
        tasks = _tool_msgs(after, "task")
        reads = _tool_msgs(after, "read_file")
        published = _tool_msgs(after, "publish_report")
        if not tasks:
            return AIMessage(
                content="Arranco la búsqueda.",
                tool_calls=[
                    _tc(
                        "task",
                        {
                            "description": "Find candidates for this brief:\n" + brief_json,
                            "subagent_type": DISCOVERY_NAME,
                        },
                    )
                ],
            )
        discovery = _json(tasks[0]) or {}
        if self.read_candidates and not reads:
            return AIMessage(
                content="", tool_calls=[_tc("read_file", {"file_path": "/candidates/hotels.json"})]
            )
        analyst_results = tasks[1:]
        if not analyst_results:
            calls = []
            for e in discovery.get("shortlist", [])[: self.n_analysts]:
                desc = (
                    f"Analyze property: {e['name']} (token {e['property_token']})\n\nBrief:\n{brief_json}\n\n"
                    f"Candidate:\n{json.dumps(e)}"
                )
                calls.append(_tc("task", {"description": desc, "subagent_type": ANALYST_NAME}))
            return AIMessage(content="", tool_calls=calls)
        if not published:
            verdicts = [
                v
                for v in (_json(t) for t in analyst_results)
                if isinstance(v, dict) and v.get("property_token")
            ]
            ranking = [
                {
                    "property_token": v["property_token"],
                    "rank": i + 1,
                    "rationale": f"Buena relación precio/ubicación para {v['name']}.",
                }
                for i, v in enumerate(verdicts)
            ]
            return AIMessage(
                content="",
                tool_calls=[
                    _tc(
                        "publish_report",
                        {
                            "ranking": ranking,
                            "not_recommended": [],
                            "summary": "Se analizaron varias opciones en Palermo y estas son las mejores para el brief.",
                            "caveats": ["Desayuno sin verificar en todas."],
                        },
                    )
                ],
            )
        res = _json(published[-1]) or {}
        return AIMessage(content=f"Listo: publiqué el reporte con {res.get('ranked', 0)} recomendaciones.")
