"""Tool-calling agent loop shared by the director, the labs and the reviewer."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from orbital import llm
from orbital.agents.events import EventLog

MAX_TOOL_RESULT_CHARS = 14000


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
    def __init__(self, name: str, system: str, tools: list[Tool], log: EventLog, provider: str, max_steps: int = 14):
        self.name, self.system, self.log, self.provider, self.max_steps = name, system, log, provider, max_steps
        self.tools = {t.name: t for t in tools}
        self.results: list[dict] = []  # every tool output, for auditing the numbers the agent later claims

    def run(self, task: str, images: list[Path] = ()) -> dict:
        user = [{"type": "text", "text": task}] + [llm.image_part(p) for p in images]
        messages = [{"role": "system", "content": self.system}, {"role": "user", "content": user}]
        specs = [t.spec() for t in self.tools.values()]
        for step in range(self.max_steps):
            reply = llm.chat(messages, tools=specs, provider=self.provider)
            if reply.reasoning:
                self.log.emit(self.name, "thought", reply.reasoning.strip()[:600], model=llm.model_name(self.provider),
                              seconds=round(reply.seconds, 1))
            if reply.content.strip():
                self.log.emit(self.name, "say", reply.content.strip()[:2000])
            messages.append(reply.as_message())

            if not reply.tool_calls:
                terminal = [t.name for t in self.tools.values() if t.terminal]
                messages.append({"role": "user", "content": f"Continue with tools. Finish by calling {' or '.join(terminal)}."})
                continue

            visuals, finished = [], None
            for tc in reply.tool_calls:
                tool = self.tools.get(tc.function.name)
                try:
                    args = llm.parse_args(tc.function.arguments)
                    if tool is None:
                        raise ValueError(f"unknown tool {tc.function.name}")
                    self.log.emit(self.name, "tool_call", tool.name, args=args)
                    result = tool.fn(args)
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


def _compact(result: dict, limit: int = 1500) -> dict:
    text = json.dumps(result, default=str)
    return result if len(text) <= limit else {"summary": text[:limit] + "…"}
