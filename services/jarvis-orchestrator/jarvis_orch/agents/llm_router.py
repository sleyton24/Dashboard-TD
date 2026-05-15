"""Router LLM. Único proveedor permitido: Ollama (Ley 21.719).

La firma incluye `task_kind` y `agent_default` para uso futuro (eventual
escalamiento a un modelo más grande on-prem cuando llegue la GPU). Por
ahora ambos parámetros se ignoran — todo va al modelo local configurado.

NUNCA importar `anthropic`, `openai`, `google-generativeai` u otros SDKs
de LLM externo desde este módulo.
"""
from __future__ import annotations

import time
from typing import Any, Literal

import httpx
import structlog

from jarvis_orch.settings import get_settings

logger = structlog.get_logger(__name__)

ModelTier = Literal["local"]  # extensible cuando entre GPU; por ahora solo local


class LLMRouter:
    """Cliente para Ollama. Una instancia por proceso (worker)."""

    def __init__(
        self,
        ollama_url: str | None = None,
        model: str | None = None,
        timeout: int | None = None,
    ) -> None:
        s = get_settings()
        self.ollama_url = ollama_url or s.OLLAMA_URL
        self.model = model or s.OLLAMA_MODEL
        self.timeout = timeout or s.OLLAMA_TIMEOUT_S
        self._client = httpx.AsyncClient(base_url=self.ollama_url, timeout=self.timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def chat(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        task_kind: str | None = None,        # noqa: ARG002 — uso futuro
        agent_default: ModelTier = "local",  # noqa: ARG002 — uso futuro
        temperature: float = 0.2,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Envía una conversación al modelo local y devuelve la respuesta normalizada.

        Returns:
            dict con keys:
                - role: 'assistant'
                - content: str
                - tool_calls: list[{function: {name, arguments}}] o []
                - model: nombre del modelo
                - usage: {prompt_tokens, completion_tokens, total_tokens, ...}
        """
        active_model = model or self.model
        body: dict[str, Any] = {
            "model": active_model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if tools:
            body["tools"] = tools

        started = time.monotonic()
        r = await self._client.post("/api/chat", json=body)
        duration_ms = int((time.monotonic() - started) * 1000)

        if r.status_code != 200:
            logger.error(
                "llm.local.error",
                model=active_model,
                status=r.status_code,
                duration_ms=duration_ms,
                body=r.text[:500],
            )
            r.raise_for_status()

        data = r.json()
        message = data.get("message", {})
        content = message.get("content", "") or ""
        tool_calls = message.get("tool_calls") or []

        # Ollama devuelve métricas en ns; convertimos a tokens si están.
        prompt_tokens = data.get("prompt_eval_count", 0)
        completion_tokens = data.get("eval_count", 0)

        logger.info(
            "llm.local.call",
            model=active_model,
            msg_count=len(messages),
            tools=len(tools or []),
            content_len=len(content),
            tool_calls=len(tool_calls),
            duration_ms=duration_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

        return {
            "role": "assistant",
            "content": content,
            "tool_calls": tool_calls,
            "model": f"local:{active_model}",
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
                "duration_ms": duration_ms,
            },
        }
