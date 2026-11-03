"""Model registry and teacher models (Multi-Model Learning Hub).

Teachers are other AI models. MOSAIC learns from them and then
*graduates*: once its own skill matches the teacher on held-out tasks,
the teacher is no longer called for that category.
"""

from __future__ import annotations

import json
import os
import random
import urllib.error
import urllib.request
from typing import Callable, Protocol

from .memory import Memory
from .util import answers_match


class Teacher(Protocol):
    id: str
    provider: str
    allows_training_on_outputs: str  # "yes" | "no" | "unknown"
    allows_private_data: bool
    cost_per_call: float

    def answer(self, category: str, description: str, inp: str) -> str | None: ...


class SimulatedTeacher:
    """A stand-in for a stronger AI model, with a configurable error rate.

    Used for offline demos and tests. Its oracle functions represent what a
    capable external model would answer; `error_rate` models its mistakes.
    """

    def __init__(self, oracles: dict[str, Callable[[str], str]], error_rate: float = 0.05,
                 model_id: str = "sim-teacher-1", cost_per_call: float = 0.002, seed: int = 7):
        self.id = model_id
        self.provider = "simulated"
        self.allows_training_on_outputs = "yes"
        self.allows_private_data = False
        self.cost_per_call = cost_per_call
        self.oracles = oracles
        self.error_rate = error_rate
        self.rng = random.Random(seed)

    def answer(self, category: str, description: str, inp: str) -> str | None:
        fn = self.oracles.get(category)
        if fn is None:
            return None
        out = fn(inp)
        if self.rng.random() < self.error_rate:
            return out + " ?"  # a plausible-looking wrong answer
        return out


class AnthropicTeacher:
    """Real external model via the Messages API (needs ANTHROPIC_API_KEY).

    Check the provider's terms yourself before enabling training on outputs;
    the default is "unknown", which blocks using outputs as training labels.
    """

    def __init__(self, model: str = "claude-sonnet-5-5", allows_training_on_outputs: str = "unknown",
                 cost_per_call: float = 0.01, api_key: str | None = None):
        self.id = f"anthropic:{model}"
        self.provider = "anthropic"
        self.model = model
        self.allows_training_on_outputs = allows_training_on_outputs
        self.allows_private_data = False
        self.cost_per_call = cost_per_call
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")

    def answer(self, category: str, description: str, inp: str) -> str | None:
        if not self.api_key:
            return None
        body = {
            "model": self.model,
            "max_tokens": 200,
            "system": "You are a precise task solver. Reply with the answer only, no explanation.",
            "messages": [{"role": "user", "content": f"Task: {description}\nInput: {inp}\nAnswer:"}],
        }
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(),
            headers={"content-type": "application/json", "x-api-key": self.api_key,
                     "anthropic-version": "2023-06-01"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
        except (urllib.error.URLError, TimeoutError, ValueError):
            return None
        parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
        return "".join(parts).strip() or None


class TeacherHub:
    def __init__(self, memory: Memory):
        self.memory = memory
        self.teachers: dict[str, Teacher] = {}
        self.calls_this_cycle = 0

    def register(self, teacher: Teacher) -> None:
        self.teachers[teacher.id] = teacher
        self.memory.x(
            "INSERT OR IGNORE INTO models(id, provider, kind, allows_training_on_outputs, allows_private_data,"
            " cost_per_call) VALUES (?,?,?,?,?,?)",
            (teacher.id, teacher.provider, "teacher", teacher.allows_training_on_outputs,
             int(teacher.allows_private_data), teacher.cost_per_call))
        self.memory.log("system", "model_registered", teacher.id,
                        {"training_allowed": teacher.allows_training_on_outputs})

    def available(self, category: str) -> bool:
        m = self.memory
        cat = m.category(category)
        return bool(self.teachers and m.setting("online") and m.setting("allow_teacher")
                    and cat is not None and not cat["graduated"])

    def best_teacher(self) -> Teacher | None:
        if not self.teachers:
            return None
        rows = {r["id"]: r for r in self.memory.q("SELECT * FROM models WHERE enabled=1")}

        def measured(t: Teacher) -> float:
            r = rows.get(t.id)
            if r is None:
                return -1
            return (r["correct"] + 1) / (r["checked"] + 2)  # Laplace-smoothed measured accuracy
        enabled = [t for t in self.teachers.values() if t.id in rows]
        return max(enabled, key=measured) if enabled else None

    def ask(self, category: str, inp: str, private: bool = False, for_training: bool = False,
            budget: int | None = None, scope: str = "cycle") -> tuple[str | None, Teacher | None]:
        """Ask a teacher, enforcing connectivity, graduation, privacy, budget and held-out rules."""
        if not self.available(category):
            return None, None
        if self.memory.is_heldout_input(category, inp):
            return None, None  # never leak proof sets to a teacher
        if scope == "cycle":
            limit = budget if budget is not None else int(self.memory.setting("teacher_budget_per_cycle"))
            if self.calls_this_cycle >= limit:
                return None, None
        spent = self.memory.q("SELECT COALESCE(SUM(calls*cost_per_call),0) AS s FROM models")[0]["s"]
        if spent >= float(self.memory.setting("money_budget_usd")):
            return None, None  # user-defined money cap reached
        t = self.best_teacher()
        if t is None or (private and not t.allows_private_data):
            return None, None
        if for_training and t.allows_training_on_outputs != "yes":
            return None, None
        cat = self.memory.category(category)
        if scope == "cycle":
            self.calls_this_cycle += 1
        ans = t.answer(category, cat["description"], inp)
        self.memory.x("UPDATE models SET calls=calls+1 WHERE id=?", (t.id,))
        self.memory.x("UPDATE categories SET teacher_calls=teacher_calls+1 WHERE name=?", (category,))
        return ans, t

    def grade_teacher(self, teacher_id: str, teacher_answer: str | None, truth: str) -> None:
        self.memory.x("UPDATE models SET checked=checked+1, correct=correct+? WHERE id=?",
                      (int(answers_match(teacher_answer, truth)), teacher_id))

    def measured_accuracy(self, teacher_id: str) -> float | None:
        rows = self.memory.q("SELECT checked, correct FROM models WHERE id=?", (teacher_id,))
        if not rows or rows[0]["checked"] == 0:
            return None
        return rows[0]["correct"] / rows[0]["checked"]
