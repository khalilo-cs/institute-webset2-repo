"""Local text-generation server for MOSAIC base models and PEFT adapters.

Binds to loopback by default. This is an optional runtime and is not imported by the
Android app or the normal workspace server.
"""
from __future__ import annotations

import argparse
import json
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def load_runtime(model_id: str, adapter_dir: str | None = None):
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except ImportError as exc:
        raise RuntimeError(
            "Install PyTorch and mosaic/training-requirements.txt before starting local inference."
        ) from exc

    has_cuda = torch.cuda.is_available()
    bf16 = bool(has_cuda and hasattr(torch.cuda, "is_bf16_supported")
                and torch.cuda.is_bf16_supported())
    dtype = torch.bfloat16 if bf16 else (torch.float16 if has_cuda else torch.float32)
    tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=False)
    if not getattr(tokenizer, "chat_template", None):
        raise RuntimeError("Selected model has no chat template")
    model = AutoModelForCausalLM.from_pretrained(
        model_id, torch_dtype=dtype, low_cpu_mem_usage=True,
        trust_remote_code=False, use_safetensors=True
    )
    if adapter_dir:
        try:
            from peft import PeftModel
        except ImportError as exc:
            raise RuntimeError("PEFT is required to load a MOSAIC adapter") from exc
        model = PeftModel.from_pretrained(model, adapter_dir, is_trainable=False)
    model.eval()
    return torch, tokenizer, model


def serve(model_id: str, adapter_dir: str | None, host: str, port: int,
          max_input_tokens: int = 8192):
    torch, tokenizer, model = load_runtime(model_id, adapter_dir)
    device = next(model.parameters()).device

    class Handler(BaseHTTPRequestHandler):
        server_version = "MOSAIC-LocalModel/0.1"

        def log_message(self, fmt, *args):
            pass

        def send_json(self, code: int, value: dict):
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                return self.send_json(200, {
                    "ok": True, "model": model_id, "adapter_loaded": bool(adapter_dir),
                    "device": str(device)
                })
            if self.path == "/v1/models":
                return self.send_json(200, {
                    "object": "list",
                    "data": [{"id": "mosaic-local", "object": "model", "owned_by": "local"}]
                })
            self.send_json(404, {"error": {"message": "not found"}})

        def do_POST(self):
            if self.path != "/v1/chat/completions":
                return self.send_json(404, {"error": {"message": "not found"}})
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 2 * 1024 * 1024:
                    raise ValueError("request body must be between 1 byte and 2 MiB")
                payload = json.loads(self.rfile.read(length))
                messages = payload.get("messages")
                if not isinstance(messages, list) or not messages:
                    raise ValueError("messages must be a non-empty list")
                clean_messages = []
                for message in messages[-24:]:
                    role = str(message.get("role", ""))
                    if role not in {"system", "user", "assistant"}:
                        continue
                    content = message.get("content", "")
                    if not isinstance(content, str):
                        raise ValueError("This local runtime currently supports text messages only")
                    clean_messages.append({"role": role, "content": content})
                if not clean_messages or clean_messages[-1]["role"] != "user":
                    raise ValueError("last supported message must be from user")
                prompt = tokenizer.apply_chat_template(
                    clean_messages, tokenize=False, add_generation_prompt=True
                )
                encoded = tokenizer(prompt, return_tensors="pt", add_special_tokens=False)
                if encoded["input_ids"].shape[1] > max_input_tokens:
                    raise ValueError(
                        f"prompt exceeds configured limit ({max_input_tokens} tokens); shorten the conversation"
                    )
                encoded = {key: value.to(device) for key, value in encoded.items()}
                max_new = max(1, min(int(payload.get("max_tokens", 512)), 2048))
                temperature = float(payload.get("temperature", 0.4))
                top_p = float(payload.get("top_p", 0.9))
                do_sample = temperature > 0
                generation = {
                    "max_new_tokens": max_new,
                    "pad_token_id": tokenizer.eos_token_id,
                    "do_sample": do_sample,
                }
                if do_sample:
                    generation["temperature"] = max(0.05, min(temperature, 2.0))
                    generation["top_p"] = max(0.1, min(top_p, 1.0))
                with torch.inference_mode():
                    output = model.generate(**encoded, **generation)
                new_tokens = output[0, encoded["input_ids"].shape[1]:]
                answer = tokenizer.decode(new_tokens, skip_special_tokens=True).strip()
                if not answer:
                    raise RuntimeError("model generated an empty response")
                return self.send_json(200, {
                    "id": "chatcmpl-" + uuid.uuid4().hex,
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": str(payload.get("model") or "mosaic-local"),
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": answer},
                        "finish_reason": "stop"
                    }],
                    "usage": {
                        "prompt_tokens": int(encoded["input_ids"].shape[1]),
                        "completion_tokens": int(new_tokens.shape[0]),
                        "total_tokens": int(output.shape[1])
                    }
                })
            except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                self.send_json(400, {"error": {"message": str(exc)}})
            except Exception as exc:
                self.send_json(500, {"error": {"message": f"local inference failed: {exc}"}})

    httpd = ThreadingHTTPServer((host, int(port)), Handler)
    print(f"MOSAIC local model ready at http://{host}:{port}/v1")
    print(f"Base model: {model_id}")
    print(f"Adapter: {adapter_dir or '(none)'}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Serve a MOSAIC local model and optional LoRA adapter")
    parser.add_argument("--model", default="Qwen/Qwen2.5-0.5B-Instruct")
    parser.add_argument("--adapter", default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--max-input-tokens", type=int, default=8192)
    args = parser.parse_args(argv)
    serve(args.model, args.adapter, args.host, args.port, args.max_input_tokens)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
