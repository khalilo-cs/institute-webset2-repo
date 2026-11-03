"""Resource-aware learning scheduler.

Decides *whether* learning is worth it, in which order, and under which
device conditions. Jobs can be scheduled, paused, resumed and cancelled.
"""

from __future__ import annotations

import json

from .memory import Memory
from .util import new_id, now

JOB_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, category TEXT NOT NULL, priority REAL NOT NULL,
  status TEXT NOT NULL, detail TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
);
"""

STATES = {"queued", "running", "paused", "cancelled", "done", "skipped"}


class Scheduler:
    def __init__(self, memory: Memory):
        self.m = memory
        self.m.db.executescript(JOB_SCHEMA)
        # device status is pushed by the host app (Android reports real values)
        self.device = {"battery_pct": 100, "charging": True, "metered_network": False, "thermal_ok": True}

    def update_device(self, **status) -> None:
        for k, v in status.items():
            if k in self.device:
                self.device[k] = v

    def can_run_heavy(self) -> tuple[bool, str]:
        d = self.device
        if not d["thermal_ok"]:
            return False, "الجهاز ساخن"
        if not d["charging"] and d["battery_pct"] < 30:
            return False, "البطارية منخفضة ولا يوجد شحن"
        return True, ""

    def can_use_network(self) -> bool:
        return bool(self.m.setting("online")) and not self.device["metered_network"]

    @staticmethod
    def priority(importance: float, frequency: float, failure_rate: float,
                 expected_gain: float, cost: float, user_weight: float = 1.0) -> float:
        return (importance * (1 + frequency) * failure_rate * expected_gain * user_weight) / (cost + 1e-3)

    def enqueue(self, kind: str, category: str, priority: float, detail: dict | None = None) -> str:
        existing = self.m.q("SELECT id FROM jobs WHERE category=? AND kind=? AND status IN ('queued','paused')",
                            (category, kind))
        if existing:
            self.m.x("UPDATE jobs SET priority=?, updated_at=? WHERE id=?", (priority, now(), existing[0]["id"]))
            return existing[0]["id"]
        jid = new_id("job")
        self.m.x("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)",
                 (jid, kind, category, priority, "queued", json.dumps(detail or {}, ensure_ascii=False), now(), now()))
        return jid

    def set_state(self, job_id: str, state: str) -> None:
        if state not in STATES:
            raise ValueError(state)
        self.m.x("UPDATE jobs SET status=?, updated_at=? WHERE id=?", (state, now(), job_id))

    def pause(self, job_id: str) -> None:
        self.set_state(job_id, "paused")

    def resume(self, job_id: str) -> None:
        self.set_state(job_id, "queued")

    def cancel(self, job_id: str) -> None:
        self.set_state(job_id, "cancelled")

    def next_jobs(self, limit: int = 10):
        return self.m.q("SELECT * FROM jobs WHERE status='queued' ORDER BY priority DESC LIMIT ?", (limit,))

    def jobs(self, limit: int = 30):
        return self.m.q("SELECT * FROM jobs ORDER BY updated_at DESC LIMIT ?", (limit,))
