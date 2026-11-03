"""Hybrid chat provider router.

Secrets are read only from process environment; never ship API keys in APKs.
Local endpoints use an OpenAI-compatible API (e.g. Ollama's /v1 endpoint).
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


class ProviderUnavailable(RuntimeError):
    pass


def _request(base_url: str, model: str, messages: list[dict], api_key: str = "") -> str:
    url = base_url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    body = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": 0.4,
        "stream": False,
    }).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        # Local CPU inference can be substantially slower than a hosted endpoint.
        timeout = 300 if ("127.0.0.1" in url or "localhost" in url) else 90
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read(4 * 1024 * 1024)
        data = json.loads(raw.decode("utf-8"))
        answer = data["choices"][0]["message"]["content"]
        if isinstance(answer, list):
            answer = "\n".join(x.get("text", "") for x in answer if isinstance(x, dict))
        if not isinstance(answer, str) or not answer.strip():
            raise ProviderUnavailable("Provider returned an empty response")
        return answer.strip()
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, IndexError) as exc:
        raise ProviderUnavailable(f"Chat provider failed: {exc}") from exc


def chat_completion(messages: list[dict], private: bool = False) -> tuple[str, str]:
    """Prefer local for hybrid mode; private chats are local-only."""
    local_url = os.environ.get("MOSAIC_LOCAL_BASE_URL", "").strip()
    local_model = os.environ.get("MOSAIC_LOCAL_MODEL", "qwen2.5:7b")
    if local_url:
        try:
            return _request(local_url, local_model, messages), "local:" + local_model
        except ProviderUnavailable:
            if private:
                raise
    if private:
        raise ProviderUnavailable("Private conversations require MOSAIC_LOCAL_BASE_URL")
    cloud_url = os.environ.get("MOSAIC_CLOUD_BASE_URL", "").strip()
    cloud_key = os.environ.get("MOSAIC_CLOUD_API_KEY", "").strip()
    cloud_model = os.environ.get("MOSAIC_CLOUD_MODEL", "").strip()
    if cloud_url and cloud_key and cloud_model:
        return _request(cloud_url, cloud_model, messages, cloud_key), "cloud:" + cloud_model
    raise ProviderUnavailable("Configure a local OpenAI-compatible endpoint or cloud endpoint, key and model")
