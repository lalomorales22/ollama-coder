"""Ollama backend.

Two things here matter more than they look:

* **num_ctx**. Ollama defaults every model to a small context window unless the
  request asks for more. The previous version advertised a 131k window and then
  silently sent 4k, so long sessions quietly lost their history. We read the
  real context length out of the model metadata and request it.
* **capabilities**. `show` reports whether a model supports tools, vision and
  thinking. Knowing that lets us fail loudly ("this model can't call tools")
  instead of watching the agent loop spin.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import ollama

DEFAULT_NUM_CTX = 16384
# Leave room for the reply inside the advertised window.
RESPONSE_HEADROOM = 2048


@dataclass
class ModelInfo:
    name: str
    context_length: int = DEFAULT_NUM_CTX
    capabilities: list[str] = field(default_factory=list)
    family: str = ""
    parameter_size: str = ""
    size_bytes: int = 0

    @property
    def supports_tools(self) -> bool:
        return "tools" in self.capabilities

    @property
    def supports_vision(self) -> bool:
        return "vision" in self.capabilities

    @property
    def supports_thinking(self) -> bool:
        return "thinking" in self.capabilities


class OllamaBackend:
    def __init__(self, config: Any):
        self.config = config
        self._client = self._build_client()
        self._info_cache: dict[str, ModelInfo] = {}
        self._models_cache: list[dict[str, Any]] | None = None

    # -- connection ------------------------------------------------------

    def _build_client(self) -> ollama.AsyncClient:
        cfg = self.config.get("ollama", {}) or {}
        kwargs: dict[str, Any] = {}

        host = (cfg.get("host") or "").strip()
        if host:
            kwargs["host"] = host

        try:
            import httpx

            kwargs["timeout"] = httpx.Timeout(
                float(cfg.get("timeout_sec", 900)),
                connect=float(cfg.get("connect_timeout_sec", 15)),
            )
        except ImportError:  # pragma: no cover
            kwargs["timeout"] = float(cfg.get("timeout_sec", 900))

        headers = {k: v for k, v in (cfg.get("headers") or {}).items() if v}
        api_key = (cfg.get("api_key") or "").strip()
        if api_key:
            headers.setdefault("Authorization", f"Bearer {api_key}")
        if headers:
            kwargs["headers"] = headers

        return ollama.AsyncClient(**kwargs)

    def reconnect(self) -> None:
        self._client = self._build_client()
        self._info_cache.clear()
        self._models_cache = None

    @property
    def client(self) -> ollama.AsyncClient:
        return self._client

    async def ping(self) -> tuple[bool, str]:
        try:
            await self._client.list()
            return True, ""
        except Exception as exc:
            host = (self.config.get("ollama.host") or "").strip() or "http://localhost:11434"
            return False, (
                f"cannot reach Ollama at {host}: {exc}\n"
                "Start it with `ollama serve`, or set ollama.host in settings.json."
            )

    # -- model discovery -------------------------------------------------

    async def list_models(self, refresh: bool = False) -> list[dict[str, Any]]:
        if self._models_cache is not None and not refresh:
            return self._models_cache

        models: list[dict[str, Any]] = []
        try:
            response = await self._client.list()
            for model in response.models or []:
                name = getattr(model, "model", None) or getattr(model, "name", None)
                if not name:
                    continue
                details = getattr(model, "details", None)
                models.append({
                    "name": name,
                    "size": getattr(model, "size", 0) or 0,
                    "family": getattr(details, "family", "") if details else "",
                    "parameter_size": getattr(details, "parameter_size", "") if details else "",
                    "modified": str(getattr(model, "modified_at", "") or ""),
                })
        except Exception:
            return self._models_cache or []

        if not self.config.get("ollama.allow_cloud_models", False):
            models = [m for m in models if not m["name"].endswith(":cloud")]

        models.sort(key=lambda m: m["name"])
        self._models_cache = models
        return models

    async def model_names(self, refresh: bool = False) -> list[str]:
        return [m["name"] for m in await self.list_models(refresh=refresh)]

    async def info(self, model: str) -> ModelInfo:
        if model in self._info_cache:
            return self._info_cache[model]

        info = ModelInfo(name=model)
        try:
            response = await self._client.show(model)
            capabilities = list(getattr(response, "capabilities", None) or [])
            info.capabilities = [str(c) for c in capabilities]

            model_info = getattr(response, "modelinfo", None) or {}
            for key, value in model_info.items():
                if key.endswith(".context_length") and isinstance(value, int):
                    info.context_length = value
                    break

            details = getattr(response, "details", None)
            if details:
                info.family = getattr(details, "family", "") or ""
                info.parameter_size = getattr(details, "parameter_size", "") or ""
        except Exception:
            pass

        self._info_cache[model] = info
        return info

    async def effective_num_ctx(self, model: str) -> int:
        """How large a context to actually request.

        A model's advertised window is not the usable one: the KV cache grows
        linearly with it and shares memory with the weights. Measured on a 32GB
        M4, a 36B MoE at 64k sits at 23GB fully on the GPU with an 8-bit KV
        cache, and spills to the CPU without one.

        Whether the cache is quantised is the *daemon's* setting, not ours, and
        there is no API to ask it -- on macOS the daemon is launched by the GUI
        and does not share a terminal's environment. So `ollama.context_ceiling`
        is authoritative; the env var is only consulted as a hint for the case
        where client and server share an environment (Linux, manual `serve`).
        """
        configured = self.config.get("num_ctx")
        if configured:
            return int(configured)
        ceiling = int(self.config.get("ollama.context_ceiling", 0) or 0)
        if not ceiling:
            quantised = os.environ.get("OLLAMA_KV_CACHE_TYPE", "").lower() in ("q8_0", "q4_0")
            ceiling = 65536 if quantised else 32768
        info = await self.info(model)
        return max(4096, min(info.context_length, ceiling))

    # -- chat ------------------------------------------------------------

    def _options(self, num_ctx: int, max_tokens: int | None = None) -> dict[str, Any]:
        options: dict[str, Any] = {"num_ctx": num_ctx}
        temperature = self.config.get("temperature")
        if temperature is not None:
            options["temperature"] = float(temperature)
        top_p = self.config.get("top_p")
        if top_p is not None:
            options["top_p"] = float(top_p)
        predict = max_tokens if max_tokens is not None else self.config.get("max_tokens")
        if predict:
            options["num_predict"] = int(predict)
        return options

    async def _think_value(self, model: str) -> Any:
        mode = str(self.config.get("think", "auto")).lower()
        if mode == "never":
            return None
        info = await self.info(model)
        if not info.supports_thinking:
            return None
        if mode in ("low", "medium", "high"):
            return mode
        return True  # "auto" and "always" both just enable it

    async def chat_stream(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[Any]:
        num_ctx = await self.effective_num_ctx(model)
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": self._options(num_ctx, max_tokens),
        }
        if tools:
            payload["tools"] = tools
        think = await self._think_value(model)
        if think is not None:
            payload["think"] = think
        keep_alive = self.config.get("keep_alive")
        if keep_alive:
            payload["keep_alive"] = keep_alive

        stream = await self._client.chat(**payload)
        async for chunk in stream:  # type: ignore[union-attr]
            yield chunk

    async def chat_once(
        self,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int | None = None,
        think: bool = False,
    ) -> str:
        """Non-streaming, no tools -- used for summarisation and titles."""
        num_ctx = await self.effective_num_ctx(model)
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": self._options(num_ctx, max_tokens),
        }
        if not think:
            info = await self.info(model)
            if info.supports_thinking:
                payload["think"] = False
        response = await self._client.chat(**payload)
        return (response.message.content or "").strip()  # type: ignore[union-attr]

    async def pull(self, model: str) -> AsyncIterator[str]:
        """Stream progress lines while downloading a model."""
        try:
            stream = await self._client.pull(model, stream=True)
            async for chunk in stream:  # type: ignore[union-attr]
                status = getattr(chunk, "status", "") or ""
                completed = getattr(chunk, "completed", None)
                total = getattr(chunk, "total", None)
                if completed and total:
                    pct = 100 * completed / total
                    yield f"{status} {pct:.0f}%"
                elif status:
                    yield status
        except Exception as exc:
            yield f"pull failed: {exc}"

    async def aclose(self) -> None:
        client = getattr(self._client, "_client", None)
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                pass
