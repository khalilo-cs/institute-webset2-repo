"""Conversational layer: a general assistant built on a pluggable LLM, with
MOSAIC's own memory, corrections and offline answers on top.

Order of answering:
  1. Explicit memory commands ("تذكر أن ...").
  2. A user-verified fact that matches very strongly → answered by MOSAIC
     itself (works offline, no cost, and corrections always stick).
  3. The configured LLM, given the relevant verified facts and saved
     preferences as context.
  4. No LLM available → say so honestly and offer what memory has.
"""

from __future__ import annotations

import re

from .llm import LLMBackend, LLMError, backend_from_settings
from .util import new_id, now, stable_hash

CHAT_CATEGORY = "chat"
HISTORY_CHARS = 24_000
MAX_HISTORY_MESSAGES = 40

REMEMBER = re.compile(r"^\s*(?:تذكر|تذكّر|احفظ|remember)\s*(?:أن|ان|that)?\s*[:،,]?\s*(.+)$", re.I | re.S)
FORGET_ALL = re.compile(r"^\s*(?:انس|انسَ|forget)\s+(?:كل شيء|كل شي|everything)\s*$", re.I)

SYSTEM_PROMPT = """أنت MOSAIC، مساعد ذكي يتعلم باستمرار من تصحيحات مستخدمه.
- أجب بلغة المستخدم. إذا كتب بالعربية فأجب بعربية فصحى واضحة.
- كن دقيقًا وصادقًا. إذا لم تكن متأكدًا فقل ذلك ولا تخترع معلومات أو مصادر.
- استخدم Markdown عند الحاجة (عناوين قصيرة، قوائم، كتل كود).
- المعلومات تحت "حقائق تحقق منها المستخدم" صحّحها المستخدم بنفسه: قدّمها على معرفتك العامة.
- احترم تفضيلات المستخدم المحفوظة."""


