"""Chat-completion client for OpenAI-compatible APIs.

Kept small and vendor-neutral on purpose: callers get text plus token usage and
a `ChatError` that says whether retrying makes sense. Any OpenAI-compatible
endpoint (OpenAI, Azure OpenAI proxies, local servers like vLLM or Ollama's
OpenAI mode) works through NEXUS_OPENAI_BASE_URL.
"""

from dataclasses import dataclass

import httpx


class ChatError(Exception):
    def __init__(self, message: str, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class ChatResult:
    text: str
    model: str
    input_tokens: int | None
    output_tokens: int | None


class OpenAIChat:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        timeout: float,
        max_output_tokens: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        self.max_output_tokens = max_output_tokens
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            transport=transport,
        )

    async def complete(self, messages: list[dict[str, str]], json_mode: bool = False) -> ChatResult:
        payload: dict[str, object] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,  # factual answers: as repeatable as the provider allows
            "max_tokens": self.max_output_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            response = await self._client.post("/chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            raise ChatError("The AI provider timed out", retryable=True) from exc
        except httpx.TransportError as exc:
            raise ChatError("Could not reach the AI provider", retryable=True) from exc

        if response.status_code == 429 or response.status_code >= 500:
            raise ChatError(f"AI provider returned {response.status_code}", retryable=True)
        if response.status_code >= 400:
            raise ChatError(
                f"AI provider rejected the request ({response.status_code})", retryable=False
            )
        try:
            body = response.json()
            text = body["choices"][0]["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ChatError("AI provider returned an unexpected response", retryable=False) from exc
        usage = body.get("usage") or {}
        return ChatResult(
            text=text.strip(),
            model=body.get("model") or self.model,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
        )

    async def aclose(self) -> None:
        await self._client.aclose()
