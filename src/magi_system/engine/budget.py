"""Budget accounting (brief §12). Consumption is persisted in graph state via reducers;
this tracker enforces limits during node execution and reports deltas.

Cost is enforced only when the provider reports it; otherwise call/token limits apply.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from magi_system.models.run import RunBudget


class BudgetExhausted(Exception):
    def __init__(self, dimension: str, detail: str) -> None:
        super().__init__(f"budget exhausted: {dimension} ({detail})")
        self.dimension = dimension


@dataclass
class BudgetTracker:
    budget: RunBudget
    started_at: float
    tool_calls: int = 0
    llm_calls: int = 0
    tokens: int = 0
    tokens_measured: bool = False
    cost_usd: float = 0.0
    cost_measured: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def check_time(self) -> None:
        elapsed = time.time() - self.started_at
        if elapsed > self.budget.max_duration_seconds:
            raise BudgetExhausted("duration", f"{elapsed:.0f}s > {self.budget.max_duration_seconds:.0f}s")

    def on_tool_call(self, _tool: str = "") -> None:
        with self._lock:
            self.check_time()
            if self.tool_calls >= self.budget.max_tool_calls:
                raise BudgetExhausted("tool_calls", f"limit {self.budget.max_tool_calls}")
            self.tool_calls += 1

    def before_llm_call(self) -> None:
        with self._lock:
            self.check_time()
            if self.llm_calls >= self.budget.max_llm_calls:
                raise BudgetExhausted("llm_calls", f"limit {self.budget.max_llm_calls}")
            if self.budget.max_tokens and self.tokens_measured and self.tokens >= self.budget.max_tokens:
                raise BudgetExhausted("tokens", f"limit {self.budget.max_tokens}")
            if self.budget.max_cost_usd and self.cost_measured and self.cost_usd >= self.budget.max_cost_usd:
                raise BudgetExhausted("cost", f"limit {self.budget.max_cost_usd}")
            self.llm_calls += 1

    def after_llm_call(self, input_tokens: int | None, output_tokens: int | None, cost: float | None) -> None:
        with self._lock:
            if input_tokens is not None or output_tokens is not None:
                self.tokens_measured = True
                self.tokens += (input_tokens or 0) + (output_tokens or 0)
            if cost is not None:
                self.cost_measured = True
                self.cost_usd += cost

    def snapshot(self) -> dict:
        return {
            "tool_calls": self.tool_calls,
            "llm_calls": self.llm_calls,
            "tokens": self.tokens if self.tokens_measured else None,
            "cost_usd": round(self.cost_usd, 6) if self.cost_measured else None,
            "elapsed_seconds": round(time.time() - self.started_at, 3),
        }
