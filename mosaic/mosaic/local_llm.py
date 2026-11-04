"""On-device language models: download, verify, run, and connect to chat.

MOSAIC runs open-weight models locally with llama.cpp's `llama-server`
(OpenAI-compatible API on 127.0.0.1), so conversation works with no cloud
and no internet once a model has been downloaded.

These are small open models (Qwen3, Apache-2.0). They are far less capable
than frontier models such as Claude; MOSAIC's memory and corrections sit on
top of whichever model is active.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

PORT = int(os.environ.get("MOSAIC_LLAMA_PORT", "8081"))
HF = "https://huggingface.co"

# Exact files and sizes as published on Hugging Face (verified when the catalog was written).
CATALOG = [
    {"id": "qwen3-0.6b", "name": "Qwen3 0.6B", "params": "0.6 مليار", "repo": "Qwen/Qwen3-0.6B-GGUF",
     "file": "Qwen3-0.6B-Q8_0.gguf", "size": 639446688, "min_ram_gb": 3,
     "note": "سريع جدًا وبسيط. للأسئلة القصيرة والجوالات المتواضعة."},
    {"id": "qwen3-1.7b", "name": "Qwen3 1.7B", "params": "1.7 مليار", "repo": "Qwen/Qwen3-1.7B-GGUF",
     "file": "Qwen3-1.7B-Q8_0.gguf", "size": 1834426016, "min_ram_gb": 4,
     "note": "توازن جيد بين السرعة والجودة."},
    {"id": "qwen3-4b", "name": "Qwen3 4B", "params": "4 مليارات", "repo": "Qwen/Qwen3-4B-GGUF",
     "file": "Qwen3-4B-Q4_K_M.gguf", "size": 2497280256, "min_ram_gb": 6,
     "note": "أذكى بوضوح. يحتاج جوالًا حديثًا."},
    {"id": "qwen3-8b", "name": "Qwen3 8B", "params": "8 مليارات", "repo": "Qwen/Qwen3-8B-GGUF",
     "file": "Qwen3-8B-Q4_K_M.gguf", "size": 5027783488, "min_ram_gb": 10,
     "note": "قوي ومناسب لجوال بذاكرة 12 إلى 16 جيجابايت. أبطأ من الأصغر."},
    {"id": "qwen3-14b", "name": "Qwen3 14B", "params": "14 مليارًا", "repo": "Qwen/Qwen3-14B-GGUF",
     "file": "Qwen3-14B-Q4_K_M.gguf", "size": 9001752960, "min_ram_gb": 16,
     "note": "الأذكى هنا، وتجريبي على الجوال: بطيء جدًا وقد يغلقه النظام إذا امتلأت الذاكرة."},
]
BY_ID = {m["id"]: m for m in CATALOG}


def device_ram_gb() -> float | None:
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) / 1024 / 1024, 1)
    except OSError:
        pass
    return None


def _cpu_has_dotprod() -> bool:
    try:
        with open("/proc/cpuinfo") as f:
            return "asimddp" in f.read()
    except OSError:
        return False


def find_server_binary() -> str | None:
    """llama-server: bundled in the Android app, or installed on a computer."""
    env = os.environ.get("MOSAIC_LLAMA_SERVER")
    if env and os.path.exists(env):
        return env
    lib = os.environ.get("MOSAIC_NATIVE_LIB_DIR")
    if lib:
        # two builds ship in the APK: one for CPUs with int8 dot-product instructions, one generic
        variants = ["libllamaserver_dotprod.so", "libllamaserver.so"] if _cpu_has_dotprod() \
            else ["libllamaserver.so"]
        for name in variants:
            p = os.path.join(lib, name)
            if os.path.exists(p):
                return p
    return shutil.which("llama-server")


class LocalModels:
    def __init__(self, memory, models_dir: str | None = None):
        self.m = memory
        self.dir = Path(models_dir or os.environ.get("MOSAIC_MODELS_DIR")
                        or Path.home() / ".mosaic" / "models")
        self.dir.mkdir(parents=True, exist_ok=True)
        self.downloads: dict[str, dict] = {}
        self.proc: subprocess.Popen | None = None
        self.active: str | None = None
        self.state = "stopped"     # stopped | starting | running | error
        self.error = ""
        self._lock = threading.Lock()

    # ------------------------------------------------------------ catalog & status
    def path(self, mid: str) -> Path:
        return self.dir / BY_ID[mid]["file"]

    def installed(self, mid: str) -> bool:
        p = self.path(mid)
        return p.exists() and p.stat().st_size == BY_ID[mid]["size"]

    def status(self) -> dict:
        ram = device_ram_gb()
        models = []
        for m in CATALOG:
            d = self.downloads.get(m["id"])
            models.append({**{k: m[k] for k in ("id", "name", "params", "size", "min_ram_gb", "note")},
                           "installed": self.installed(m["id"]),
                           "fits": ram is None or ram >= m["min_ram_gb"],
                           "download": dict(d) if d else None,
                           "active": self.active == m["id"] and self.state == "running"})
        try:
            free = shutil.disk_usage(self.dir).free
        except OSError:
            free = None
        return {"models": models, "ram_gb": ram, "free_bytes": free, "engine": bool(find_server_binary()),
                "state": self.state, "active": self.active, "error": self.error}

    # ------------------------------------------------------------ download
    def download(self, mid: str) -> None:
        if mid not in BY_ID:
            raise KeyError(mid)
        if self.installed(mid):
            return
        with self._lock:
            d = self.downloads.get(mid)
            if d and d["state"] in ("downloading", "verifying"):
                return
            need = BY_ID[mid]["size"] - self._partial(mid)
            free = shutil.disk_usage(self.dir).free
            if free < need + 200 * 1024 * 1024:
                raise ValueError(f"المساحة غير كافية: يلزم {need / 1e9:.1f} جيجابايت")
            self.downloads[mid] = {"state": "downloading", "done": self._partial(mid),
                                   "total": BY_ID[mid]["size"], "error": "", "cancel": False}
        threading.Thread(target=self._download, args=(mid,), daemon=True, name=f"dl-{mid}").start()

    def cancel(self, mid: str) -> None:
        d = self.downloads.get(mid)
        if d:
            d["cancel"] = True

    def _partial(self, mid: str) -> int:
        p = self.path(mid).with_suffix(".part")
        return p.stat().st_size if p.exists() else 0

    def _expected_sha256(self, m: dict) -> str | None:
        try:
            req = urllib.request.Request(f"{HF}/api/models/{m['repo']}/tree/main",
                                         headers={"User-Agent": "MOSAIC/0.5"})
            with urllib.request.urlopen(req, timeout=30) as r:
                for entry in json.loads(r.read()):
                    if entry.get("path") == m["file"]:
                        return (entry.get("lfs") or {}).get("oid")
        except (urllib.error.URLError, ValueError, TimeoutError):
            return None
        return None

    def _download(self, mid: str) -> None:
        m, d = BY_ID[mid], self.downloads[mid]
        part = self.path(mid).with_suffix(".part")
        try:
            expected = self._expected_sha256(m)
            while d["done"] < m["size"]:
                req = urllib.request.Request(f"{HF}/{m['repo']}/resolve/main/{m['file']}",
                                             headers={"User-Agent": "MOSAIC/0.5", "Range": f"bytes={d['done']}-"})
                with urllib.request.urlopen(req, timeout=60) as r, open(part, "ab") as f:
                    if d["done"] and r.status != 206:   # server ignored Range: start over
                        f.truncate(0)
                        d["done"] = 0
                    while True:
                        if d["cancel"]:
                            d["state"] = "cancelled"
                            return
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        d["done"] += len(chunk)
            if part.stat().st_size != m["size"]:
                raise ValueError("حجم الملف لا يطابق المنشور")
            if expected:
                d["state"] = "verifying"
                h = hashlib.sha256()
                with open(part, "rb") as f:
                    for block in iter(lambda: f.read(1 << 22), b""):
                        h.update(block)
                if h.hexdigest() != expected:
                    part.unlink()
                    raise ValueError("بصمة الملف لا تطابق المنشور على Hugging Face، حُذف الملف")
            part.rename(self.path(mid))
            d["state"] = "done"
            self.m.log("local_models", "model_downloaded", mid, {"verified_sha256": bool(expected)})
        except Exception as e:  # network drop: keep the .part file so it resumes next time
            d["state"], d["error"] = "error", str(e)

    def delete(self, mid: str) -> None:
        if self.active == mid:
            self.stop()
        for p in (self.path(mid), self.path(mid).with_suffix(".part")):
            if p.exists():
                p.unlink()
        self.downloads.pop(mid, None)
        if self.m.setting("local_model") == mid:
            self.m.set_setting("local_model", "")

    # ------------------------------------------------------------ run
    def activate(self, mid: str, wait: bool = True) -> dict:
        if not self.installed(mid):
            raise ValueError("النموذج غير منزّل بعد")
        binary = find_server_binary()
        if not binary:
            raise ValueError("محرك llama-server غير موجود على هذا الجهاز")
        self.stop()
        threads = max(1, min(os.cpu_count() or 4, 6) - 1)
        cmd = [binary, "-m", str(self.path(mid)), "--host", "127.0.0.1", "--port", str(PORT),
               "-c", "4096", "-t", str(threads), "--jinja", "--no-webui",
               # Qwen3's official non-thinking mode: answers directly instead of a long hidden reasoning pass
               "--chat-template-kwargs", '{"enable_thinking":false}']
        log = open(self.dir / "llama-server.log", "w")
        self.proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        self.active, self.state, self.error = mid, "starting", ""
        self.m.set_setting("local_model", mid, actor="local_models")
        self.m.set_setting("llm_backend", "openai_compat", actor="local_models")
        self.m.set_setting("llm_base_url", f"http://127.0.0.1:{PORT}/v1", actor="local_models")
        self.m.set_setting("llm_model", mid, actor="local_models")
        if wait:
            self._wait_ready()
        else:
            threading.Thread(target=self._wait_ready, daemon=True).start()
        return self.status()

    def _wait_ready(self, timeout: float = 300) -> None:
        end = time.time() + timeout
        while time.time() < end:
            if self.proc is None or self.proc.poll() is not None:
                self.state = "error"
                self.error = self._log_tail() or "توقف المحرك"
                return
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2) as r:
                    if r.status == 200:
                        self.state = "running"
                        return
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
                pass
            time.sleep(0.5)
        self.state, self.error = "error", "انتهت مهلة تحميل النموذج"

    def _log_tail(self) -> str:
        p = self.dir / "llama-server.log"
        try:
            return p.read_text(errors="replace")[-1500:]
        except OSError:
            return ""

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc, self.state = None, "stopped"

    def autostart(self) -> None:
        mid = self.m.setting("local_model")
        if mid in BY_ID and self.installed(mid) and find_server_binary():
            try:
                self.activate(mid, wait=False)
            except (ValueError, OSError) as e:
                self.state, self.error = "error", str(e)


def machine_note() -> str:
    return f"{platform.system()} {platform.machine()}"
