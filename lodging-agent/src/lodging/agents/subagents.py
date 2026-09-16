"""Definición de los subagentes: `hotel-discovery` y `property-analyst`.

Discovery combina Google Hotels (SerpAPI) con la web abierta: búsqueda server-side de Anthropic
(`WEB_SEARCH_TOOL`), lectura de páginas (`fetch_page`) y registro de candidatos web (`add_web_candidate`).
El analista puede leer la página de un candidato web para verificarlo.
"""

from __future__ import annotations

from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel

from lodging.agents.prompts import (
    ANALYST_DESCRIPTION,
    ANALYST_NAME,
    ANALYST_PROMPT,
    DISCOVERY_DESCRIPTION,
    DISCOVERY_NAME,
    DISCOVERY_PROMPT,
)
from lodging.schemas import AnalystOutput, DiscoveryOutput
from lodging.tools import get_property_details, get_property_reviews, search_hotels
from lodging.tools.web import WEB_SEARCH_TOOL, add_web_candidate, fetch_page


def discovery_subagent(model: str | BaseChatModel, *, web: bool = True) -> dict:
    tools: list = [search_hotels]
    if web:
        tools += [WEB_SEARCH_TOOL, fetch_page, add_web_candidate]
    return {
        "name": DISCOVERY_NAME,
        "description": DISCOVERY_DESCRIPTION,
        "system_prompt": DISCOVERY_PROMPT,
        "tools": tools,
        "model": model,
        "response_format": ToolStrategy(DiscoveryOutput),
    }


def analyst_subagent(model: str | BaseChatModel, *, web: bool = True) -> dict:
    tools: list = [get_property_details, get_property_reviews]
    if web:
        tools.append(fetch_page)
    return {
        "name": ANALYST_NAME,
        "description": ANALYST_DESCRIPTION,
        "system_prompt": ANALYST_PROMPT,
        "tools": tools,
        "model": model,
        "response_format": ToolStrategy(AnalystOutput),
    }
