"""Persistent chat workspace and universal file intake for MOSAIC.

Every extension is accepted as an opaque attachment. Text extraction is an optional,
bounded capability; uploaded content is never executed.
"""
from __future__ import annotations

import base64
import hashlib
import mimetypes
import os
import re
import sqlite3
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_EXTRACT_CHARS = 250_000
MAX_ARCHIVE_MEMBERS = 250
TEXT_EXTENSIONS = {
    ".txt", ".md", ".rst", ".csv", ".tsv", ".json", ".jsonl", ".xml", ".html", ".htm",
    ".css", ".js", ".mjs", ".cjs", ".py", ".java", ".kt", ".kts", ".c", ".h", ".cpp",
    ".hpp", ".cs", ".go", ".rs", ".php", ".rb", ".swift", ".sh", ".bash", ".zsh",
    ".ps1", ".bat", ".cmd", ".sql", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".log", ".properties", ".gradle", ".dockerfile", ".makefile", ".tex", ".env.example",
}
TEXT_MIMES = {"text/plain", "text/csv", "application/json", "application/xml", "text/xml"}
IMAGE_MIME_BY_EXTENSION = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif",
}


def _paths(db_path: str):
    db = Path(db_path).expanduser().resolve()
    db.parent.mkdir(parents=True, exist_ok=True)
    root = db.parent / (db.stem + "_workspace_files")
    root.mkdir(parents=True, exist_ok=True)
    return db, root


