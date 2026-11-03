"""Versioned skill registry with activation and rollback."""

from __future__ import annotations

import json

from .dsl import Toolbox
from .memory import Memory
from .util import new_id, now


class SkillRegistry:
    def __init__(self, memory: Memory, toolbox: Toolbox):
        self.m = memory
        self.toolbox = toolbox
        for row in self.m.q("SELECT * FROM tools"):
            self.toolbox.tools[row["name"]] = json.loads(row["program"])

    def active(self, category: str):
        rows = self.m.q("SELECT * FROM skills WHERE category=? AND status='active' ORDER BY version DESC LIMIT 1",
                        (category,))
        return rows[0] if rows else None

    def active_program(self, category: str) -> dict | None:
        row = self.active(category)
        return json.loads(row["program"]) if row else None

    def get(self, skill_id: str):
        rows = self.m.q("SELECT * FROM skills WHERE id=?", (skill_id,))
        return rows[0] if rows else None

    def next_version(self, category: str) -> int:
        rows = self.m.q("SELECT MAX(version) AS v FROM skills WHERE category=?", (category,))
        return (rows[0]["v"] or 0) + 1

    def add_candidate(self, category: str, program: dict, dev_score: float, origin: str) -> str:
        sid = new_id("skill")
        self.m.x("INSERT INTO skills VALUES (?,?,?,?,?,?,?,?,?)",
                 (sid, category, self.next_version(category), json.dumps(program, ensure_ascii=False),
                  "candidate", dev_score, None, origin, now()))
        return sid

    def activate(self, skill_id: str, heldout_score: float, actor: str) -> str | None:
        row = self.get(skill_id)
        prev = self.active(row["category"])
        if prev:
            self.m.x("UPDATE skills SET status='retired' WHERE id=?", (prev["id"],))
        self.m.x("UPDATE skills SET status='active', heldout_score=? WHERE id=?", (heldout_score, skill_id))
        self.m.log(actor, "skill_activated", skill_id,
                   {"category": row["category"], "version": row["version"],
                    "previous": prev["id"] if prev else None})
        return prev["id"] if prev else None

    def reject(self, skill_id: str) -> None:
        self.m.x("UPDATE skills SET status='rejected' WHERE id=? AND status='candidate'", (skill_id,))

    def rollback(self, category: str, actor: str = "user") -> str | None:
        """Re-activate the most recent retired version."""
        cur = self.active(category)
        rows = self.m.q("SELECT * FROM skills WHERE category=? AND status='retired' ORDER BY version DESC LIMIT 1",
                        (category,))
        if cur:
            self.m.x("UPDATE skills SET status='rolled_back' WHERE id=?", (cur["id"],))
        if rows:
            self.m.x("UPDATE skills SET status='active' WHERE id=?", (rows[0]["id"],))
        self.publish_tool(category)
        self.m.log(actor, "skill_rollback", category,
                   {"from": cur["id"] if cur else None, "to": rows[0]["id"] if rows else None})
        return rows[0]["id"] if rows else None

    def publish_tool(self, category: str) -> None:
        """Tool Evolution: an active text skill becomes a reusable building block."""
        prog = self.active_program(category)
        name = _tool_name(category)
        if prog and "steps" in prog and "linear" not in prog and len(prog["steps"]) >= 1:
            self.toolbox.tools[name] = prog
            ver = self.next_version(category) - 1
            self.m.x("INSERT OR REPLACE INTO tools VALUES (?,?,?,?,?,?)",
                     (name, json.dumps(prog, ensure_ascii=False), f"skill:{category}", ver, 1, now()))
        else:
            self.toolbox.tools.pop(name, None)
            self.m.x("DELETE FROM tools WHERE name=?", (name,))

    def dependents(self, category: str) -> list[str]:
        """Categories whose active skill uses this category's tool."""
        tool = f"tool:{_tool_name(category)}"
        out = []
        for row in self.m.q("SELECT category, program FROM skills WHERE status='active'"):
            if row["category"] != category and tool in json.loads(row["program"]).get("steps", []):
                out.append(row["category"])
        return out


def _tool_name(category: str) -> str:
    return "".join(c if c.isalnum() or c in "_-" else "_" for c in category)
