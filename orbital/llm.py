"""Talk to Meta's models through OpenAI-compatible endpoints.

Muse Spark Open (open weights) runs on Baseten. When MODEL_API_KEY is set, roles can use
Muse Spark 1.3 / Muse Wonder on the Meta Model API instead.
"""
from __future__ import annotations

import base64
import io
import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from PIL import Image

from orbital import config

PROVIDERS = {
    "spark-open": (config.BASETEN_BASE_URL, config.baseten_key, config.SPARK_OPEN_MODEL),
    "spark": (config.META_BASE_URL, config.meta_key, "muse-spark-1.3"),
    "wonder": (config.META_BASE_URL, config.meta_key, "muse-wonder-1.0"),
}
_clients: dict[str, OpenAI] = {}


def available(provider: str) -> bool:
    return bool(PROVIDERS[provider][1]())


def pick(*preferred: str) -> str:
    """First provider in preference order whose key is configured."""
    for p in preferred:
        if available(p):
            return p
    raise RuntimeError("No model key configured: set BASETEN_API_KEY and/or MODEL_API_KEY")


def model_name(provider: str) -> str:
    return PROVIDERS[provider][2]


def _client(provider: str) -> OpenAI:
    if provider not in _clients:
        base, key, _ = PROVIDERS[provider]
        _clients[provider] = OpenAI(base_url=base, api_key=key(), timeout=240, max_retries=0)
    return _clients[provider]


@dataclass
class Reply:
    content: str
    reasoning: str
    tool_calls: list = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    seconds: float = 0.0

    def as_message(self) -> dict:
        msg = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            msg["tool_calls"] = [
                {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in self.tool_calls
            ]
        return msg


def chat(messages: list[dict], tools: list[dict] | None = None, provider: str = "spark-open",
         max_tokens: int = 8000, temperature: float = 0.3, retries: int = 4) -> Reply:
    """One completion. Structured results go through tool calls: strict json_schema output
    degenerates into whitespace on the Baseten deployment."""
    kwargs = {"model": model_name(provider), "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    for attempt in range(retries + 1):
        t0 = time.time()
        try:
            r = _client(provider).chat.completions.create(**kwargs)
            m = r.choices[0].message
            extra = m.model_dump()
            return Reply(
                content=m.content or "",
                reasoning=extra.get("reasoning_content") or extra.get("reasoning") or "",
                tool_calls=list(m.tool_calls or []),
                usage=r.usage.model_dump() if r.usage else {},
                seconds=time.time() - t0,
            )
        except (APIConnectionError, APITimeoutError) as e:
            err = e
        except APIStatusError as e:
            if e.status_code not in (408, 409, 429) and e.status_code < 500:
                raise
            err = e
        if attempt == retries:
            raise err
        time.sleep(min(30, 2 ** attempt) + random.random())
    raise RuntimeError("unreachable")


def image_part(path: Path | str, max_side: int = 1024, quality: int = 85) -> dict:
    """PNG evidence → compact JPEG data URL for vision input."""
    im = Image.open(path).convert("RGB")
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()}}


def parse_args(raw: str | None) -> dict:
    """Tool arguments are JSON; tolerate code fences or trailing text from the model."""
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            return json.loads(raw[start:end + 1])
        raise
