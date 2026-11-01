"""OpenAI 兼容客户端：base_url / api_key / model 全部来自配置，不绑定任何厂商。"""
from __future__ import annotations

import json
from dataclasses import dataclass

from openai import OpenAI

from .config import Config

TASK_SCHEMA = {
    "type": "object",
    "properties": {
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "owner": {"type": "string"},
                    "due": {"type": "string"},
                    "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                    "quote": {"type": "string"},
                    "msg_ids": {"type": "array", "items": {"type": "integer"}},
                    "confidence": {"type": "number"},
                },
                "required": ["title", "priority", "quote", "msg_ids", "confidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["tasks"],
    "additionalProperties": False,
}


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def add(self, other: "Usage") -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens


class LLM:
    def __init__(self, cfg: Config):
        cfg.require_llm()
        self.cfg = cfg
        self.client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key, timeout=cfg.timeout)

    def _response_format(self) -> dict:
        if self.cfg.response_format == "json_schema":
            return {"type": "json_schema",
                    "json_schema": {"name": "wechat_todo", "schema": TASK_SCHEMA,
                                    "strict": True}}
        return {"type": "json_object"}

    def complete_json(self, system: str, user: str | list[dict]) -> tuple[dict, Usage]:
        """user 可以是纯文本，也可以是 OpenAI 多模态 content parts（含 image_url）。"""
        resp = self.client.chat.completions.create(
            model=self.cfg.model,
            temperature=self.cfg.temperature,
            response_format=self._response_format(),
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
        )
        content = resp.choices[0].message.content or ""
        usage = Usage(
            prompt_tokens=getattr(resp.usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(resp.usage, "completion_tokens", 0) or 0,
        )
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"模型返回的不是合法 JSON（response_format={self.cfg.response_format}）。"
                f"前 300 字：{content[:300]}") from exc
        return data, usage
