import json
import sqlite3
import threading
import unittest
import urllib.error
import urllib.request

from mosaic import demo
from mosaic.core import Mosaic
from mosaic.dsl import Toolbox, synthesize
from mosaic.models import SimulatedTeacher
from mosaic.server import ThreadingHTTPServer, make_handler


class TestSynthesis(unittest.TestCase):
    def test_finds_pipelines_wraps_and_linear(self):
        tb = Toolbox()
        prog, s = synthesize(tb, [("Ali@Gmail.com", "gmail.com"), ("x@Mosaic.AI", "mosaic.ai"), ("q@a.b", "a.b")])
        self.assertEqual(s, 1.0)
        self.assertEqual(tb.run(prog, "Z@Proton.ME"), "proton.me")
        prog, s = synthesize(tb, [("سارة", "مرحبا يا سارة!"), ("عمر", "مرحبا يا عمر!")])
        self.assertEqual(tb.run(prog, "نور"), "مرحبا يا نور!")
        prog, s = synthesize(tb, [("0", "32"), ("100", "212"), ("-40", "-40"), ("37", "98.6")])
        self.assertEqual(tb.run(prog, "10"), "50")

    def test_tolerates_noisy_teacher_labels(self):
        tb = Toolbox()
        ex = [(f"a{i} b{i}", f"b{i} a{i}") for i in range(10)]
        ex[3] = ("a3 b3", "garbage ?")
        prog, s = synthesize(tb, ex)
        self.assertGreaterEqual(s, 0.9)
        self.assertEqual(tb.run(prog, "x y"), "y x")


class TestLearningAndGraduation(unittest.TestCase):
    def test_learns_proves_and_stops_needing_teacher(self):
        mo = demo.run(rounds=12, verbose=False)
        cats = {c["name"]: mo.category_stats(c["name"]) for c in mo.m.categories()}
        for name, st in cats.items():
            self.assertTrue(st["graduated"], name)
            self.assertGreaterEqual(st["heldout_acc"], 0.95, name)
        before = sum(c["teacher_calls"] for c in mo.m.categories())
        rng = demo.random.Random(99)
        demo.user_traffic(mo, rng)
        mo.learn_cycle()
        after = sum(c["teacher_calls"] for c in mo.m.categories())
        self.assertEqual(before, after, "graduated categories must not call the teacher")
        accepted = mo.m.q("SELECT COUNT(*) c FROM improvements WHERE decision='accepted'")[0]["c"]
        self.assertGreaterEqual(accepted, 5)

    def test_heldout_never_reaches_teacher(self):
        mo, _ = demo.build()
        ho = mo.heldout("email_domain")[0]
        with self.assertRaises(PermissionError):
            mo.m.add_example("email_domain", ho["input"], "x", "teacher", False)
        ans, t = mo.hub.ask("email_domain", ho["input"])
        self.assertIsNone(ans)
        self.assertIsNone(t)

    def test_offline_makes_no_teacher_calls(self):
        mo, rng = demo.build()
        mo.m.set_setting("online", False)
        r = mo.solve("email_domain", "a@b.com")
        self.assertEqual(r["solver"], "none")
        mo.learn_cycle()
        self.assertEqual(sum(c["teacher_calls"] for c in mo.m.categories()), 0)
        online_facts = mo.m.q("SELECT COUNT(*) c FROM semantic WHERE acquired_mode='online'")[0]["c"]
        self.assertEqual(online_facts, 0)

    def test_training_blocked_when_license_unknown(self):
        t = SimulatedTeacher(demo.ORACLES)
        t.allows_training_on_outputs = "unknown"
        mo = Mosaic(teachers=[t])
        mo.add_category("email_domain", "transform", "domain")
        r = mo.solve("email_domain", "a@B.com")
        self.assertTrue(r["solver"].startswith("teacher"))
        self.assertEqual(mo.m.examples("email_domain"), [])

    def test_approval_and_rollback(self):
        mo, rng = demo.build()
        mo.m.set_setting("require_approval_all", True)
        demo.user_traffic(mo, rng)
        mo.learn_cycle()
        pending = mo.m.q("SELECT * FROM improvements WHERE decision='pending_approval' AND category='greeting'")
        self.assertTrue(pending)
        self.assertIsNone(mo.skills.active("greeting"))
        mo.decide(pending[0]["id"], True)
        self.assertIsNotNone(mo.skills.active("greeting"))
        mo.rollback("greeting")
        self.assertIsNone(mo.skills.active("greeting"))


