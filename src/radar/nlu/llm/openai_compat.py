"""Backend for any server speaking the OpenAI chat-completions protocol
(hosted APIs, vLLM, Ollama, llama.cpp server). Uses JSON mode plus our own
pydantic validation, which is the lowest common denominator these servers share.
"""

from __future__ import annotations

import json
from datetime import date

import httpx
from pydantic import ValidationError

from radar.domain.intents import SearchIntent
from radar.nlu.llm.base import LLMBackend, LLMIntent, LLMUnavailable, system_prompt, user_payload


class OpenAICompatBackend(LLMBackend):
    name = "openai"

    def __init__(self, base_url: str, model: str, api_key: str | None = None, timeout_s: float = 30.0) -> None:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.client = httpx.AsyncClient(base_url=base_url.rstrip("/"), headers=headers, timeout=timeout_s)
        self.model = model

    async def extract(self, text: str, *, today: date, zone: str, previous: SearchIntent | None) -> LLMIntent:
        schema = json.dumps(LLMIntent.model_json_schema(), ensure_ascii=False)
        body = {
            "model": self.model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt(today, zone) + "\nJSON schema:\n" + schema},
                {"role": "user", "content": user_payload(text, previous)},
            ],
        }
        try:
            resp = await self.client.post("/chat/completions", json=body)
        except httpx.HTTPError as exc:
            raise LLMUnavailable("connection error") from exc
        if resp.status_code != 200:
            raise LLMUnavailable(f"api status {resp.status_code}")
        try:
            content = resp.json()["choices"][0]["message"]["content"]
            return LLMIntent.model_validate_json(content)
        except (KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
            raise LLMUnavailable("invalid structured output") from exc

    async def aclose(self) -> None:
        await self.client.aclose()