def _connect(db_path: str):
    db, _ = _paths(db_path)
    con = sqlite3.connect(str(db), timeout=20)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
    CREATE TABLE IF NOT EXISTS conversations(
      id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS chat_messages(
      id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
      role TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
      content TEXT NOT NULL, created_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX IF NOT EXISTS idx_chat_messages_conversation ON chat_messages(conversation_id, created_at);
    CREATE TABLE IF NOT EXISTS workspace_files(
      id TEXT PRIMARY KEY, conversation_id TEXT, original_name TEXT NOT NULL,
      stored_name TEXT NOT NULL, extension TEXT NOT NULL, mime_type TEXT NOT NULL,
      size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, parser TEXT NOT NULL,
      extracted_text TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
      FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE SET NULL
    );
    CREATE INDEX IF NOT EXISTS idx_workspace_files_conversation ON workspace_files(conversation_id, created_at);
    """)
    con.execute("PRAGMA foreign_keys=ON")
    return con


def _now():
    return datetime.now(timezone.utc).isoformat()

@contextmanager
def _db(db_path: str):
    con = _connect(db_path)
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def create_conversation(db_path: str, title: str = "محادثة جديدة") -> dict:
    cid, now = uuid.uuid4().hex, _now()
    with _db(db_path) as con:
        con.execute("INSERT INTO conversations(id,title,created_at,updated_at) VALUES(?,?,?,?)",
                    (cid, (title.strip() or "محادثة جديدة")[:120], now, now))
    return {"id": cid, "title": title.strip() or "محادثة جديدة", "created_at": now, "updated_at": now}


def list_conversations(db_path: str, include_archived: bool = False) -> list[dict]:
    with _db(db_path) as con:
        rows = con.execute(
            "SELECT c.*, (SELECT COUNT(*) FROM chat_messages m WHERE m.conversation_id=c.id) AS message_count "
            "FROM conversations c WHERE (? OR c.archived=0) ORDER BY c.updated_at DESC",
            (int(include_archived),)).fetchall()
    return [dict(r) for r in rows]


def get_conversation(db_path: str, cid: str) -> dict | None:
    with _db(db_path) as con:
        c = con.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
        if not c:
            return None
        messages = con.execute("SELECT id,role,content,created_at,metadata_json FROM chat_messages "
                               "WHERE conversation_id=? ORDER BY created_at,id", (cid,)).fetchall()
        files = con.execute("SELECT id,original_name,mime_type,size_bytes,sha256,parser,created_at "
                            "FROM workspace_files WHERE conversation_id=? ORDER BY created_at",
                            (cid,)).fetchall()
    result = dict(c)
    result["messages"] = [{**dict(m), "metadata": _json(m["metadata_json"])} for m in messages]
    result["files"] = [dict(f) for f in files]
    return result


def _json(value):
    import json
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return {}


def add_message(db_path: str, cid: str, role: str, content: str, metadata: dict | None = None) -> dict:
    import json
    if role not in ("user", "assistant", "system"):
        raise ValueError("Unsupported message role")
    content = str(content)
    if len(content) > 200_000:
        raise ValueError("Message is too large")
    mid, now = uuid.uuid4().hex, _now()
    with _db(db_path) as con:
        if not con.execute("SELECT 1 FROM conversations WHERE id=?", (cid,)).fetchone():
            raise KeyError("Conversation not found")
        con.execute("INSERT INTO chat_messages(id,conversation_id,role,content,created_at,metadata_json) "
                    "VALUES(?,?,?,?,?,?)", (mid, cid, role, content, now,
                    json.dumps(metadata or {}, ensure_ascii=False)))
        con.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, cid))
        if role == "user":
            title = content.strip().splitlines()[0][:72]
            con.execute("UPDATE conversations SET title=? WHERE id=? AND title='محادثة جديدة'",
                        (title or "محادثة جديدة", cid))
    return {"id": mid, "conversation_id": cid, "role": role, "content": content, "created_at": now}


def rename_conversation(db_path: str, cid: str, title: str) -> bool:
    with _db(db_path) as con:
        cur = con.execute("UPDATE conversations SET title=?,updated_at=? WHERE id=?",
                          (title.strip()[:120] or "محادثة جديدة", _now(), cid))
        return cur.rowcount > 0


def archive_conversation(db_path: str, cid: str, archived: bool = True) -> bool:
    with _db(db_path) as con:
        cur = con.execute("UPDATE conversations SET archived=?,updated_at=? WHERE id=?",
                          (int(archived), _now(), cid))
        return cur.rowcount > 0


def delete_conversation(db_path: str, cid: str) -> bool:
    with _db(db_path) as con:
        cur = con.execute("DELETE FROM conversations WHERE id=?", (cid,))
        return cur.rowcount > 0


def _xml_text(data: bytes) -> str:
    root = ET.fromstring(data)
    return "\n".join(t.strip() for t in root.itertext() if t and t.strip())


def _extract(data: bytes, ext: str, mime: str) -> tuple[str, str]:
    if ext in TEXT_EXTENSIONS or mime in TEXT_MIMES or mime.startswith("text/"):
        try:
            return data.decode("utf-8-sig", errors="replace")[:MAX_EXTRACT_CHARS], "utf8-text"
        except Exception:
            pass
    if ext == ".pdf":
        for module in ("pypdf", "PyPDF2"):
            try:
                lib = __import__(module)
                reader = lib.PdfReader(__import__("io").BytesIO(data))
                text = "\n".join((page.extract_text() or "") for page in reader.pages[:100])
                return text[:MAX_EXTRACT_CHARS], module
            except ImportError:
                continue
            except Exception:
                return "", "pdf-parser-error"
        return "", "pdf-parser-unavailable"
    if ext in (".docx", ".pptx", ".xlsx", ".odt", ".ods", ".odp", ".epub"):
        try:
            import io
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = zf.namelist()[:MAX_ARCHIVE_MEMBERS]
                selected = [n for n in names if (
                    n.startswith("word/") and n.endswith(".xml") or
                    n.startswith("ppt/slides/") and n.endswith(".xml") or
                    n.startswith("xl/sharedStrings") and n.endswith(".xml") or
                    n.startswith("xl/worksheets/") and n.endswith(".xml") or
                    n in ("content.xml", "OEBPS/content.opf") or n.endswith(".xhtml")
                )]
                parts = []
                total = 0
                for name in selected:
                    info = zf.getinfo(name)
                    if info.file_size > 5_000_000 or total + info.file_size > 15_000_000:
                        continue
                    total += info.file_size
                    parts.append(_xml_text(zf.read(name)))
                return "\n".join(parts)[:MAX_EXTRACT_CHARS], "office-openxml"
        except Exception:
            return "", "office-parser-error"
    if ext in (".zip", ".epub", ".jar", ".apk", ".docm", ".xlsm", ".pptm"):
        try:
            import io
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                listing = []
                for info in zf.infolist()[:MAX_ARCHIVE_MEMBERS]:
                    if info.is_dir():
                        continue
                    listing.append(f"{info.filename} ({info.file_size} bytes)")
                return "\n".join(listing)[:MAX_EXTRACT_CHARS], "archive-manifest"
        except Exception:
            return "", "archive-unreadable"
    return "", "opaque-binary"


def store_file(db_path: str, filename: str, content_base64: str, conversation_id: str | None = None) -> dict:
    import json
    filename = Path(str(filename).replace("\\", "/")).name[:240]
    if not filename or filename in (".", ".."):
        filename = "unnamed-file"
    try:
        data = base64.b64decode(content_base64, validate=True)
    except Exception as exc:
        raise ValueError("Invalid base64 file data") from exc
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"File exceeds {MAX_FILE_BYTES // (1024*1024)} MB limit")
    db, root = _paths(db_path)
    ext = Path(filename).suffix.lower()[:24]
    mime = IMAGE_MIME_BY_EXTENSION.get(ext) or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    text, parser = _extract(data, ext, mime)
    fid = uuid.uuid4().hex
    stored_name = fid + ".bin"
    target = root / stored_name
    target.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    now = _now()
    try:
        with _db(str(db)) as con:
            if conversation_id and not con.execute("SELECT 1 FROM conversations WHERE id=?",
                                                   (conversation_id,)).fetchone():
                raise KeyError("Conversation not found")
            con.execute("INSERT INTO workspace_files(id,conversation_id,original_name,stored_name,extension,"
                        "mime_type,size_bytes,sha256,parser,extracted_text,created_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (fid, conversation_id, filename, stored_name, ext, mime, len(data), digest,
                         parser, text, now))
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return {"id": fid, "conversation_id": conversation_id, "name": filename, "extension": ext,
            "mime_type": mime, "size_bytes": len(data), "sha256": digest,
            "parser": parser, "extracted_characters": len(text), "created_at": now,
            "status": "stored_and_indexed" if text else "stored_opaque"}


def list_files(db_path: str, conversation_id: str | None = None) -> list[dict]:
    with _db(db_path) as con:
        if conversation_id:
            rows = con.execute("SELECT id,conversation_id,original_name,mime_type,size_bytes,sha256,parser,"
                               "length(extracted_text) AS extracted_characters,created_at FROM workspace_files "
                               "WHERE conversation_id=? ORDER BY created_at DESC", (conversation_id,)).fetchall()
        else:
            rows = con.execute("SELECT id,conversation_id,original_name,mime_type,size_bytes,sha256,parser,"
                               "length(extracted_text) AS extracted_characters,created_at FROM workspace_files "
                               "ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def get_file(db_path: str, fid: str) -> dict | None:
    with _db(db_path) as con:
        row = con.execute("SELECT * FROM workspace_files WHERE id=?", (fid,)).fetchone()
    if not row:
        return None
    result = dict(row)
    _, root = _paths(db_path)
    path = root / result["stored_name"]
    if not path.is_file():
        return None
    result["download_path"] = str(path)
    return result


def delete_file(db_path: str, fid: str) -> bool:
    with _db(db_path) as con:
        row = con.execute("SELECT stored_name FROM workspace_files WHERE id=?", (fid,)).fetchone()
        if not row:
            return False
        con.execute("DELETE FROM workspace_files WHERE id=?", (fid,))
    _, root = _paths(db_path)
    (root / row["stored_name"]).unlink(missing_ok=True)
    return True


def search_conversations(db_path: str, query: str) -> list[dict]:
    term = "%" + query.strip() + "%"
    with _db(db_path) as con:
        rows = con.execute(
            "SELECT c.*, (SELECT COUNT(*) FROM chat_messages m WHERE m.conversation_id=c.id) AS message_count "
            "FROM conversations c WHERE c.archived=0 AND (c.title LIKE ? OR EXISTS "
            "(SELECT 1 FROM chat_messages m WHERE m.conversation_id=c.id AND m.content LIKE ?)) "
            "ORDER BY c.updated_at DESC",
            (term, term)).fetchall()
    return [dict(r) for r in rows]


def build_attachment_content(db_path: str, file_ids: list[str], max_files: int = 10):
    """Build safe model context from extracted text and bounded image data URLs.

    Images are sent only to the already-selected model provider. Non-image binary
    attachments remain stored but are never executed or blindly forwarded.
    """
    import io
    blocks = []
    image_count = 0
    image_bytes = 0
    text_parts = []
    for fid in file_ids[:max_files]:
        item = get_file(db_path, str(fid))
        if not item:
            continue
        name = item["original_name"]
        extracted = item.get("extracted_text", "")
        if extracted:
            text_parts.append(f"[ملف: {name}]\n{extracted[:40000]}")
            continue
        mime = item.get("mime_type", "")
        mime = IMAGE_MIME_BY_EXTENSION.get(item.get("extension", "").lower(), mime)
        if mime in IMAGE_MIME_BY_EXTENSION.values() and image_count < 4:
            size = int(item.get("size_bytes", 0))
            if size > 5 * 1024 * 1024 or image_bytes + size > 8 * 1024 * 1024:
                text_parts.append(f"[الصورة {name}: محفوظة لكن تجاوزت حد الإرسال المرئي]")
                continue
            data = Path(item["download_path"]).read_bytes()
            blocks.append({"type": "image_url", "image_url": {
                "url": "data:" + mime + ";base64," + base64.b64encode(data).decode("ascii")
            }})
            image_count += 1
            image_bytes += size
            blocks.append({"type": "text", "text": "الصورة المرفقة: " + name})
    if text_parts:
        blocks.insert(0, {"type": "text", "text": "محتويات الملفات المرفقة:\n" + "\n\n".join(text_parts)})
    return blocks
