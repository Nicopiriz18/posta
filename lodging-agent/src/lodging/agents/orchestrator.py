"""Construcción del deep agent (agente principal conversacional + subagentes).

Regla 11: el agente principal no tiene tools de datos. Solo `save_brief`, `confirm_brief`, `publish_report`,
`write_todos`, `task` y filesystem. El subagente `general-purpose` está deshabilitado a propósito.
"""

from __future__ import annotations

from typing import Any

from deepagents import create_deep_agent
from deepagents.profiles.harness.harness_profiles import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    register_harness_profile,
)
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver

from lodging import config
from lodging.agents.control_tools import CONTROL_TOOLS
from lodging.agents.prompts import AGENT_PROMPT
from lodging.agents.subagents import analyst_subagent, discovery_subagent

_PROFILES_REGISTERED = False


def _disable_general_purpose() -> None:
    """Registra un perfil que apaga el subagente general-purpose para modelos anthropic."""
    global _PROFILES_REGISTERED
    if _PROFILES_REGISTERED:
        return
    profile = HarnessProfile(general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False))
    for key in ("anthropic", "fake", "test"):
        register_harness_profile(key, profile)
    _PROFILES_REGISTERED = True


def resolve_model(model: str | BaseChatModel) -> BaseChatModel:
    """Instancia el modelo con timeout y reintentos de `config` (visibles en los eventos vía callbacks)."""
    if isinstance(model, BaseChatModel):
        return model
    return init_chat_model(model, max_retries=config.MODEL_MAX_RETRIES, timeout=config.MODEL_TIMEOUT_SECONDS)


def build_agent(
    *,
    orchestrator_model: str | BaseChatModel | None = None,
    discovery_model: str | BaseChatModel | None = None,
    analyst_model: str | BaseChatModel | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
) -> Any:
    """Arma el grafo. Acepta instancias de modelo (tests) o specs `provider:model` (config).

    El checkpointer hace que la conversación sea multi-turno (`thread_id` en config). Por defecto en memoria.
    """
    _disable_general_purpose()
    s = config.get_settings()
    return create_deep_agent(
        model=resolve_model(orchestrator_model or s.orchestrator_model),
        tools=CONTROL_TOOLS,  # regla 11: sin tools de datos
        system_prompt=AGENT_PROMPT,
        subagents=[
            discovery_subagent(resolve_model(discovery_model or s.discovery_model)),
            analyst_subagent(resolve_model(analyst_model or s.analyst_model)),
        ],
        checkpointer=checkpointer or MemorySaver(),
        name="lodging-agent",
    )
