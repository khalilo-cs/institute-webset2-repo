"""Memory system: eight logical stores + registries on one SQLite file.

Every store sits behind this class, so it can later be swapped for
PostgreSQL/pgvector, Qdrant, etc. without touching the rest of MOSAIC.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any, Iterable

import math

from .util import cosine, embed, new_id, normalize, now, stable_hash, tokens

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
  name TEXT PRIMARY KEY, kind TEXT NOT NULL, description TEXT NOT NULL,
  language TEXT NOT NULL DEFAULT 'ar', created_at REAL NOT NULL,
  graduated INTEGER NOT NULL DEFAULT 0, graduated_at REAL,
  teacher_calls INTEGER NOT NULL DEFAULT 0, total_tasks INTEGER NOT NULL DEFAULT 0,
  frozen INTEGER NOT NULL DEFAULT 0, failed_attempts INTEGER NOT NULL DEFAULT 0,
  seen_data INTEGER NOT NULL DEFAULT -1
);
-- 1. semantic memory
CREATE TABLE IF NOT EXISTS semantic (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, category TEXT, key_hash TEXT NOT NULL,
  question TEXT NOT NULL, answer TEXT NOT NULL, embedding TEXT NOT NULL,
  source TEXT NOT NULL, confidence REAL NOT NULL, language TEXT,
  acquired_mode TEXT NOT NULL, verified INTEGER NOT NULL DEFAULT 0,
  conflict INTEGER NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_sem_key ON semantic(user_id, key_hash);
-- 2. episodic / experience memory
CREATE TABLE IF NOT EXISTS episodes (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, category TEXT NOT NULL,
  input TEXT NOT NULL, answer TEXT, solver TEXT NOT NULL, expected TEXT,
  correct INTEGER, cost REAL NOT NULL DEFAULT 0, language TEXT,
  archived INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ep_cat ON episodes(category, created_at);
-- 3. skill memory (versioned)
CREATE TABLE IF NOT EXISTS skills (
  id TEXT PRIMARY KEY, category TEXT NOT NULL, version INTEGER NOT NULL,
  program TEXT NOT NULL, status TEXT NOT NULL, dev_score REAL, heldout_score REAL,
  origin TEXT NOT NULL, created_at REAL NOT NULL
);
-- 4. tool memory (tools discovered/composed by the system)
CREATE TABLE IF NOT EXISTS tools (
  name TEXT PRIMARY KEY, program TEXT NOT NULL, origin TEXT NOT NULL,
  version INTEGER NOT NULL, tests_passed INTEGER NOT NULL, created_at REAL NOT NULL
);
-- 5. experiment results (Evolution Lab improvements)
CREATE TABLE IF NOT EXISTS improvements (
  id TEXT PRIMARY KEY, type TEXT NOT NULL, category TEXT NOT NULL, target TEXT NOT NULL,
  reason TEXT NOT NULL, origin TEXT NOT NULL, baseline_version TEXT,
  candidate_version TEXT, results TEXT NOT NULL, cost TEXT NOT NULL,
  risk TEXT NOT NULL, requires_approval INTEGER NOT NULL, decision TEXT NOT NULL,
  decided_by TEXT, rollback_to TEXT, created_at REAL NOT NULL, decided_at REAL
);
-- 6. errors and fixes
CREATE TABLE IF NOT EXISTS errors (
  id TEXT PRIMARY KEY, category TEXT NOT NULL, pattern TEXT NOT NULL,
  cause TEXT, fix TEXT, count INTEGER NOT NULL DEFAULT 1, last_seen REAL NOT NULL,
  UNIQUE(category, pattern)
);
-- 7. consented preferences only
CREATE TABLE IF NOT EXISTS preferences (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
  consent_scope TEXT NOT NULL, consented_at REAL NOT NULL, UNIQUE(user_id, key)
);
-- 8. change & version log (append-only)
CREATE TABLE IF NOT EXISTS changelog (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, actor TEXT NOT NULL,
  change_type TEXT NOT NULL, entity TEXT NOT NULL, detail TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS changelog_no_update BEFORE UPDATE ON changelog
BEGIN SELECT RAISE(ABORT, 'changelog is append-only'); END;
CREATE TRIGGER IF NOT EXISTS changelog_no_delete BEFORE DELETE ON changelog
BEGIN SELECT RAISE(ABORT, 'changelog is append-only'); END;
-- labelled examples (dev for learning, heldout for proof)
CREATE TABLE IF NOT EXISTS examples (
  id TEXT PRIMARY KEY, category TEXT NOT NULL, input TEXT NOT NULL, expected TEXT NOT NULL,
  split TEXT NOT NULL, source TEXT NOT NULL, verified INTEGER NOT NULL,
  input_hash TEXT NOT NULL, created_at REAL NOT NULL,
  UNIQUE(category, input_hash, source)
);
CREATE TABLE IF NOT EXISTS models (
  id TEXT PRIMARY KEY, provider TEXT NOT NULL, kind TEXT NOT NULL,
  allows_training_on_outputs TEXT NOT NULL, allows_private_data INTEGER NOT NULL,
  cost_per_call REAL NOT NULL, calls INTEGER NOT NULL DEFAULT 0,
  checked INTEGER NOT NULL DEFAULT 0, correct INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT NOT NULL,
  created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY, conv_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
  source TEXT, rating INTEGER, created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_msg_conv ON messages(conv_id, created_at);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS verify_queue (
  id TEXT PRIMARY KEY, category TEXT, input TEXT, claim TEXT, reason TEXT, created_at REAL
);
"""