class TestFeedbackAndMemory(unittest.TestCase):
    def setUp(self):
        self.mo = Mosaic()
        self.mo.add_category("caps", "qa", "capital")

    def test_feedback_kinds(self):
        mo = self.mo
        ep = mo.solve("caps", "عاصمة مصر؟")["episode_id"]
        mo.feedback(ep, "correction", "القاهرة")
        self.assertEqual(mo.solve("caps", "ما عاصمة مصر")["answer"], "القاهرة")
        ep2 = mo.solve("caps", "أجمل مدينة؟")["episode_id"]
        mo.feedback(ep2, "opinion", note="دمشق")
        self.assertIsNone(mo.solve("caps", "أجمل مدينة؟")["answer"], "an opinion must not become a fact")
        ep3 = mo.solve("caps", "عاصمة المريخ؟")["episode_id"]
        mo.feedback(ep3, "unreliable", "أولمبوس")
        self.assertEqual(len(mo.m.q("SELECT * FROM verify_queue")), 1)
        self.assertIsNone(mo.solve("caps", "عاصمة المريخ؟")["answer"])

    def test_users_are_isolated(self):
        mo = self.mo
        ep = mo.solve("caps", "عاصمة قطر؟", user_id="u2")["episode_id"]
        mo.feedback(ep, "correction", "الدوحة")
        self.assertEqual(mo.m.examples("caps"), [])
        self.assertIsNone(mo.solve("caps", "عاصمة قطر؟")["answer"])
        self.assertEqual(mo.solve("caps", "عاصمة قطر؟", user_id="u2")["answer"], "الدوحة")

    def test_conflicts_are_flagged_not_answered(self):
        m = self.mo.m
        m.add_fact("عاصمة س", "أ", "model:x", 0.8, "online", "caps")
        m.add_fact("عاصمة س", "ب", "model:y", 0.8, "online", "caps")
        self.assertIsNone(m.recall("عاصمة س", category="caps"))
        m.add_fact("عاصمة س", "ب", "human", 0.95, "offline", "caps", verified=True)
        self.assertEqual(m.recall("عاصمة س", category="caps")["answer"], "ب")

    def test_changelog_append_only(self):
        self.mo.m.log("t", "x", "y", {})
        with self.assertRaises(sqlite3.DatabaseError):
            self.mo.m.x("DELETE FROM changelog")

    def test_preferences_need_consent(self):
        with self.assertRaises(PermissionError):
            self.mo.m.set_preference("k", "v", consent_scope="")


class TestScheduler(unittest.TestCase):
    def test_low_battery_defers_and_job_controls(self):
        mo, _ = demo.build()
        mo.scheduler.update_device(battery_pct=10, charging=False)
        self.assertTrue(mo.learn_cycle()["skipped"])
        j = mo.scheduler.enqueue("learn", "greeting", 1.0)
        mo.scheduler.pause(j)
        self.assertEqual(len(mo.scheduler.next_jobs()), 0)
        mo.scheduler.resume(j)
        self.assertEqual(len(mo.scheduler.next_jobs()), 1)
        mo.scheduler.cancel(j)
        self.assertEqual(len(mo.scheduler.next_jobs()), 0)


class TestServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mo, rng = demo.build()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.mo))
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def post(self, path, body, ctype="application/json"):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), headers={"Content-Type": ctype})
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())

    def test_endpoints(self):
        with urllib.request.urlopen(self.base + "/") as r:
            self.assertIn("MOSAIC", r.read().decode())
        r = self.post("/api/task", {"category": "greeting", "input": "زينب"})
        self.assertEqual(r["answer"], "مرحبا يا زينب!")
        self.post("/api/learn", {})
        with urllib.request.urlopen(self.base + "/api/dashboard") as r:
            d = json.loads(r.read())
        self.assertIn("categories", d)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post("/api/task", {"category": "greeting", "input": "x"}, ctype="text/plain")
        self.assertEqual(cm.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
