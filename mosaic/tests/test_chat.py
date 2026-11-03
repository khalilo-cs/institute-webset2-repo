import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mosaic.chat import ChatEngine
from mosaic.core import Mosaic
from mosaic.llm import LLMError, OpenAICompatChat


class FakeLLM:
    id = "fake:1"
    is_local = True

    def __init__(self):
        self.calls = []

    def chat(self, system, messages, max_tokens=2048):
        self.calls.append((system, messages))
        return f"رد رقم {len(self.calls)}"


class FailingLLM(FakeLLM):
    def chat(self, system, messages, max_tokens=2048):
        raise LLMError("down")


class CloudLLM(FakeLLM):
    id = "cloud:1"
    is_local = False


class TestChat(unittest.TestCase):
    def setUp(self):
        self.mo = Mosaic()
        self.llm = FakeLLM()
        self.chat = ChatEngine(self.mo, self.llm)

    def test_conversation_history_goes_to_llm(self):
        r1 = self.chat.send(None, "مرحبا")
        r2 = self.chat.send(r1["conversation_id"], "كيف حالك؟")
        self.assertEqual(r2["source"], "llm:fake:1")
        _, msgs = self.llm.calls[-1]
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant", "user"])
        self.assertEqual(msgs[-1]["content"], "كيف حالك؟")

    def test_correction_is_learned_and_answered_offline(self):
        r = self.chat.send(None, "ما اسم مشروعي؟")
        self.chat.rate(r["message_id"], good=False, correction="اسم مشروعك MOSAIC")
        self.mo.m.set_setting("online", False)
        engine = ChatEngine(self.mo, CloudLLM())  # cloud model unusable offline
        r2 = engine.send(None, "ما اسم مشروعي؟")
        self.assertEqual(r2["source"], "mosaic:memory")
        self.assertEqual(r2["content"], "اسم مشروعك MOSAIC")
        self.assertEqual(engine.stats()["learned_facts"], 1)

    def test_verified_facts_and_notes_reach_the_llm_context(self):
        self.chat.send(None, "تذكر أن اسمي خليل")
        self.assertEqual(len(self.chat.preferences()), 1)
        self.chat.send(None, "من أنا؟")
        system, _ = self.llm.calls[-1]
        self.assertIn("اسمي خليل", system)
        self.chat.send(None, "انس كل شيء")
        self.assertEqual(self.chat.preferences(), [])

    def test_cloud_forbidden_and_failures_are_honest(self):
        self.mo.m.set_setting("allow_cloud_llm", False)
        r = ChatEngine(self.mo, CloudLLM()).send(None, "سؤال")
        self.assertEqual(r["source"], "none")
        r = ChatEngine(self.mo, FailingLLM()).send(None, "سؤال")
        self.assertEqual(r["source"], "error")

    def test_users_cannot_read_each_others_conversations(self):
        r = self.chat.send(None, "سر", user_id="u1")
        with self.assertRaises(KeyError):
            self.chat.messages(r["conversation_id"], user_id="u2")
        with self.assertRaises(KeyError):
            self.chat.rate(r["message_id"], True, user_id="u2")

    def test_settings_are_validated(self):
        with self.assertRaises(ValueError):
            self.mo.m.set_setting("llm_backend", "evil")
        with self.assertRaises(ValueError):
            self.mo.m.set_setting("online", "yes")


class TestOpenAICompat(unittest.TestCase):
    def test_talks_to_a_local_openai_compatible_server(self):
        seen = {}

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                seen["path"] = self.path
                seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                out = json.dumps({"choices": [{"message": {"content": "أهلًا"}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            b = OpenAICompatChat(f"http://127.0.0.1:{srv.server_address[1]}/v1", "qwen2.5:3b", api_key="")
            self.assertTrue(b.is_local)
            self.assertEqual(b.chat("sys", [{"role": "user", "content": "hi"}]), "أهلًا")
            self.assertEqual(seen["path"], "/v1/chat/completions")
            self.assertEqual(seen["body"]["messages"][0], {"role": "system", "content": "sys"})
        finally:
            srv.shutdown()


if __name__ == "__main__":
    unittest.main()
