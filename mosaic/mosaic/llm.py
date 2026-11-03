"""Large-language-model backends behind one interface.

MOSAIC itself is not a frontier model. For open-ended conversation it uses a
pluggable LLM backend and adds its own layer on top: memory, learned skills,
corrections, privacy rules and measurable learning.

Backends:
  * AnthropicChat      — Claude through the Messages API (cloud).
  * OpenAICompatChat   — any OpenAI-compatible server: Ollama, llama.cpp,
                          LM Studio, vLLM (local or remote), or a cloud provider.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Iterator, Protocol


class LLMError(RuntimeError):
    pass


class LLMBackend(Protocol):
    id: str
    is_local: bool

    def chat(self, system: str, messages: list[dict], max_tokens: int = 2048) -> str: ...

    def stream(self, system: str, messages: list[dict], max_tokens: int = 4096) -> Iterator[str]: ...


def _post(url: str, body: dict, headers: dict, timeout: int = 120) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"content-type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read()[:300].decode("utf-8", "replace")
        raise LLMError(f"HTTP {e.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise LLMError(str(e)) from None


def _stream_lines(url: str, body: dict, headers: dict, timeout: int = 300) -> Iterator[str]:
    """POST and yield the `data:` payloads of a server-sent-events response."""
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"content-type": "application/json", **headers})
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        detail = e.read()[:300].decode("utf-8", "replace")
        raise LLMError(f"HTTP {e.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise LLMError(str(e)) from None
    with resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("data:"):
                yield line[5:].strip()


class AnthropicChat:
    is_local = False

    def __init__(self, model: str = "claude-sonnet-5-5", api_key: str | None = None):
        self.model = model
        self.id = f"anthropic:{model}"
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")

    def chat(self, system: str, messages: list[dict], max_tokens: int = 2048) -> str:
        if not self.api_key:
            raise LLMError("ANTHROPIC_API_KEY غير مضبوط")
        data = _post("https://api.anthropic.com/v1/messages",
                     {"model": self.model, "max_tokens": max_tokens, "system": system,
                      "messages": [_anthropic_message(m) for m in messages]},
                     {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"})
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
        if not text:
            raise LLMError("رد فارغ من النموذج")
        return text

    def stream(self, system: str, messages: list[dict], max_tokens: int = 4096) -> Iterator[str]:
        if not self.api_key:
            raise LLMError("ANTHROPIC_API_KEY غير مضبوط")
        body = {"model": self.model, "max_tokens": max_tokens, "system": system,
                "messages": [_anthropic_message(m) for m in messages], "stream": True}
        for data in _stream_lines("https://api.anthropic.com/v1/messages", body,
                                  {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}):
            try:
                ev = json.loads(data)
            except ValueError:
                continue
            if ev.get("type") == "content_block_delta" and ev.get("delta", {}).get("type") == "text_delta":
                yield ev["delta"]["text"]
            elif ev.get("type") == "error":
                raise LLMError(ev.get("error", {}).get("message", "stream error"))


class OpenAICompatChat:
    def __init__(self, base_url: str = "http://127.0.0.1:11434/v1", model: str = "qwen2.5:3b",
                 api_key: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.id = f"openai_compat:{model}"
        self.api_key = api_key if api_key is not None else os.environ.get("MOSAIC_LLM_API_KEY", "")
        host = urllib.parse.urlparse(self.base_url).hostname or ""
        self.is_local = host in ("127.0.0.1", "localhost", "::1")

    def chat(self, system: str, messages: list[dict], max_tokens: int = 2048) -> str:
        headers = {"authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        data = _post(f"{self.base_url}/chat/completions",
                     {"model": self.model, "max_tokens": max_tokens,
                      "messages": [{"role": "system", "content": system},
                                   *[_openai_message(m) for m in messages]]}, headers)
        try:
            text = data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, AttributeError):
            raise LLMError("صيغة رد غير متوقعة") from None
        if not text:
            raise LLMError("رد فارغ من النموذج")
        return text

    def stream(self, system: str, messages: list[dict], max_tokens: int = 4096) -> Iterator[str]:
        headers = {"authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        body = {"model": self.model, "max_tokens": max_tokens, "stream": True,
                "messages": [{"role": "system", "content": system}, *[_openai_message(m) for m in messages]]}
        for data in _stream_lines(f"{self.base_url}/chat/completions", body, headers):
            if data == "[DONE]":
                break
            try:
                delta = json.loads(data)["choices"][0].get("delta", {}).get("content")
            except (ValueError, KeyError, IndexError):
                continue
            if delta:
                yield delta


def _anthropic_message(m: dict) -> dict:
    """Internal message → Anthropic format (images become base64 image blocks)."""
    images = m.get("images") or []
    if not images:
        return {"role": m["role"], "content": m["content"]}
    blocks = [{"type": "image", "source": {"type": "base64", "media_type": i["media_type"], "data": i["data"]}}
              for i in images]
    return {"role": m["role"], "content": [*blocks, {"type": "text", "text": m["content"]}]}


def _openai_message(m: dict) -> dict:
    images = m.get("images") or []
    if not images:
        return {"role": m["role"], "content": m["content"]}
    parts = [{"type": "image_url", "image_url": {"url": f"data:{i['media_type']};base64,{i['data']}"}}
             for i in images]
    return {"role": m["role"], "content": [*parts, {"type": "text", "text": m["content"]}]}


def backend_from_settings(memory) -> LLMBackend | None:
    kind = memory.setting("llm_backend")
    model = memory.setting("llm_model")
    if kind == "anthropic":
        return AnthropicChat(model or "claude-sonnet-5-5")
    if kind == "openai_compat":
        return OpenAICompatChat(memory.setting("llm_base_url") or "http://127.0.0.1:11434/v1",
                                model or "qwen2.5:3b")
    return None
