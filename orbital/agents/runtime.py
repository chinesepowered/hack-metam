"""Tool-calling agent loop shared by the director, the labs and the reviewer."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from orbital import llm
from orbital.agents.events import EventLog

MAX_TOOL_RESULT_CHARS = 14000
MAX_IMAGES = 8  # per request on the Baseten deployment; older images give way to newer ones


def _cap_images(messages: list[dict], limit: int = MAX_IMAGES) -> None:
    """Keep only the most recent `limit` images in the conversation, replacing older ones with a note."""
    seen = 0
    for msg in reversed(messages):
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for i in range(len(content) - 1, -1, -1):
            if content[i].get("type") == "image_url":
                seen += 1
                if seen > limit:
                    content[i] = {"type": "text", "text": "[older image removed to stay within the image limit]"}


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    fn: Callable[[dict], dict]
    terminal: bool = False  # calling it ends the agent's turn (e.g. submit_finding)

    def spec(self) -> dict:
        return {"type": "function", "function": {"name": self.name, "description": self.description, "parameters": self.parameters}}


def schema(required: list[str] | None = None, **props) -> dict:
    return {"type": "object", "properties": props, "required": required or list(props)}


class Agent:
    def __init__(self, name: str, system: str, tools: list[Tool], log: EventLog, provider: str,
                 max_steps: int = 20, tag: str | None = None):
        self.name, self.system, self.provider, self.max_steps = name, system, provider, max_steps
        self.log = _Tagged(log, tag) if tag else log  # tag = study id, so parallel agents stay readable
        self.tools = {t.name: t for t in tools}
        self.results: list[dict] = []  # every tool output, for auditing the numbers the agent later claims

    def run(self, task: str, images: list[Path] = ()) -> dict:
        user = [{"type": "text", "text": task}] + [llm.image_part(p) for p in images]
        messages = [{"role": "system", "content": self.system}, {"role": "user", "content": user}]
        specs = [t.spec() for t in self.tools.values()]
        terminal = " or ".join(t.name for t in self.tools.values() if t.terminal)
        seen: dict[str, dict] = {}  # identical repeat calls get the earlier result instead of re-running
        for step in range(self.max_steps):
            if step == self.max_steps - 3:
                messages.append({"role": "user", "content": f"You have 3 steps left. Stop gathering; finish now by "
                                                            f"calling {terminal} with what you have verified."})
            _cap_images(messages)
            reply = llm.chat(messages, tools=specs, provider=self.provider)
            if reply.reasoning:
                self.log.emit(self.name, "thought", reply.reasoning.strip()[:600], model=llm.model_name(self.provider),
                              seconds=round(reply.seconds, 1))
            if reply.content.strip():
                self.log.emit(self.name, "say", reply.content.strip()[:2000])
            messages.append(reply.as_message())

            if not reply.tool_calls:
                messages.append({"role": "user", "content": f"Continue with tools. Finish by calling {terminal}."})
                continue

            visuals, finished = [], None
            for tc in reply.tool_calls:
                tool = self.tools.get(tc.function.name)
                try:
                    args = llm.parse_args(tc.function.arguments)
                    if tool is None:
                        raise ValueError(f"unknown tool {tc.function.name}")
                    self.log.emit(self.name, "tool_call", tool.name, args=args)
                    key = tool.name + json.dumps(args, sort_keys=True)
                    if key in seen and not tool.terminal:
                        result = {"note": "identical to one of your earlier calls; same result"} | seen[key]
                    else:
                        result = tool.fn(args)
                        seen[key] = {k: v for k, v in result.items() if k != "_images"} | (
                            {"_images": result["_images"]} if "_images" in result else {})
                except Exception as e:  # report failures back to the model; it can retry differently
                    result = {"error": f"{type(e).__name__}: {e}"}
                images = result.pop("_images", [])
                self.results.append({"tool": tc.function.name, "result": result})
                self.log.emit(self.name, "tool_result", tc.function.name, result=_compact(result),
                              images=[str(p) for p in images])
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "content": json.dumps(result, default=str)[:MAX_TOOL_RESULT_CHARS]})
                if images:
                    visuals.append((tc.function.name, images))
                if tool and tool.terminal and "error" not in result:
                    finished = args | {"_result": result}
            for name, imgs in visuals:
                messages.append({"role": "user", "content": [{"type": "text", "text": f"Images returned by {name}:"}]
                                 + [llm.image_part(p) for p in imgs]})
            if finished is not None:
                return finished
        self.log.emit(self.name, "error", "ran out of steps without finishing")
        return {"error": "max_steps"}


class _Tagged:
    def __init__(self, log: EventLog, tag: str):
        self.log, self.tag = log, tag

    def emit(self, actor: str, kind: str, text: str = "", **data) -> dict:
        return self.log.emit(actor, kind, text, tag=self.tag, **data)


def _compact(result: dict, limit: int = 1500) -> dict:
    """Keep small fields as-is for the event log; elide big ones (point lists) to their size."""
    if len(json.dumps(result, default=str)) <= limit:
        return result
    out = {}
    for k, v in result.items():
        size = len(json.dumps(v, default=str))
        out[k] = v if size <= 400 else f"<{size} chars>"
    return out
