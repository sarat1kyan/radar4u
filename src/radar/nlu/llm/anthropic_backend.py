from __future__ import annotations

from datetime import date
from typing import Any

from radar.domain.intents import SearchIntent
from radar.nlu.llm.base import LLMBackend, LLMIntent, LLMUnavailable, system_prompt, user_payload


class AnthropicBackend(LLMBackend):
    """Structured extraction via the official Anthropic SDK (messages.parse)."""

    name = "anthropic"

    def __init__(self, api_key: str, model: str, effort: str = "low") -> None:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - depends on install extras
            raise LLMUnavailable("install the 'anthropic' extra to use this backend") from exc
        self._anthropic = anthropic
        self.client: Any = anthropic.AsyncAnthropic(api_key=api_key, max_retries=2, timeout=30.0)
        self.model = model
        self.effort = effort

    async def extract(self, text: str, *, today: date, zone: str, previous: SearchIntent | None) -> LLMIntent:
        a = self._anthropic
        try:
            resp = await self.client.messages.parse(
                model=self.model,
                max_tokens=2048,
                system=system_prompt(today, zone),
                messages=[{"role": "user", "content": user_payload(text, previous)}],
                output_format=LLMIntent,
                output_config={"effort": self.effort},
            )
        except a.RateLimitError as exc:
            raise LLMUnavailable("rate limited") from exc
        except a.APIStatusError as exc:
            raise LLMUnavailable(f"api status {exc.status_code}") from exc
        except a.APIConnectionError as exc:
            raise LLMUnavailable("connection error") from exc
        if resp.stop_reason == "refusal":
            raise LLMUnavailable("request declined by model")
        parsed = resp.parsed_output
        if not isinstance(parsed, LLMIntent):
            raise LLMUnavailable("no structured output")
        return parsed

    async def aclose(self) -> None:
        await self.client.close()
