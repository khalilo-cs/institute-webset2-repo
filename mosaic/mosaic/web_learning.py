"""Permission-respecting website research and local knowledge store.

This module fetches public pages only, follows same-origin links, honours robots.txt,
blocks private/local network destinations, and stores extracted text with provenance.
It does not bypass logins, paywalls, CAPTCHAs, or access controls.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.robotparser
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

MAX_PAGES = 8
MAX_BYTES = 1_500_000
TIMEOUT = 12
USER_AGENT = "MOSAICResearchBot/1.0 (+user-initiated public-page indexing)"


class TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header"}
    def __init__(self):
        super().__init__()
        self.parts, self.links, self.title = [], [], []
        self.skip = 0
        self.in_title = False
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in self.SKIP: self.skip += 1
        if tag == "title": self.in_title = True
        if tag == "a" and attrs.get("href"): self.links.append(attrs["href"])
    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip: self.skip -= 1
        if tag == "title": self.in_title = False
    def handle_data(self, data):
        text = re.sub(r"\s+", " ", data).strip()
        if not text: return
        if self.in_title: self.title.append(text)
        if not self.skip: self.parts.append(text)


def _public_url(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError("يسمح فقط بروابط HTTP وHTTPS العامة.")
    if p.username or p.password:
        raise ValueError("الروابط التي تحتوي بيانات دخول غير مسموحة.")
    host = p.hostname.encode("idna").decode("ascii")
    if host.lower() in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise ValueError("لا يمكن فهرسة عناوين الشبكة المحلية.")
    try:
        addresses = socket.getaddrinfo(host, p.port or (443 if p.scheme == "https" else 80), type=socket.SOCK_STREAM)
        for item in addresses:
            ip = ipaddress.ip_address(item[4][0])
            if not ip.is_global:
                raise ValueError("الرابط يشير إلى عنوان شبكة خاص أو محلي.")
    except socket.gaierror as exc:
        raise ValueError("تعذر العثور على عنوان الموقع.") from exc
    return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path or "/", p.query, ""))


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Validate every redirect before following it, preventing redirects to local services.
        safe = _public_url(urllib.parse.urljoin(req.full_url, newurl))
        return super().redirect_request(req, fp, code, msg, headers, safe)


SAFE_OPENER = urllib.request.build_opener(SafeRedirectHandler)


def _fetch(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    with SAFE_OPENER.open(req, timeout=TIMEOUT) as response:
        ctype = response.headers.get_content_type()
        if ctype not in ("text/html", "application/xhtml+xml"):
            return None
        raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("الصفحة أكبر من الحد المسموح.")
        charset = response.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace"), response.geturl()


def _robots_allows(root: str, url: str) -> bool:
    p = urllib.parse.urlsplit(root)
    robots_url = f"{p.scheme}://{p.netloc}/robots.txt"
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(robots_url)
    try:
        req = urllib.request.Request(robots_url, headers={"User-Agent": USER_AGENT})
        with SAFE_OPENER.open(req, timeout=5) as r:
            parser.parse(r.read(256_000).decode("utf-8", errors="replace").splitlines())
        return parser.can_fetch(USER_AGENT, url)
    except Exception:
        # If robots.txt cannot be read, do not assume permission to crawl.
        return False


def _db(path: str):
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE IF NOT EXISTS web_pages (
        id INTEGER PRIMARY KEY, url TEXT UNIQUE, title TEXT, text TEXT,
        content_hash TEXT, fetched_at INTEGER, source_domain TEXT
    )""")
    conn.commit()
    return conn


def learn_website(start_url: str, db_path: str, max_pages: int = MAX_PAGES):
    start = _public_url(start_url)
    root_parts = urllib.parse.urlsplit(start)
    origin = (root_parts.scheme, root_parts.netloc)
    queue, seen, saved, skipped = [start], set(), [], []
    conn = _db(db_path)
    try:
        while queue and len(seen) < min(max_pages, MAX_PAGES):
            current = queue.pop(0)
            if current in seen: continue
            seen.add(current)
            try:
                current = _public_url(current)
                if (urllib.parse.urlsplit(current).scheme, urllib.parse.urlsplit(current).netloc) != origin:
                    continue
                if not _robots_allows(start, current):
                    skipped.append({"url": current, "reason": "robots.txt منع الوصول أو تعذر التحقق منه"})
                    continue
                fetched = _fetch(current)
                if not fetched: continue
                html, final_url = fetched
                final_url = _public_url(final_url)
                final_parts = urllib.parse.urlsplit(final_url)
                if (final_parts.scheme, final_parts.netloc) != origin:
                    skipped.append({"url": current, "reason": "تم تجاوز إعادة توجيه خارج النطاق الأصلي"})
                    continue
                parser = TextExtractor()
                parser.feed(html)
                text = re.sub(r"\n{3,}", "\n\n", "\n".join(parser.parts)).strip()
                if not text: continue
                title = " ".join(parser.title)[:300] or final_url
                digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
                conn.execute("""INSERT INTO web_pages(url,title,text,content_hash,fetched_at,source_domain)
                    VALUES(?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET
                    title=excluded.title,text=excluded.text,content_hash=excluded.content_hash,
                    fetched_at=excluded.fetched_at,source_domain=excluded.source_domain""",
                    (final_url, title, text[:200_000], digest, int(time.time()), root_parts.netloc))
                saved.append({"url": final_url, "title": title, "characters": len(text), "sha256": digest})
                for href in parser.links:
                    if len(queue) + len(seen) >= MAX_PAGES * 3: break
                    absolute = urllib.parse.urljoin(final_url, href)
                    parts = urllib.parse.urlsplit(absolute)
                    if (parts.scheme, parts.netloc) == origin and absolute not in seen:
                        queue.append(absolute)
            except Exception as exc:
                skipped.append({"url": current, "reason": str(exc)[:240]})
        conn.commit()
        return {"ok": True, "pages_saved": len(saved), "pages_skipped": len(skipped),
                "pages": saved, "skipped": skipped, "max_pages": min(max_pages, MAX_PAGES)}
    finally:
        conn.close()


def list_sources(db_path: str):
    conn = _db(db_path)
    try:
        rows = conn.execute("SELECT url,title,length(text),fetched_at,content_hash FROM web_pages ORDER BY fetched_at DESC LIMIT 100").fetchall()
        return [{"url": r[0], "title": r[1], "characters": r[2], "fetched_at": r[3], "sha256": r[4]} for r in rows]
    finally:
        conn.close()


def search_knowledge(query: str, db_path: str, limit: int = 5):
    terms = [x.lower() for x in re.findall(r"[\w\u0600-\u06ff]{3,}", query)[:12]]
    if not terms: return []
    conn = _db(db_path)
    try:
        rows = conn.execute("SELECT url,title,text,fetched_at FROM web_pages ORDER BY fetched_at DESC LIMIT 100").fetchall()
        scored = []
        for url, title, body, fetched in rows:
            low = body.lower()
            score = sum(low.count(term) for term in terms)
            if score: scored.append((score, url, title, body, fetched))
        scored.sort(reverse=True)
        return [{"score": s, "url": u, "title": t, "excerpt": body[:1200], "fetched_at": ts}
                for s,u,t,body,ts in scored[:limit]]
    finally:
        conn.close()
