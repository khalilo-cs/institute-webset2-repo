import hashlib
import json
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mosaic import local_llm
from mosaic.chat import ChatEngine
from mosaic.core import Mosaic

BLOB = bytes(range(256)) * 4096  # 1 MiB stand-in for a GGUF file
REPO, FILE = "Test/Tiny-GGUF", "tiny-Q8_0.gguf"

# A stand-in for llama-server, written for these tests: /health + streaming chat completions.
FAKE_SERVER = r'''#!PYTHON
import json, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
args = sys.argv[1:]
port = int(args[args.index("--port") + 1])
model = args[args.index("-m") + 1]
assert "--jinja" in args and json.loads(args[args.index("--chat-template-kwargs") + 1]) == {"enable_thinking": False}
class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    def log_message(self, *a): pass
    def do_GET(self):
        self.send_response(200 if self.path == "/health" else 404); self.end_headers(); self.wfile.write(b"{}")
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
        for w in ["جواب ", "محلي ", "من ", model.rsplit("/", 1)[-1]]:
            self.wfile.write(("data: " + json.dumps({"choices": [{"delta": {"content": w}}]}) + "\n\n").encode())
        self.wfile.write(b"data: [DONE]\n\n")
ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
'''


class FakeHub(BaseHTTPRequestHandler):
    sha = hashlib.sha256(BLOB).hexdigest()
    ranges: list = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == f"/api/models/{REPO}/tree/main":
            out = json.dumps([{"path": FILE, "lfs": {"oid": FakeHub.sha, "size": len(BLOB)}}]).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
            return
        if self.path == f"/{REPO}/resolve/main/{FILE}":
            start = int((self.headers.get("Range") or "bytes=0-")[6:].split("-")[0])
            FakeHub.ranges.append(start)
            data = BLOB[start:]
            self.send_response(206 if start else 200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_response(404)
        self.end_headers()


class TestLocalModels(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hub = ThreadingHTTPServer(("127.0.0.1", 0), FakeHub)
        threading.Thread(target=cls.hub.serve_forever, daemon=True).start()
        cls.old = (local_llm.HF, dict(local_llm.BY_ID), local_llm.PORT)
        local_llm.HF = f"http://127.0.0.1:{cls.hub.server_address[1]}"
        local_llm.BY_ID["tiny"] = {"id": "tiny", "name": "Tiny", "params": "1M", "repo": REPO, "file": FILE,
                                   "size": len(BLOB), "min_ram_gb": 1, "note": "test"}
        local_llm.CATALOG.append(local_llm.BY_ID["tiny"])
        local_llm.PORT = 18000 + os.getpid() % 1000

    @classmethod
    def tearDownClass(cls):
        cls.hub.shutdown()
        local_llm.HF, by_id, local_llm.PORT = cls.old
        local_llm.CATALOG.remove(local_llm.BY_ID["tiny"])
        local_llm.BY_ID.clear()
        local_llm.BY_ID.update(by_id)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mo = Mosaic()
        self.lm = local_llm.LocalModels(self.mo.m, models_dir=self.tmp.name)
        FakeHub.sha = hashlib.sha256(BLOB).hexdigest()
        FakeHub.ranges = []

    def tearDown(self):
        self.lm.stop()
        self.tmp.cleanup()

    def wait_download(self):
        for _ in range(200):
            d = self.lm.downloads["tiny"]
            if d["state"] not in ("downloading", "verifying"):
                return d
            time.sleep(0.02)
        self.fail("download did not finish")

    def test_download_verifies_checksum(self):
        self.lm.download("tiny")
        self.assertEqual(self.wait_download()["state"], "done")
        self.assertTrue(self.lm.installed("tiny"))

    def test_corrupt_download_is_rejected_and_deleted(self):
        FakeHub.sha = "0" * 64
        self.lm.download("tiny")
        d = self.wait_download()
        self.assertEqual(d["state"], "error")
        self.assertFalse(self.lm.installed("tiny"))
        self.assertFalse(self.lm.path("tiny").with_suffix(".part").exists())

    def test_resume_uses_range(self):
        part = self.lm.path("tiny").with_suffix(".part")
        part.write_bytes(BLOB[:300_000])
        self.lm.download("tiny")
        self.assertEqual(self.wait_download()["state"], "done")
        self.assertEqual(FakeHub.ranges, [300_000])
        self.assertEqual(self.lm.path("tiny").read_bytes(), BLOB)

    def test_activate_runs_engine_and_chat_uses_it_offline(self):
        self.lm.download("tiny")
        self.wait_download()
        script = os.path.join(self.tmp.name, "fake-llama-server")
        with open(script, "w") as f:
            f.write(FAKE_SERVER.replace("PYTHON", sys.executable))
        os.chmod(script, os.stat(script).st_mode | stat.S_IEXEC)
        os.environ["MOSAIC_LLAMA_SERVER"] = script
        try:
            st = self.lm.activate("tiny", wait=True)
            self.assertEqual(st["state"], "running", st["error"])
            self.assertEqual(self.mo.m.setting("local_model"), "tiny")
            self.mo.m.set_setting("online", False)  # local model keeps working offline
            r = ChatEngine(self.mo).send(None, "مرحبا")
            self.assertTrue(r["source"].startswith("llm:openai_compat:tiny"), r)
            self.assertEqual(r["content"], f"جواب محلي من {FILE}")
            self.lm.delete("tiny")
            self.assertEqual(self.lm.state, "stopped")
            self.assertFalse(self.lm.installed("tiny"))
            self.assertEqual(self.mo.m.setting("local_model"), "")
        finally:
            os.environ.pop("MOSAIC_LLAMA_SERVER", None)

    def test_catalog_matches_published_files(self):
        sizes = {m["id"]: m["size"] for m in local_llm.CATALOG if m["id"] != "tiny"}
        self.assertEqual(sizes["qwen3-8b"], 5027783488)
        self.assertTrue(all(m["file"].endswith(".gguf") for m in local_llm.CATALOG))


if __name__ == "__main__":
    unittest.main()
