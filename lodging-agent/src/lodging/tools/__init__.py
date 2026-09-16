"""Capa de datos: cliente SerpAPI + las 3 tools que ven los subagentes.

Reglas que viven acá: normalizar y recortar (1), errores estructurados (7),
semáforo global (8), caps (9).
"""

from lodging.tools.hotels import get_property_details, get_property_reviews, search_hotels
from lodging.tools.serpapi_client import SerpApiClient, SerpApiError, get_client, set_client

__all__ = [
    "SerpApiClient",
    "SerpApiError",
    "get_client",
    "set_client",
    "search_hotels",
    "get_property_details",
    "get_property_reviews",
]
