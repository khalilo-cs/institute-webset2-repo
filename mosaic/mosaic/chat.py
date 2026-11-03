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

import base64
import binascii
import json
import re
from typing import Iterable, Iterator

from .llm import LLMBackend, LLMError, backend_from_settings
from .util import new_id, now, stable_hash

CHAT_CATEGORY = "chat"
HISTORY_CHARS = 24_000
MAX_HISTORY_MESSAGES = 40
MAX_ATTACHMENTS = 5
MAX_IMAGE_B64 = 7_000_000      # ≈5 MB of image bytes
MAX_TEXT_FILE = 300_000
IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}

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

    def _add(self, conv_id: str, role: str, content: str, source: str | None = None,
             attachments: list | None = None) -> str:
        mid = new_id("msg")
        self.m.x("INSERT INTO messages(id, conv_id, role, content, source, rating, created_at, attachments) "
                 "VALUES (?,?,?,?,?,?,?,?)", (mid, conv_id, role, content, source, None, now(),
                                             json.dumps(attachments or [], ensure_ascii=False)))
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
        custom = str(self.m.setting("custom_instructions") or "").strip()
        if custom:
            parts.append("تعليمات المستخدم المخصصة (اتبعها ما لم تتعارض مع الصدق والسلامة):\n" + custom[:4000])
        prefs = self.preferences(user_id)
        if prefs:
            parts.append("ما طلب المستخدم أن تتذكره:\n" + "\n".join(f"- {p['value']}" for p in prefs[-30:]))
        facts = self.m.search(text, user_id=user_id, k=6, min_sim=0.3, min_conf=0.9)
        if facts:
            parts.append("حقائق تحقق منها المستخدم:\n" +
                         "\n".join(f"- س: {r['question']}\n  ج: {r['answer']}" for _, r in facts))
        return "\n\n".join(parts)

    @staticmethod
    def _with_text_files(content: str, attachments: list[dict]) -> str:
        files = [a for a in attachments if a["kind"] == "text"]
        if not files:
            return content
        blocks = "\n\n".join(f"📎 الملف المرفق «{a['name']}»:\n```\n{a['text']}\n```" for a in files)
        return f"{blocks}\n\n{content}"

    def _history(self, conv_id: str) -> list[dict]:
        rows = self.m.q("SELECT role, content, attachments FROM messages WHERE conv_id=? AND role IN "
                        "('user','assistant') ORDER BY created_at DESC, rowid DESC LIMIT ?",
                        (conv_id, MAX_HISTORY_MESSAGES))
        out, total, images_left = [], 0, 1  # only the newest user turn carries its images
        for r in rows:
            atts = json.loads(r["attachments"] or "[]")
            content = self._with_text_files(r["content"], atts)
            msg = {"role": r["role"], "content": content}
            imgs = [a for a in atts if a["kind"] == "image"]
            if imgs and r["role"] == "user":
                if images_left:
                    msg["images"] = [{"media_type": a["media_type"], "data": a["data"]} for a in imgs]
                    images_left -= 1
                else:
                    msg["content"] = "".join(f"[صورة مرفقة: {a['name']}]\n" for a in imgs) + content
            total += len(content)
            if total > HISTORY_CHARS and out:
                break
            out.append(msg)
        out.reverse()
        while out and out[0]["role"] != "user":
            out.pop(0)  # APIs expect the history to start with a user turn
        return out

    @staticmethod
    def _clean_attachments(attachments) -> list[dict]:
        clean = []
        for a in (attachments or [])[:MAX_ATTACHMENTS]:
            name = str(a.get("name", "file"))[:120]
            if a.get("kind") == "image":
                mt = str(a.get("media_type", ""))
                data = str(a.get("data", ""))
                if mt not in IMAGE_TYPES:
                    raise ValueError(f"نوع صورة غير مدعوم: {mt}")
                if len(data) > MAX_IMAGE_B64:
                    raise ValueError(f"الصورة {name} أكبر من 5 ميغابايت")
                try:
                    base64.b64decode(data, validate=True)
                except (binascii.Error, ValueError):
                    raise ValueError(f"بيانات الصورة {name} غير صالحة") from None
                clean.append({"kind": "image", "name": name, "media_type": mt, "data": data})
            elif a.get("kind") == "text":
                text = str(a.get("text", ""))
                if len(text) > MAX_TEXT_FILE:
                    raise ValueError(f"الملف {name} كبير جدًا")
                clean.append({"kind": "text", "name": name, "text": text})
            else:
                raise ValueError("نوع مرفق غير معروف")
        return clean

    # ------------------------------------------------------------ the main entry points
    def send(self, conv_id: str | None, text: str, user_id: str = "default",
             attachments: list | None = None) -> dict:
        result: dict = {}
        for ev in self.send_stream(conv_id, text, user_id, attachments):
            if ev["type"] == "done":
                result = ev
        return result

    def send_stream(self, conv_id: str | None, text: str, user_id: str = "default",
                    attachments: list | None = None) -> Iterator[dict]:
        text = (text or "").strip()
        atts = self._clean_attachments(attachments)
        if not text and not atts:
            raise ValueError("رسالة فارغة")
        if len(text) > 20_000:
            raise ValueError("الرسالة طويلة جدًا")
        if not conv_id:
            conv_id = self.new_conversation(user_id, title=(text or atts[0]["name"])[:60])
        else:
            self._own(conv_id, user_id)
        self._add(conv_id, "user", text, attachments=atts)
        yield {"type": "meta", "conversation_id": conv_id}
        yield from self._answer(conv_id, text, user_id, atts)

    def regenerate(self, conv_id: str, user_id: str = "default") -> Iterator[dict]:
        """Drop the last assistant reply and answer the last user message again."""
        self._own(conv_id, user_id)
        last = self.m.q("SELECT * FROM messages WHERE conv_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                        (conv_id,))
        if last and last[0]["role"] == "assistant":
            self.m.x("DELETE FROM messages WHERE id=?", (last[0]["id"],))
        u = self.m.q("SELECT * FROM messages WHERE conv_id=? AND role='user' ORDER BY created_at DESC, rowid DESC "
                     "LIMIT 1", (conv_id,))
        if not u:
            raise ValueError("لا توجد رسالة لإعادة الإجابة عليها")
        yield {"type": "meta", "conversation_id": conv_id}
        yield from self._answer(conv_id, u[0]["content"], user_id, json.loads(u[0]["attachments"] or "[]"),
                                skip_memory=True)

    def edit(self, message_id: str, new_text: str, user_id: str = "default") -> Iterator[dict]:
        """Replace a user message and discard everything after it, then answer again."""
        rows = self.m.q("SELECT m.*, c.user_id FROM messages m JOIN conversations c ON c.id=m.conv_id "
                        "WHERE m.id=? AND m.role='user'", (message_id,))
        if not rows or rows[0]["user_id"] != user_id:
            raise KeyError("message not found")
        msg = rows[0]
        new_text = (new_text or "").strip()
        if not new_text:
            raise ValueError("رسالة فارغة")
        later = self.m.q("SELECT id FROM messages WHERE conv_id=? AND (created_at>? OR (created_at=? AND rowid>"
                         "(SELECT rowid FROM messages WHERE id=?)))",
                         (msg["conv_id"], msg["created_at"], msg["created_at"], message_id))
        for r in later:
            self.m.x("DELETE FROM messages WHERE id=?", (r["id"],))
        self.m.x("UPDATE messages SET content=? WHERE id=?", (new_text, message_id))
        yield {"type": "meta", "conversation_id": msg["conv_id"]}
        yield from self._answer(msg["conv_id"], new_text, user_id, json.loads(msg["attachments"] or "[]"))

    def _answer(self, conv_id: str, text: str, user_id: str, atts: list[dict],
                skip_memory: bool = False) -> Iterator[dict]:
        m = REMEMBER.match(text) if not atts else None
        if m:
            note = m.group(1).strip()
            self.m.set_preference(f"note:{stable_hash(note)[:16]}", note, "explicit_user_command", user_id)
            yield from self._emit(conv_id, f"حسنًا، سأتذكر ذلك: «{note}»", "mosaic:memory")
            return
        if not atts and FORGET_ALL.match(text):
            n = len(self.preferences(user_id))
            self.m.x("DELETE FROM preferences WHERE user_id=? AND key LIKE 'note:%'", (user_id,))
            self.m.log(user_id, "memory_cleared", "preferences", {"count": n})
            yield from self._emit(conv_id, f"حذفت {n} ملاحظة محفوظة.", "mosaic:memory")
            return
        if not atts and not skip_memory:
            threshold = float(self.m.setting("memory_answer_threshold"))
            hits = self.m.search(text, user_id=user_id, k=1, min_sim=threshold, min_conf=0.9,
                                 category=CHAT_CATEGORY)
            if hits:
                yield from self._emit(conv_id, hits[0][1]["answer"], "mosaic:memory")
                return
        backend = self.backend()
        if backend is None:
            hint = self.m.search(text, user_id=user_id, k=1, min_sim=0.5, min_conf=0.9)
            msg = ("لا يوجد نموذج لغوي متاح الآن (غير متصل، أو لم يُضبط نموذج في الإعدادات). "
                   "أستطيع الإجابة فقط مما تعلمته منك.")
            if hint:
                msg += f"\n\nأقرب ما أعرفه: {hint[0][1]['answer']}"
            yield from self._emit(conv_id, msg, "none")
            return
        system, history = self._context(text, user_id), self._history(conv_id)
        source, parts = f"llm:{backend.id}", []
        try:
            stream = getattr(backend, "stream", None)
            if stream is None:
                chunks: Iterable[str] = [backend.chat(system, history)]
            else:
                chunks = stream(system, history)
            for chunk in chunks:
                parts.append(chunk)
                yield {"type": "delta", "text": chunk}
        except LLMError as e:
            if parts:
                parts.append(f"\n\n⚠️ انقطع الرد: {e}")
            else:
                yield from self._emit(conv_id, f"تعذّر الاتصال بالنموذج ({backend.id}): {e}", "error")
                return
        except GeneratorExit:  # the user pressed "stop": keep what was written so far
            if parts:
                self._add(conv_id, "assistant", "".join(parts) + "\n\n_(أُوقف)_", source)
            raise
        answer = "".join(parts).strip() or "(رد فارغ)"
        mid = self._add(conv_id, "assistant", answer, source)
        yield {"type": "done", "conversation_id": conv_id, "message_id": mid, "content": answer, "source": source}

    def _emit(self, conv_id: str, content: str, source: str) -> Iterator[dict]:
        mid = self._add(conv_id, "assistant", content, source)
        yield {"type": "delta", "text": content}
        yield {"type": "done", "conversation_id": conv_id, "message_id": mid, "content": content, "source": source}

    def rename(self, conv_id: str, title: str, user_id: str = "default") -> None:
        self._own(conv_id, user_id)
        title = (title or "").strip()[:120]
        if not title:
            raise ValueError("عنوان فارغ")
        self.m.x("UPDATE conversations SET title=? WHERE id=?", (title, conv_id))

    def search_conversations(self, query: str, user_id: str = "default") -> list[dict]:
        like = f"%{query.strip()}%"
        return [dict(r) for r in self.m.q(
            "SELECT DISTINCT c.* FROM conversations c LEFT JOIN messages m ON m.conv_id=c.id "
            "WHERE c.user_id=? AND (c.title LIKE ? OR m.content LIKE ?) ORDER BY c.updated_at DESC LIMIT 50",
            (user_id, like, like))]

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
