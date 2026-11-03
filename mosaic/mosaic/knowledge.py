"""Persistent, provenance-aware retrieval for user-provided learning material.

This indexes extracted text as searchable knowledge. It does not execute uploads,
fine-tune model weights, or silently treat unverified material as ground truth.
"""
from __future__ import annotations

import re
import sqlite3
import hashlib
from pathlib import Path

CHUNK_CHARS = 1800
OVERLAP_CHARS = 220
MAX_CHUNKS_PER_FILE = 500


def _connect(db_path: str):
    path = Path(db_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=20)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("""
      CREATE TABLE IF NOT EXISTS knowledge_chunks(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        file_id TEXT NOT NULL,
        chunk_index INTEGER NOT NULL,
        source_name TEXT NOT NULL,
        source_sha256 TEXT NOT NULL,
        content TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(file_id, chunk_index)
      )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_file ON knowledge_chunks(file_id)")
    return con


def _chunks(text: str):
    text = re.sub(r"\r\n?", "\n", text).strip()
    if not text:
        return []
    result = []
    start = 0
    while start < len(text) and len(result) < MAX_CHUNKS_PER_FILE:
        end = min(len(text), start + CHUNK_CHARS)
        if end < len(text):
            boundary = max(text.rfind("\n", start + CHUNK_CHARS // 2, end),
                           text.rfind(". ", start + CHUNK_CHARS // 2, end),
                           text.rfind("؟", start + CHUNK_CHARS // 2, end))
            if boundary > start:
                end = boundary + 1
        part = text[start:end].strip()
        if part:
            result.append(part)
        if end >= len(text):
            break
        start = max(start + 1, end - OVERLAP_CHARS)
    return result


def ingest_file(db_path: str, file_id: str) -> dict:
    from .workspace import get_file
    item = get_file(db_path, file_id)
    if not item:
        raise KeyError("File not found")
    extracted = item.get("extracted_text", "").strip()
    if not extracted:
        return {"file_id": file_id, "indexed": 0, "status": "no_extracted_text",
                "parser": item.get("parser", "unknown")}
    chunks = _chunks(extracted)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    con = _connect(db_path)
    try:
        con.execute("DELETE FROM knowledge_chunks WHERE file_id=?", (file_id,))
        for idx, content in enumerate(chunks):
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            con.execute(
                "INSERT INTO knowledge_chunks(file_id,chunk_index,source_name,source_sha256,content,content_hash,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (file_id, idx, item["original_name"], item["sha256"], content, digest, now))
        con.commit()
    finally:
        con.close()
    return {"file_id": file_id, "source": item["original_name"], "indexed": len(chunks),
            "characters": len(extracted), "parser": item["parser"], "status": "indexed"}


def _terms(text: str):
    return [x.lower() for x in re.findall(r"[\w\u0600-\u06ff]{2,}", text, flags=re.UNICODE)]


def search_knowledge(db_path: str, query: str, limit: int = 5) -> list[dict]:
    terms = list(dict.fromkeys(_terms(query)))[:24]
    if not terms:
        return []
    con = _connect(db_path)
    try:
        rows = con.execute("SELECT id,file_id,source_name,source_sha256,content,chunk_index FROM knowledge_chunks").fetchall()
    finally:
        con.close()
    scored = []
    for row in rows:
        content = row["content"]
        low = content.lower()
        score = sum(min(low.count(term), 5) * (2.0 if len(term) > 4 else 1.0) for term in terms)
        if score:
            scored.append((score, dict(row)))
    scored.sort(key=lambda item: (-item[0], item[1]["source_name"], item[1]["chunk_index"]))
    return [{"source": r["source_name"], "file_id": r["file_id"], "sha256": r["source_sha256"],
             "chunk": r["chunk_index"], "score": score, "excerpt": r["content"][:2400]}
            for score, r in scored[:max(1, min(int(limit), 10))]]


def forget_file_knowledge(db_path: str, file_id: str) -> int:
    con = _connect(db_path)
    try:
        cur = con.execute("DELETE FROM knowledge_chunks WHERE file_id=?", (file_id,))
        con.commit()
        return cur.rowcount
    finally:
        con.close()


def knowledge_stats(db_path: str) -> dict:
    con = _connect(db_path)
    try:
        row = con.execute("SELECT COUNT(*) chunks, COUNT(DISTINCT file_id) files FROM knowledge_chunks").fetchone()
        return dict(row)
    finally:
        con.close()
