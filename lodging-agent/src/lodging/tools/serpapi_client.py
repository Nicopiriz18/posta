"""Cliente asíncrono de SerpAPI.

- Un único `httpx.AsyncClient` y un único semáforo global (`SERPAPI_CONCURRENCY`): todas las
  llamadas pasan por acá (regla 8). No crear otros clientes.
- Cache en memoria con TTL para búsquedas de discovery (regla del backend).
- Errores de red / HTTP / SerpAPI se levantan como `SerpApiError`; las tools los convierten
  en `ToolError` estructurado hacia el modelo (regla 7).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Any

import httpx

from lodging import config

log = logging.getLogger(__name__)

SERPAPI_URL = "https://serpapi.com/search.json"


class SerpApiError(Exception):
    def __init__(self, kind: str, message: str, *, retryable: bool = False, status: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.retryable = retryable
        self.status = status


class _TTLCache:
    def __init__(self, ttl: float):
        self.ttl = ttl
        self._data: dict[str, tuple[float, dict[str, Any]]] = {}

    @staticmethod
    def key(params: dict[str, Any]) -> str:
        raw = json.dumps(params, sort_keys=True, default=str)
        return hashlib.sha1(raw.encode()).hexdigest()

    def get(self, params: dict[str, Any]) -> dict[str, Any] | None:
        k = self.key(params)
        hit = self._data.get(k)
        if not hit:
            return None
        ts, value = hit
        if time.monotonic() - ts > self.ttl:
            self._data.pop(k, None)
            return None
        return value

    def put(self, params: dict[str, Any], value: dict[str, Any]) -> None:
        self._data[self.key(params)] = (time.monotonic(), value)

    def clear(self) -> None:
        self._data.clear()


class SerpApiClient:
    """Cliente único. `transport` permite inyectar un `httpx.MockTransport` en tests."""

    def __init__(
        self,
        api_key: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        concurrency: int = config.SERPAPI_CONCURRENCY,
        timeout: float = config.SERPAPI_TIMEOUT_SECONDS,
        retries: int = config.SERPAPI_RETRIES,
        cache_ttl: float = config.DISCOVERY_CACHE_TTL_SECONDS,
    ):
        self.api_key = api_key
        self._http = httpx.AsyncClient(timeout=timeout, transport=transport)
        self._sem = asyncio.Semaphore(concurrency)
        self._retries = retries
        self.cache = _TTLCache(cache_ttl)
        self.calls = 0  # llamadas reales (no cacheadas) — para stats y tests
        self.cache_hits = 0

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get(self, params: dict[str, Any], *, use_cache: bool = False) -> dict[str, Any]:
        """GET a SerpAPI. Levanta `SerpApiError` en cualquier falla."""
        if not self.api_key:
            raise SerpApiError("invalid_params", "SERPAPI_KEY no configurada", retryable=False)
        clean = {k: v for k, v in params.items() if v is not None and v != ""}
        if use_cache:
            cached = self.cache.get(clean)
            if cached is not None:
                self.cache_hits += 1
                return cached
        attempt = 0
        while True:
            attempt += 1
            try:
                async with self._sem:
                    self.calls += 1
                    resp = await self._http.get(SERPAPI_URL, params={**clean, "api_key": self.api_key})
            except httpx.TimeoutException as e:
                if attempt <= self._retries:
                    continue
                raise SerpApiError("timeout", f"SerpAPI timeout: {e}", retryable=True) from e
            except httpx.HTTPError as e:
                if attempt <= self._retries:
                    continue
                raise SerpApiError("http", f"SerpAPI network error: {e}", retryable=True) from e

            if resp.status_code == 429:
                raise SerpApiError(
                    "rate_limit", "SerpAPI rate limit / cupo agotado", retryable=True, status=429
                )
            if resp.status_code >= 500:
                if attempt <= self._retries:
                    continue
                raise SerpApiError(
                    "http", f"SerpAPI HTTP {resp.status_code}", retryable=True, status=resp.status_code
                )
            try:
                data = resp.json()
            except ValueError as e:
                raise SerpApiError("http", "SerpAPI devolvió un cuerpo no-JSON", retryable=False) from e
            if resp.status_code >= 400 or "error" in data:
                msg = str(data.get("error") or f"HTTP {resp.status_code}")
                kind = "invalid_params" if resp.status_code == 400 else "serpapi"
                raise SerpApiError(kind, msg, retryable=False, status=resp.status_code)
            if use_cache:
                self.cache.put(clean, data)
            return data


_client: SerpApiClient | None = None


def get_client() -> SerpApiClient:
    """Cliente global (lazy). En tests usar `set_client(...)` con un MockTransport."""
    global _client
    if _client is None:
        _client = SerpApiClient(config.get_settings().serpapi_key)
    return _client


def set_client(client: SerpApiClient | None) -> None:
    global _client
    _client = client
