"""Callback de LangChain que hace visibles las llamadas al modelo (duración, tokens, errores, reintentos).

Sin esto, un rate limit de Anthropic se ve como "el analista no hizo nada durante 2 minutos".
"""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler

from lodging.runtime import RunContext


class ModelTimingHandler(AsyncCallbackHandler):
    def __init__(self, ctx: RunContext):
        self.ctx = ctx
        self._started: dict[UUID, tuple[float, str]] = {}

    @staticmethod
    def _model_name(
        serialized: dict[str, Any] | None, metadata: dict[str, Any] | None, kwargs: dict[str, Any]
    ) -> str:
        for src in (metadata or {}), kwargs.get("invocation_params") or {}:
            for k in ("ls_model_name", "model", "model_name"):
                if src.get(k):
                    return str(src[k])
        if serialized and serialized.get("kwargs", {}).get("model"):
            return str(serialized["kwargs"]["model"])
        return "model"

    async def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: Any,
        *,
        run_id: UUID,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self._started[run_id] = (time.monotonic(), self._model_name(serialized, metadata, kwargs))

    async def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:
        t0, model = self._started.pop(run_id, (None, "model"))
        seconds = round(time.monotonic() - t0, 1) if t0 else None
        usage: dict[str, Any] = {}
        try:
            gen = response.generations[0][0]
            msg = getattr(gen, "message", None)
            usage = dict(getattr(msg, "usage_metadata", None) or {})
        except (IndexError, AttributeError, TypeError):
            pass
        inp, out = usage.get("input_tokens"), usage.get("output_tokens")
        cached = (usage.get("input_token_details") or {}).get("cache_read")
        detail = f"{seconds}s" if seconds is not None else ""
        if inp is not None:
            detail += f", {inp} in / {out} out tokens"
            if cached:
                detail += f" ({cached} cacheados)"
        self.ctx.emit(
            "model",
            f"{model} respondió ({detail})",
            agent="system",
            data={
                "model": model,
                "seconds": seconds,
                "input_tokens": inp,
                "output_tokens": out,
                "cache_read": cached,
            },
        )

    async def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        t0, model = self._started.pop(run_id, (None, "model"))
        seconds = round(time.monotonic() - t0, 1) if t0 else None
        self.ctx.emit(
            "warning",
            f"Error del modelo {model}: {type(error).__name__}: {str(error)[:200]}",
            agent="system",
            data={"model": model, "seconds": seconds, "error": str(error)[:500]},
        )

    async def on_retry(self, retry_state: Any, *, run_id: UUID, **kwargs: Any) -> None:
        self.ctx.emit("warning", "Reintentando llamada al modelo", agent="system", data={"retry": True})