class ChatEngine:
    def __init__(self, mosaic, backend: LLMBackend | None = None):
        self.mo = mosaic
        self.m = mosaic.m
        self._backend = backend
        if self.m.category(CHAT_CATEGORY) is None:
            self.m.add_category(CHAT_CATEGORY, "qa", "تصحيحات المحادثة العامة", "ar")

    # ------------------------------------------------------------ backend
    def backend(self) -> LLMBackend | None:
        b = self._backend if self._backend is not None else backend_from_settings(self.m)
        if b is None:
            return None
        if not b.is_local and not (self.m.setting("online") and self.m.setting("allow_cloud_llm")):
            return None  # offline, or the user forbade cloud models
        return b

    def status(self) -> dict:
        b = self.backend()
        configured = self._backend or backend_from_settings(self.m)
        return {"backend": b.id if b else None, "configured": configured.id if configured else None,
                "local": bool(b and b.is_local)}

    # ------------------------------------------------------------ conversations
    def new_conversation(self, user_id: str = "default", title: str = "محادثة جديدة") -> str:
        cid = new_id("conv")
        self.m.x("INSERT INTO conversations VALUES (?,?,?,?,?)", (cid, user_id, title, now(), now()))
        return cid

    def conversations(self, user_id: str = "default") -> list[dict]:
        return [dict(r) for r in self.m.q("SELECT * FROM conversations WHERE user_id=? ORDER BY updated_at DESC",
                                          (user_id,))]

    def messages(self, conv_id: str, user_id: str = "default") -> list[dict]:
        self._own(conv_id, user_id)
        return [dict(r) for r in self.m.q("SELECT * FROM messages WHERE conv_id=? ORDER BY created_at, rowid",
                                          (conv_id,))]

    def delete_conversation(self, conv_id: str, user_id: str = "default") -> None:
        self._own(conv_id, user_id)
        self.m.x("DELETE FROM messages WHERE conv_id=?", (conv_id,))
        self.m.x("DELETE FROM conversations WHERE id=?", (conv_id,))

    def _own(self, conv_id: str, user_id: str):
        rows = self.m.q("SELECT * FROM conversations WHERE id=? AND user_id=?", (conv_id, user_id))
        if not rows:
            raise KeyError("conversation not found")
        return rows[0]

    def _add(self, conv_id: str, role: str, content: str, source: str | None = None) -> str:
        mid = new_id("msg")
        self.m.x("INSERT INTO messages VALUES (?,?,?,?,?,?,?)", (mid, conv_id, role, content, source, None, now()))
        self.m.x("UPDATE conversations SET updated_at=? WHERE id=?", (now(), conv_id))
        return mid

    # ------------------------------------------------------------ memory helpers
    def preferences(self, user_id: str = "default") -> list[dict]:
        return [dict(r) for r in self.m.q("SELECT id, key, value, consented_at FROM preferences WHERE user_id=? "
                                          "AND key LIKE 'note:%' ORDER BY consented_at", (user_id,))]

    def delete_preference(self, pref_id: str, user_id: str = "default") -> None:
        self.m.x("DELETE FROM preferences WHERE id=? AND user_id=?", (pref_id, user_id))
        self.m.log(user_id, "memory_deleted", pref_id, {})

    def _context(self, text: str, user_id: str) -> str:
        parts = [SYSTEM_PROMPT]
        prefs = self.preferences(user_id)
        if prefs:
            parts.append("ما طلب المستخدم أن تتذكره:\n" + "\n".join(f"- {p['value']}" for p in prefs[-30:]))
        facts = self.m.search(text, user_id=user_id, k=6, min_sim=0.3, min_conf=0.9)
        if facts:
            parts.append("حقائق تحقق منها المستخدم:\n" +
                         "\n".join(f"- س: {r['question']}\n  ج: {r['answer']}" for _, r in facts))
        return "\n\n".join(parts)

    def _history(self, conv_id: str) -> list[dict]:
        rows = self.m.q("SELECT role, content FROM messages WHERE conv_id=? AND role IN ('user','assistant') "
                        "ORDER BY created_at DESC, rowid DESC LIMIT ?", (conv_id, MAX_HISTORY_MESSAGES))
        out, total = [], 0
        for r in rows:
            total += len(r["content"])
            if total > HISTORY_CHARS and out:
                break
            out.append({"role": r["role"], "content": r["content"]})
        out.reverse()
        while out and out[0]["role"] != "user":
            out.pop(0)  # APIs expect the history to start with a user turn
        return out

    # ------------------------------------------------------------ the main entry point
    def send(self, conv_id: str | None, text: str, user_id: str = "default") -> dict:
        text = (text or "").strip()
        if not text:
            raise ValueError("رسالة فارغة")
        if len(text) > 20_000:
            raise ValueError("الرسالة طويلة جدًا")
        if not conv_id:
            conv_id = self.new_conversation(user_id, title=text[:60])
        else:
            self._own(conv_id, user_id)
        self._add(conv_id, "user", text)

        m = REMEMBER.match(text)
        if m:
            note = m.group(1).strip()
            self.m.set_preference(f"note:{stable_hash(note)[:16]}", note, "explicit_user_command", user_id)
            return self._reply(conv_id, f"حسنًا، سأتذكر ذلك: «{note}»", "mosaic:memory")
        if FORGET_ALL.match(text):
            n = len(self.preferences(user_id))
            self.m.x("DELETE FROM preferences WHERE user_id=? AND key LIKE 'note:%'", (user_id,))
            self.m.log(user_id, "memory_cleared", "preferences", {"count": n})
            return self._reply(conv_id, f"حذفت {n} ملاحظة محفوظة.", "mosaic:memory")

        threshold = float(self.m.setting("memory_answer_threshold"))
        hits = self.m.search(text, user_id=user_id, k=1, min_sim=threshold, min_conf=0.9, category=CHAT_CATEGORY)
        if hits:
            return self._reply(conv_id, hits[0][1]["answer"], "mosaic:memory")

        backend = self.backend()
        if backend is None:
            hint = self.m.search(text, user_id=user_id, k=1, min_sim=0.5, min_conf=0.9)
            msg = ("لا يوجد نموذج لغوي متاح الآن (غير متصل، أو لم يُضبط نموذج في الإعدادات). "
                   "أستطيع الإجابة فقط مما تعلمته منك.")
            if hint:
                msg += f"\n\nأقرب ما أعرفه: {hint[0][1]['answer']}"
            return self._reply(conv_id, msg, "none")
        try:
            answer = backend.chat(self._context(text, user_id), self._history(conv_id))
        except LLMError as e:
            return self._reply(conv_id, f"تعذّر الاتصال بالنموذج ({backend.id}): {e}", "error")
        return self._reply(conv_id, answer, f"llm:{backend.id}")

    def _reply(self, conv_id: str, content: str, source: str) -> dict:
        mid = self._add(conv_id, "assistant", content, source)
        return {"conversation_id": conv_id, "message_id": mid, "content": content, "source": source}

    # ------------------------------------------------------------ learning from the user
    def rate(self, message_id: str, good: bool, correction: str | None = None,
             user_id: str = "default") -> dict:
        rows = self.m.q("SELECT m.*, c.user_id FROM messages m JOIN conversations c ON c.id=m.conv_id "
                        "WHERE m.id=? AND m.role='assistant'", (message_id,))
        if not rows or rows[0]["user_id"] != user_id:
            raise KeyError("message not found")
        msg = rows[0]
        q = self.m.q("SELECT content FROM messages WHERE conv_id=? AND role='user' AND created_at<=? "
                     "ORDER BY created_at DESC, rowid DESC LIMIT 1", (msg["conv_id"], msg["created_at"]))
        question = q[0]["content"] if q else ""
        self.m.x("UPDATE messages SET rating=? WHERE id=?", (1 if good else -1, message_id))
        learned = False
        if correction:
            correction = correction.strip()
            self.m.add_fact(question, correction, "human", 0.95, "offline", CHAT_CATEGORY,
                            user_id=user_id, verified=True)
            if user_id == "default":  # corrections become regression tests for the shared memory
                self.m.add_example(CHAT_CATEGORY, question, correction, "human", True)
            self.m.record_error(CHAT_CATEGORY, f"wrong:{msg['source']}", cause=question[:120], fix=correction[:200])
            learned = True
        elif good and len(msg["content"]) <= 400 and question:
            # a short answer the user confirmed becomes a verified fact MOSAIC can reuse offline
            self.m.add_fact(question, msg["content"], "human", 0.9, "offline", CHAT_CATEGORY,
                            user_id=user_id, verified=True)
            learned = True
        self.m.log(user_id, "chat_feedback", message_id, {"good": good, "corrected": bool(correction)})
        return {"learned": learned}

    def stats(self, user_id: str = "default") -> dict:
        rows = self.m.q("SELECT m.source, m.rating FROM messages m JOIN conversations c ON c.id=m.conv_id "
                        "WHERE c.user_id=? AND m.role='assistant'", (user_id,))
        total = len(rows)
        own = sum(1 for r in rows if (r["source"] or "").startswith("mosaic"))
        rated = [r["rating"] for r in rows if r["rating"] is not None]
        return {"assistant_messages": total, "answered_by_mosaic": own,
                "independence": round(own / total, 3) if total else None,
                "rated": len(rated), "positive": sum(1 for r in rated if r > 0),
                "learned_facts": self.m.q("SELECT COUNT(*) c FROM semantic WHERE category=? AND user_id=? "
                                          "AND verified=1 AND archived=0", (CHAT_CATEGORY, user_id))[0]["c"],
                "saved_notes": len(self.preferences(user_id))}