DEFAULT_SETTINGS = {
    "online": True,
    "allow_teacher": True,
    "graduation_threshold": 0.95,
    "graduation_margin": 0.02,
    "min_heldout": 10,
    "max_regression_drop": 0.02,
    "max_failed_attempts": 3,
    "teacher_budget_per_cycle": 40,
    "money_budget_usd": 5.0,
    "require_approval_all": False,
    "approval_cost_usd": 1.0,
    "episode_limit": 5000,
    "llm_backend": "none",          # none | anthropic | openai_compat
    "llm_model": "",
    "llm_base_url": "http://127.0.0.1:11434/v1",
    "allow_cloud_llm": True,        # False = only local models may see conversations
    "memory_answer_threshold": 0.9,
}


class Memory:
    def __init__(self, path: str = ":memory:"):
        self.path = path
        self._lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        for k, v in DEFAULT_SETTINGS.items():
            self.db.execute("INSERT OR IGNORE INTO settings VALUES (?, ?)", (k, json.dumps(v)))
        self.db.commit()

    # ---------- low level ----------
    def q(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.db.execute(sql, tuple(params)).fetchall()

    def x(self, sql: str, params: Iterable[Any] = ()) -> None:
        with self._lock:
            self.db.execute(sql, tuple(params))
            self.db.commit()

    # ---------- settings ----------
    def setting(self, key: str) -> Any:
        rows = self.q("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(rows[0]["value"]) if rows else DEFAULT_SETTINGS.get(key)

    def set_setting(self, key: str, value: Any, actor: str = "user") -> None:
        if key not in DEFAULT_SETTINGS:
            raise KeyError(f"unknown setting {key}")
        default = DEFAULT_SETTINGS[key]
        if isinstance(default, bool):
            if not isinstance(value, bool):
                raise ValueError(f"{key} must be true/false")
        elif isinstance(default, (int, float)):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{key} must be a number")
        elif not isinstance(value, str):
            raise ValueError(f"{key} must be text")
        if key == "llm_backend" and value not in ("none", "anthropic", "openai_compat"):
            raise ValueError("llm_backend must be none, anthropic or openai_compat")
        self.x("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, json.dumps(value)))
        self.log(actor, "setting", key, {"value": value})

    # ---------- change log ----------
    def log(self, actor: str, change_type: str, entity: str, detail: dict) -> None:
        self.x("INSERT INTO changelog(ts, actor, change_type, entity, detail) VALUES (?,?,?,?,?)",
               (now(), actor, change_type, entity, json.dumps(detail, ensure_ascii=False)))

    # ---------- categories ----------
    def add_category(self, name: str, kind: str, description: str, language: str = "ar") -> None:
        if kind not in ("transform", "qa"):
            raise ValueError("kind must be 'transform' or 'qa'")
        self.x("INSERT OR IGNORE INTO categories(name, kind, description, language, created_at)"
               " VALUES (?,?,?,?,?)", (name, kind, description, language, now()))
        self.log("system", "category_added", name, {"kind": kind, "language": language})

    def category(self, name: str) -> sqlite3.Row | None:
        rows = self.q("SELECT * FROM categories WHERE name=?", (name,))
        return rows[0] if rows else None

    def categories(self) -> list[sqlite3.Row]:
        return self.q("SELECT * FROM categories ORDER BY created_at")

    # ---------- examples ----------
    def add_example(self, category: str, inp: str, expected: str, source: str,
                    verified: bool, split: str | None = None) -> str:
        h = stable_hash(inp)
        if split is None:
            # deterministic split, ~30% held-out; teacher labels never go to held-out
            split = "heldout" if (verified and int(h[:4], 16) % 10 < 3) else "dev"
        if source == "teacher":
            split = "dev"
            if self.is_heldout_input(category, inp):
                raise PermissionError("held-out inputs must never be labelled by a teacher")
        eid = new_id("ex")
        self.x("INSERT OR REPLACE INTO examples VALUES (?,?,?,?,?,?,?,?,?)",
               (eid, category, inp, expected, split, source, int(verified), h, now()))
        return split

    def examples(self, category: str, split: str | None = None,
                 verified_only: bool = False) -> list[sqlite3.Row]:
        sql = "SELECT * FROM examples WHERE category=?"
        params: list[Any] = [category]
        if split:
            sql += " AND split=?"
            params.append(split)
        if verified_only:
            sql += " AND verified=1"
        return self.q(sql + " ORDER BY created_at", params)

    def is_heldout_input(self, category: str, inp: str) -> bool:
        return bool(self.q("SELECT 1 FROM examples WHERE category=? AND input_hash=? AND split='heldout'",
                           (category, stable_hash(inp))))

    # ---------- semantic memory ----------
    def add_fact(self, question: str, answer: str, source: str, confidence: float,
                 acquired_mode: str, category: str | None = None, language: str = "ar",
                 user_id: str = "default", verified: bool = False) -> str:
        key = stable_hash(question)
        existing = self.q("SELECT * FROM semantic WHERE user_id=? AND key_hash=? AND archived=0",
                          (user_id, key))
        for row in existing:
            if normalize(row["answer"]) == normalize(answer):
                # duplicate: merge evidence instead of storing twice
                conf = min(1.0, max(row["confidence"], confidence) + (0.1 if row["source"] != source else 0))
                self.x("UPDATE semantic SET confidence=?, verified=MAX(verified, ?), updated_at=? WHERE id=?",
                       (conf, int(verified), now(), row["id"]))
                return row["id"]
        conflict = int(bool(existing))
        fid = new_id("fact")
        self.x("INSERT INTO semantic VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (fid, user_id, category, key, question, answer, json.dumps(embed(question)),
                source, confidence, language, acquired_mode, int(verified), conflict, 0, now(), now()))
        if conflict:
            self.x("UPDATE semantic SET conflict=1 WHERE user_id=? AND key_hash=?", (user_id, key))
            self.log("system", "conflict_detected", fid, {"question": question})
        return fid

    def search(self, question: str, user_id: str = "default", k: int = 5, min_sim: float = 0.0,
               min_conf: float = 0.5, category: str | None = None) -> list[tuple[float, sqlite3.Row]]:
        """Hybrid retrieval: IDF-weighted token overlap (entities matter more
        than frequent words like "capital") + embedding cosine."""
        sql = "SELECT * FROM semantic WHERE user_id=? AND archived=0"
        params: list[Any] = [user_id]
        if category:
            sql += " AND category=?"
            params.append(category)
        rows = self.q(sql, params)
        if not rows:
            return []
        docs = [set(tokens(r["question"], True)) for r in rows]
        df: dict[str, int] = {}
        for d in docs:
            for t in d:
                df[t] = df.get(t, 0) + 1
        n = len(docs)
        idf = lambda t: math.log((n + 1) / (df.get(t, 0) + 0.5))
        q_toks = set(tokens(question, True))
        q_weight = sum(idf(t) for t in q_toks) or 1.0
        qv = embed(question)
        scored = []
        for row, d in zip(rows, docs):
            if row["confidence"] < min_conf or (row["conflict"] and not row["verified"]):
                continue  # never answer from low confidence or an unresolved conflict
            overlap = sum(idf(t) for t in q_toks & d) / q_weight
            s = 0.7 * overlap + 0.3 * max(0.0, cosine(qv, json.loads(row["embedding"])))
            if s > min_sim:
                scored.append((s, row))
        scored.sort(key=lambda x: -x[0])
        return scored[:k]

    def recall(self, question: str, user_id: str = "default", min_sim: float = 0.55,
               min_conf: float = 0.5, category: str | None = None) -> sqlite3.Row | None:
        hits = self.search(question, user_id, 1, min_sim, min_conf, category)
        return hits[0][1] if hits else None

    # ---------- episodes ----------
    def add_episode(self, category: str, inp: str, answer: str | None, solver: str,
                    expected: str | None = None, correct: bool | None = None,
                    cost: float = 0.0, user_id: str = "default", language: str = "ar") -> str:
        eid = new_id("ep")
        self.x("INSERT INTO episodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
               (eid, user_id, category, inp, answer, solver, expected,
                None if correct is None else int(correct), cost, language, 0, now()))
        return eid

    def episode(self, eid: str) -> sqlite3.Row | None:
        rows = self.q("SELECT * FROM episodes WHERE id=?", (eid,))
        return rows[0] if rows else None

    # ---------- errors ----------
    def record_error(self, category: str, pattern: str, cause: str = "", fix: str = "") -> None:
        self.x("INSERT INTO errors VALUES (?,?,?,?,?,1,?) ON CONFLICT(category, pattern) DO UPDATE SET "
               "count=count+1, last_seen=excluded.last_seen, fix=COALESCE(NULLIF(excluded.fix,''), fix)",
               (new_id("err"), category, pattern, cause, fix, now()))

    # ---------- preferences ----------
    def set_preference(self, key: str, value: str, consent_scope: str, user_id: str = "default") -> None:
        if not consent_scope:
            raise PermissionError("preferences are stored only with explicit consent")
        self.x("INSERT OR REPLACE INTO preferences VALUES (?,?,?,?,?,?)",
               (new_id("pref"), user_id, key, value, consent_scope, now()))

    # ---------- storage policy ----------
    def compact(self) -> dict:
        """Declared policy: dedupe facts, archive old episodes. Never deletes
        preferences, verified facts or examples."""
        archived = 0
        limit = int(self.setting("episode_limit"))
        rows = self.q("SELECT id FROM episodes WHERE archived=0 ORDER BY created_at DESC")
        for row in rows[limit:]:
            self.x("UPDATE episodes SET archived=1 WHERE id=?", (row["id"],))
            archived += 1
        dupes = 0
        seen: dict[tuple, str] = {}
        for row in self.q("SELECT id, user_id, key_hash, answer FROM semantic WHERE archived=0 AND verified=0"):
            k = (row["user_id"], row["key_hash"], normalize(row["answer"]))
            if k in seen:
                self.x("UPDATE semantic SET archived=1 WHERE id=?", (row["id"],))
                dupes += 1
            else:
                seen[k] = row["id"]
        result = {"episodes_archived": archived, "facts_deduplicated": dupes}
        self.log("system", "compact", "memory", result)
        return result
