import json
import urllib.error
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


class StreamingLLM(FakeLLM):
    def stream(self, system, messages, max_tokens=4096):
        self.calls.append((system, messages))
        for w in ["أهلًا ", "بك ", "يا ", "صديقي"]:
            yield w


class TestStreamingAndEditing(unittest.TestCase):
    def setUp(self):
        self.mo = Mosaic()
        self.llm = StreamingLLM()
        self.chat = ChatEngine(self.mo, self.llm)

    def test_stream_yields_meta_deltas_done(self):
        evs = list(self.chat.send_stream(None, "مرحبا"))
        self.assertEqual(evs[0]["type"], "meta")
        self.assertEqual("".join(e["text"] for e in evs if e["type"] == "delta"), "أهلًا بك يا صديقي")
        self.assertEqual(evs[-1]["type"], "done")
        self.assertEqual(evs[-1]["content"], "أهلًا بك يا صديقي")

    def test_stop_keeps_partial_answer(self):
        gen = self.chat.send_stream(None, "مرحبا")
        cid = next(gen)["conversation_id"]
        next(gen)
        next(gen)
        gen.close()  # what the server does when the user presses stop
        msgs = self.chat.messages(cid)
        self.assertEqual(msgs[-1]["role"], "assistant")
        self.assertTrue(msgs[-1]["content"].startswith("أهلًا بك"))
        self.assertIn("أُوقف", msgs[-1]["content"])

    def test_regenerate_replaces_last_answer(self):
        cid = self.chat.send(None, "سؤال")["conversation_id"]
        list(self.chat.regenerate(cid))
        roles = [m["role"] for m in self.chat.messages(cid)]
        self.assertEqual(roles, ["user", "assistant"])
        self.assertEqual(len(self.llm.calls), 2)

    def test_edit_truncates_and_reanswers(self):
        cid = self.chat.send(None, "الأول")["conversation_id"]
        self.chat.send(cid, "الثاني")
        first_user = self.chat.messages(cid)[0]
        list(self.chat.edit(first_user["id"], "الأول بعد التعديل"))
        msgs = self.chat.messages(cid)
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant"])
        self.assertEqual(msgs[0]["content"], "الأول بعد التعديل")

    def test_attachments_reach_model_and_are_validated(self):
        png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
        self.chat.send(None, "ما في الصورة والملف؟", attachments=[
            {"kind": "image", "name": "a.png", "media_type": "image/png", "data": png},
            {"kind": "text", "name": "n.txt", "text": "محتوى الملف"}])
        _, msgs = self.llm.calls[-1]
        self.assertEqual(msgs[-1]["images"][0]["media_type"], "image/png")
        self.assertIn("محتوى الملف", msgs[-1]["content"])
        with self.assertRaises(ValueError):
            self.chat.send(None, "x", attachments=[{"kind": "image", "name": "e.svg", "media_type": "image/svg+xml", "data": png}])
        with self.assertRaises(ValueError):
            self.chat.send(None, "x", attachments=[{"kind": "image", "name": "b.png", "media_type": "image/png", "data": "@@@"}])

    def test_rename_and_search(self):
        cid = self.chat.send(None, "خطة السفر إلى تونس")["conversation_id"]
        self.chat.rename(cid, "رحلة")
        self.assertEqual(self.chat.search_conversations("تونس")[0]["id"], cid)
        self.assertEqual(self.chat.search_conversations("رحلة")[0]["title"], "رحلة")
        self.assertEqual(self.chat.search_conversations("رحلة", user_id="u2"), [])


class TestSSEEndpoint(unittest.TestCase):
    def test_stream_over_http(self):
        import urllib.request
        from mosaic.server import make_handler
        mo = Mosaic()
        srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(mo, ChatEngine(mo, StreamingLLM())))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{srv.server_address[1]}"
            req = urllib.request.Request(base + "/api/chat/stream", data=json.dumps({"text": "مرحبا"}).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as r:
                self.assertIn("text/event-stream", r.headers["Content-Type"])
                events = [json.loads(l[5:]) for l in r.read().decode().split("\n\n") if l.startswith("data:")]
            self.assertEqual(events[-1]["type"], "done")
            req = urllib.request.Request(base + "/api/chat/stream", data=json.dumps({"text": ""}).encode(),
                                         headers={"Content-Type": "application/json"})
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req)
            self.assertEqual(cm.exception.code, 400)
            for p in ("/manifest.webmanifest", "/icon.svg", "/icon-192.png", "/sw.js", "/charts.js"):
                with urllib.request.urlopen(base + p) as r:
                    self.assertEqual(r.status, 200, p)
        finally:
            srv.shutdown()


class TestCustomInstructions(unittest.TestCase):
    def test_custom_instructions_reach_system_prompt(self):
        mo = Mosaic()
        llm = FakeLLM()
        mo.m.set_setting("custom_instructions", "أجب دائمًا باختصار شديد")
        ChatEngine(mo, llm).send(None, "مرحبا")
        self.assertIn("أجب دائمًا باختصار شديد", llm.calls[-1][0])
