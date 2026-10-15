"""LLM adapter protocol, structured-output parsing and the offline scripted model.

Agents ask for a JSON object matching a Pydantic schema. Parsing/validation happens in
code; an invalid answer becomes a typed `AgentFailure`, never a silent default.
No chain-of-thought is requested or stored: schemas carry concise rationales only.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError


class ModelTimeout(Exception):
    pass


class ModelProviderError(Exception):
    pass


class InvalidStructuredOutput(Exception):
    pass


@dataclass(frozen=True)
class ModelRequest:
    agent: str  # internal agent id
    purpose: str  # e.g. "analysis", "proposal", "cross_review"
    round: int
    system: str
    prompt: str
    schema_name: str
    json_schema: dict[str, Any]
    prompt_version: str


@dataclass(frozen=True)
class ModelResponse:
    text: str
    input_tokens: int | None = None  # None when the provider does not report usage
    output_tokens: int | None = None
    cost_usd: float | None = None
    model: str = ""


class ModelAdapter(Protocol):
    name: str

    def complete(self, request: ModelRequest, *, timeout: float) -> ModelResponse: ...


_FENCE = re.compile(r"```(?:json)?\s*(\{[\s\S]*\})\s*```")


def parse_structured[T: BaseModel](text: str, schema: type[T]) -> T:
    """Parse the first JSON object in `text` and validate it against `schema`."""
    candidate = text.strip()
    m = _FENCE.search(candidate)
    if m:
        candidate = m.group(1)
    elif not candidate.startswith("{"):
        start, end = candidate.find("{"), candidate.rfind("}")
        if start == -1 or end <= start:
            raise InvalidStructuredOutput("no JSON object in model output")
        candidate = candidate[start : end + 1]
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise InvalidStructuredOutput(f"model output is not valid JSON: {exc}") from exc
    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        raise InvalidStructuredOutput(f"model output failed schema {schema.__name__}: {exc}") from exc


# -- offline scripted model ---------------------------------------------------------------

Responder = Callable[[ModelRequest], "str | dict | BaseModel | Exception"]


@dataclass
class ScriptedModel:
    """Deterministic fake model for offline tests and demos.

    Scripts are keyed by (agent, purpose). The value is either a list consumed in order
    (one per call; the last one repeats) or a callable receiving the request. A value may
    be a dict / Pydantic model (serialized to JSON), raw text (to simulate invalid output),
    or an exception instance to raise (e.g. `ModelTimeout()`).

    This demonstrates orchestration behavior only; it says nothing about real model quality.
    """

    scripts: dict[tuple[str, str], list | Responder] = field(default_factory=dict)
    name: str = "scripted"
    calls: list[ModelRequest] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _cursor: dict[tuple[str, str], int] = field(default_factory=dict, repr=False)

    def set(self, agent: str, purpose: str, responses: list | Responder) -> ScriptedModel:
        self.scripts[(agent, purpose)] = responses
        return self

    def complete(self, request: ModelRequest, *, timeout: float) -> ModelResponse:
        key = (request.agent, request.purpose)
        with self._lock:
            self.calls.append(request)
            script = self.scripts.get(key)
            if script is None:
                raise ModelProviderError(f"no scripted response for {key}")
            if callable(script):
                value = script(request)
            else:
                i = self._cursor.get(key, 0)
                value = script[min(i, len(script) - 1)]
                self._cursor[key] = i + 1
        if isinstance(value, Exception):
            raise value
        if isinstance(value, BaseModel):
            text = value.model_dump_json()
        elif isinstance(value, dict):
            text = json.dumps(value)
        else:
            text = str(value)
        return ModelResponse(text=text, input_tokens=len(request.prompt) // 4, output_tokens=len(text) // 4, model="scripted")
