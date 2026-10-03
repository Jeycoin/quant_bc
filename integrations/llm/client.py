"""Multi-provider LLM adapter.

Neutral message format (used by agent.py, provider-independent):
  content = str
  content = [ {"type": "text", "text": ...},
              {"type": "tool_use", "id": ..., "name": ..., "input": {...}},
              {"type": "tool_result", "tool_use_id": ..., "content": str} ]

Providers:
  - anthropic       (Claude, native tool use)
  - openai_compat   (Alibaba Bailian/DashScope, DeepSeek, Kimi, Zhipu, ...
                     any OpenAI-compatible chat-completions endpoint)

Select via env: LLM_PROVIDER=anthropic|openai_compat
  anthropic:      ANTHROPIC_API_KEY, ANTHROPIC_MODEL
  openai_compat:  LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "8192"))


@dataclass
class LLMResponse:
    blocks: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = "end_turn"  # "tool_use" | "end_turn" | other

    @property
    def text(self) -> str:
        return "".join(b["text"] for b in self.blocks if b.get("type") == "text")

    @property
    def tool_uses(self) -> list[dict[str, Any]]:
        return [b for b in self.blocks if b.get("type") == "tool_use"]


class LLMClient(Protocol):
    model: str

    async def create(
        self, system: str, tools: list[dict[str, Any]], messages: list[dict[str, Any]]
    ) -> LLMResponse: ...


# ---------------------------------------------------------------- anthropic

class AnthropicClient:
    def __init__(self) -> None:
        import anthropic

        self._client = anthropic.AsyncAnthropic()
        self.model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")

    async def create(self, system, tools, messages) -> LLMResponse:
        resp = await self._client.messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            system=system,
            tools=[
                {
                    "name": t["name"],
                    "description": t["description"],
                    "input_schema": t["input_schema"],
                }
                for t in tools
            ],
            messages=[self._to_anthropic(m) for m in messages],
        )
        blocks = []
        for b in resp.content:
            if b.type == "text":
                blocks.append({"type": "text", "text": b.text})
            elif b.type == "tool_use":
                blocks.append(
                    {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
                )
        return LLMResponse(blocks=blocks, stop_reason=resp.stop_reason or "end_turn")

    @staticmethod
    def _to_anthropic(msg: dict[str, Any]) -> dict[str, Any]:
        content = msg["content"]
        if isinstance(content, str):
            return {"role": msg["role"], "content": content}
        blocks = []
        for b in content:
            if b["type"] == "tool_result":
                blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": b["tool_use_id"],
                        "content": b["content"],
                    }
                )
            else:
                blocks.append(b)
        return {"role": msg["role"], "content": blocks}


# ------------------------------------------------------------ openai compat

class OpenAICompatClient:
    def __init__(self) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(
            api_key=os.environ["LLM_API_KEY"],
            base_url=os.environ["LLM_BASE_URL"],
            # thinking-style models (glm-5.x, qwen3) can take minutes on
            # large tool-loop contexts; 60s is not enough
            timeout=300,
        )
        self.model = os.environ["LLM_MODEL"]

    async def create(self, system, tools, messages) -> LLMResponse:
        oai_messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            oai_messages.extend(self._to_openai(m))

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "messages": oai_messages,
        }
        extra_body = os.getenv("LLM_EXTRA_BODY")
        if extra_body:
            kwargs["extra_body"] = json.loads(extra_body)
        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t["description"],
                        "parameters": t["input_schema"],
                    },
                }
                for t in tools
            ]
        resp = await self._client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        msg = choice.message

        blocks: list[dict[str, Any]] = []
        if msg.content:
            blocks.append({"type": "text", "text": msg.content})
        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            blocks.append(
                {
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.function.name,
                    "input": args,
                }
            )
        stop = "tool_use" if choice.finish_reason == "tool_calls" else "end_turn"
        return LLMResponse(blocks=blocks, stop_reason=stop)

    @staticmethod
    def _to_openai(msg: dict[str, Any]) -> list[dict[str, Any]]:
        content = msg["content"]
        if isinstance(content, str):
            return [{"role": msg["role"], "content": content}]

        if msg["role"] == "assistant":
            text = "".join(b["text"] for b in content if b["type"] == "text")
            tool_calls = [
                {
                    "id": b["id"],
                    "type": "function",
                    "function": {"name": b["name"], "arguments": json.dumps(b["input"])},
                }
                for b in content
                if b["type"] == "tool_use"
            ]
            out: dict[str, Any] = {"role": "assistant", "content": text or None}
            if tool_calls:
                out["tool_calls"] = tool_calls
            return [out]

        # user role: tool_result blocks become individual tool messages
        return [
            {"role": "tool", "tool_call_id": b["tool_use_id"], "content": b["content"]}
            for b in content
            if b["type"] == "tool_result"
        ]


def create_llm_client() -> LLMClient:
    provider = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()
    if provider == "anthropic":
        return AnthropicClient()
    if provider in {"openai_compat", "openai", "dashscope", "qwen"}:
        return OpenAICompatClient()
    raise ValueError(f"Unknown LLM_PROVIDER: {provider!r}")
