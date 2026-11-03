"""MOSAIC core: task execution, Continual Learning Engine, Evolution Lab,
regression guard, teacher graduation, human feedback and the dashboard data.
"""

from __future__ import annotations

import json
import time
from typing import Iterable

from .dsl import Toolbox, describe, score, synthesize
from .memory import Memory
from .models import Teacher, TeacherHub
from .scheduler import Scheduler
from .skills import SkillRegistry, _tool_name
from .util import answers_match, diff_is_significant, new_id, now, stable_hash, wilson_interval

TEACHER_FACT_CONF = 0.6     # knowledge from another model: usable only after the lab promotes it
ACTIVE_FACT_CONF = 0.7      # minimum confidence to answer from memory
PROMOTED_FACT_CONF = 0.75
DEFAULT_QA_STRATEGY = {"strategy": "recall", "min_sim": 0.55}
QA_SIM_GRID = [0.35, 0.45, 0.55, 0.65, 0.75]
FEEDBACK_KINDS = {"correct", "correction", "opinion", "needs_verification", "unreliable"}


class Mosaic:
    def __init__(self, path: str = ":memory:", teachers: Iterable[Teacher] = ()):
        self.m = Memory(path)
        self.toolbox = Toolbox()
        self.skills = SkillRegistry(self.m, self.toolbox)
        self.hub = TeacherHub(self.m)
        self.scheduler = Scheduler(self.m)
        for t in teachers:
            self.hub.register(t)

    # ================================================================ setup
    def add_category(self, name: str, kind: str, description: str, language: str = "ar") -> None:
        self.m.add_category(name, kind, description, language)

    def add_examples(self, category: str, pairs: Iterable[tuple[str, str]], source: str = "human",
                     verified: bool = True, split: str | None = None) -> dict:
        counts = {"dev": 0, "heldout": 0}
        for inp, out in pairs:
            counts[self.m.add_example(category, inp, out, source, verified, split)] += 1
            if self.m.category(category)["kind"] == "qa" and verified and split != "heldout":
                self.m.add_fact(inp, out, source=source, confidence=0.9, acquired_mode="offline",
                                category=category, verified=True)
        return counts

    # ================================================================ solving
    def _qa_strategy(self, category: str) -> dict:
        return self.skills.active_program(category) or dict(DEFAULT_QA_STRATEGY)

    def _student_answer(self, category: str, inp: str, user_id: str = "default",
                        program: dict | None = None, min_conf: float = ACTIVE_FACT_CONF) -> str | None:
        cat = self.m.category(category)
        if cat["kind"] == "transform":
            prog = program if program is not None else self.skills.active_program(category)
            if prog is None:
                return None
            try:
                return self.toolbox.run(prog, inp)
            except Exception:
                return None
        strat = program if program is not None else self._qa_strategy(category)
        fact = self.m.recall(inp, user_id=user_id, min_sim=strat["min_sim"], min_conf=min_conf,
                             category=category)
        return fact["answer"] if fact else None

    def solve(self, category: str, inp: str, user_id: str = "default", private: bool = False) -> dict:
        cat = self.m.category(category)
        if cat is None:
            raise KeyError(f"unknown category {category}")
        answer = self._student_answer(category, inp, user_id)
        solver, cost = "mosaic", 0.0
        if answer is not None:
            row = self.skills.active(category)
            solver = f"mosaic:skill:v{row['version']}" if row else "mosaic:memory"
        else:
            t_ans, teacher = self.hub.ask(category, inp, private=private, scope="user")
            if t_ans is not None:
                answer, solver, cost = t_ans, f"teacher:{teacher.id}", teacher.cost_per_call
                if teacher.allows_training_on_outputs == "yes" and not private and user_id == "default":
                    try:
                        self.m.add_example(category, inp, t_ans, source="teacher", verified=False)
                    except PermissionError:
                        pass
                    if cat["kind"] == "qa":
                        self.m.add_fact(inp, t_ans, source=f"model:{teacher.id}", confidence=TEACHER_FACT_CONF,
                                        acquired_mode="online", category=category)
            else:
                solver = "none"
                self.m.record_error(category, "no_answer", cause="لا مهارة ولا معرفة كافية")
        self.m.x("UPDATE categories SET total_tasks=total_tasks+1 WHERE name=?", (category,))
        eid = self.m.add_episode(category, inp, answer, solver, cost=cost, user_id=user_id,
                                 language=cat["language"])
        return {"episode_id": eid, "answer": answer, "solver": solver, "cost": cost,
                "graduated": bool(cat["graduated"])}

    # ================================================================ human feedback
    def feedback(self, episode_id: str, kind: str, correct_answer: str | None = None,
                 note: str = "") -> dict:
        if kind not in FEEDBACK_KINDS:
            raise ValueError(f"kind must be one of {sorted(FEEDBACK_KINDS)}")
        ep = self.m.episode(episode_id)
        if ep is None:
            raise KeyError(episode_id)
        cat, user = ep["category"], ep["user_id"]
        kind_of = self.m.category(cat)["kind"]
        teacher_id = ep["solver"][8:] if ep["solver"].startswith("teacher:") else None
        shared = user == "default"  # other users' data never feeds the shared skills
        result = {"kind": kind, "learned": False}

        if kind == "correct" and ep["answer"] is not None:
            self.m.x("UPDATE episodes SET correct=1, expected=? WHERE id=?", (ep["answer"], episode_id))
            if teacher_id:
                self.hub.grade_teacher(teacher_id, ep["answer"], ep["answer"])
            if shared:
                self.m.add_example(cat, ep["input"], ep["answer"], "human", True)
            if kind_of == "qa":
                self.m.add_fact(ep["input"], ep["answer"], "human", 0.9, "offline", cat, user_id=user, verified=True)
            result["learned"] = True
        elif kind == "correction":
            if not correct_answer:
                raise ValueError("a correction needs correct_answer")
            self.m.x("UPDATE episodes SET correct=0, expected=? WHERE id=?", (correct_answer, episode_id))
            if teacher_id:
                self.hub.grade_teacher(teacher_id, ep["answer"], correct_answer)
            if shared:
                self.m.add_example(cat, ep["input"], correct_answer, "human", True)
            if kind_of == "qa":
                self.m.add_fact(ep["input"], correct_answer, "human", 0.95, "offline", cat,
                                user_id=user, verified=True)
            self.m.record_error(cat, f"wrong:{ep['solver']}", cause=note, fix=correct_answer)
            result["learned"] = True
        elif kind == "opinion":
            self.m.set_preference(f"opinion:{cat}", note or (correct_answer or ""),
                                  consent_scope="user_submitted_feedback", user_id=user)
        else:  # needs_verification / unreliable → quarantined, never learned automatically
            self.m.x("INSERT INTO verify_queue VALUES (?,?,?,?,?,?)",
                     (new_id("vq"), cat, ep["input"], correct_answer or ep["answer"], kind, now()))
        self.m.log(user, "feedback", episode_id, {"kind": kind})
        return result

    # ================================================================ evaluation
    def _eval(self, category: str, program: dict | None, rows, min_conf: float = ACTIVE_FACT_CONF,
              tool_override: tuple[str, dict | None] | None = None) -> tuple[int, int, list[bool]]:
        saved = None
        if tool_override:
            name, prog = tool_override
            saved = self.toolbox.tools.get(name)
            if prog is None:
                self.toolbox.tools.pop(name, None)
            else:
                self.toolbox.tools[name] = prog
        try:
            kind = self.m.category(category)["kind"]
            hits = []
            for r in rows:
                if kind == "transform" and program is None:
                    hits.append(False)
                    continue
                hits.append(answers_match(self._student_answer(category, r["input"], program=program,
                                                               min_conf=min_conf), r["expected"]))
            return sum(hits), len(hits), hits
        finally:
            if tool_override:
                name, _ = tool_override
                if saved is None:
                    self.toolbox.tools.pop(name, None)
                else:
                    self.toolbox.tools[name] = saved

    def heldout(self, category: str, part: str = "all"):
        rows = self.m.examples(category, "heldout", verified_only=True)
        if part == "tune":
            return [r for r in rows if int(r["input_hash"][-2:], 16) % 2 == 0]
        if part == "proof":
            return [r for r in rows if int(r["input_hash"][-2:], 16) % 2 == 1]
        return rows

    def _dev_pairs(self, category: str) -> list[tuple[str, str]]:
        best: dict[str, tuple[int, str, str]] = {}
        for r in self.m.examples(category, "dev"):
            rank = 1 if r["verified"] else 0
            if r["input_hash"] not in best or rank > best[r["input_hash"]][0]:
                best[r["input_hash"]] = (rank, r["input"], r["expected"])
        return [(i, o) for _, i, o in best.values()]

    # ================================================================ evolution lab
    def _record(self, type_: str, category: str, target: str, reason: str, origin: str,
                baseline: str | None, candidate: str | None, results: dict, cost: dict,
                risk: str, decision: str) -> str:
        requires = decision == "pending_approval"
        iid = new_id("imp")
        self.m.x("INSERT INTO improvements VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (iid, type_, category, target, reason, origin, baseline, candidate,
                  json.dumps(results, ensure_ascii=False), json.dumps(cost), risk, int(requires),
                  decision, None if requires else "auto", baseline, now(), None if requires else now()))
        self.m.log("evolution_lab", "improvement", iid, {"type": type_, "category": category, "decision": decision})
        return iid

    def _needs_approval(self, risk: str, cost_usd: float) -> bool:
        return (self.m.setting("require_approval_all") or risk == "high"
                or cost_usd > float(self.m.setting("approval_cost_usd")))

    def decide(self, improvement_id: str, accept: bool, actor: str = "user") -> None:
        rows = self.m.q("SELECT * FROM improvements WHERE id=?", (improvement_id,))
        if not rows:
            raise KeyError(improvement_id)
        imp = rows[0]
        if imp["decision"] != "pending_approval":
            raise ValueError("already decided")
        if accept:
            self._apply(imp["type"], imp["category"], imp["candidate_version"], json.loads(imp["results"]))
        elif imp["candidate_version"] and imp["candidate_version"].startswith("skill-"):
            self.skills.reject(imp["candidate_version"])
        self.m.x("UPDATE improvements SET decision=?, decided_by=?, decided_at=? WHERE id=?",
                 ("accepted" if accept else "rejected", actor, now(), improvement_id))
        self.m.log(actor, "approval", improvement_id, {"accepted": accept})

    def _apply(self, type_: str, category: str, candidate: str | None, results: dict) -> None:
        if candidate and candidate.startswith("skill-"):
            self.skills.activate(candidate, results.get("candidate_acc", 0.0), "evolution_lab")
            self.skills.publish_tool(category)
        for fid in results.get("promote_facts", []):
            self.m.x("UPDATE semantic SET confidence=MAX(confidence, ?), updated_at=? WHERE id=?",
                     (PROMOTED_FACT_CONF, now(), fid))

    def rollback(self, category: str) -> str | None:
        return self.skills.rollback(category)

    # ================================================================ continual learning engine
    def category_stats(self, category: str) -> dict:
        cat = self.m.category(category)
        ho = self.heldout(category)
        prog = self.skills.active_program(category) if cat["kind"] == "transform" else self._qa_strategy(category)
        ok, n, _ = self._eval(category, prog, ho)
        recent = self.m.q("SELECT solver, correct FROM episodes WHERE category=? AND archived=0 "
                          "ORDER BY created_at DESC LIMIT 50", (category,))
        teacher_share = (sum(r["solver"].startswith("teacher:") for r in recent) / len(recent)) if recent else 0.0
        unanswered = (sum(r["solver"] == "none" for r in recent) / len(recent)) if recent else 0.0
        lo, hi = wilson_interval(ok, n)
        active = self.skills.active(category)
        return {
            "name": category, "kind": cat["kind"], "language": cat["language"],
            "description": cat["description"], "graduated": bool(cat["graduated"]),
            "frozen": bool(cat["frozen"]), "heldout_acc": (ok / n) if n else None, "heldout_n": n,
            "heldout_ci": [round(lo, 3), round(hi, 3)], "teacher_share_recent": round(teacher_share, 3),
            "unanswered_recent": round(unanswered, 3), "total_tasks": cat["total_tasks"],
            "teacher_calls": cat["teacher_calls"], "dev_examples": len(self._dev_pairs(category)),
            "active_skill": ({"id": active["id"], "version": active["version"],
                              "program": describe(json.loads(active["program"])) if cat["kind"] == "transform"
                              else json.loads(active["program"])} if active else None),
            "language_level": _language_level(ok / n if n else None, n),
        }

    def learn_cycle(self) -> dict:
        """One pass of the 12-step learning loop over all categories."""
        self.hub.calls_this_cycle = 0
        ok, why = self.scheduler.can_run_heavy()
        if not ok:
            self.m.log("scheduler", "cycle_skipped", "device", {"reason": why})
            return {"skipped": why, "results": []}
        for cat in self.m.categories():
            if cat["frozen"]:
                continue
            st = self.category_stats(cat["name"])
            failure = 1 - st["heldout_acc"] if st["heldout_acc"] is not None else 1.0
            dependency = st["teacher_share_recent"]
            gain = max(failure, dependency, st["unanswered_recent"])
            if gain < 0.01 and cat["graduated"] and st["active_skill"]:
                continue  # nothing measurable to gain: do not burn resources
            freq = min(cat["total_tasks"] / 20, 5)
            prio = self.scheduler.priority(1.0, freq, max(failure, 0.05), gain, cost=0.1 + 0.01 * st["dev_examples"])
            self.scheduler.enqueue("learn", cat["name"], prio)
        results = []
        for job in self.scheduler.next_jobs():
            self.scheduler.set_state(job["id"], "running")
            try:
                res = self._learn_category(job["category"])
            except Exception as e:  # a failing job must never break the loop
                res = {"category": job["category"], "status": "error", "error": str(e)}
            self.scheduler.set_state(job["id"], "done")
            results.append(res)
        for cat in self.m.categories():
            g = self._check_graduation(cat["name"])
            if g:
                results.append(g)
        if self.m.q("SELECT COUNT(*) AS c FROM episodes WHERE archived=0")[0]["c"] > int(self.m.setting("episode_limit")):
            self.m.compact()
        return {"skipped": None, "results": results, "teacher_calls": self.hub.calls_this_cycle}

    def _gather(self, category: str) -> int:
        """Step 5: label unanswered past inputs with a teacher; calibrate teachers."""
        if not (self.scheduler.can_use_network() and self.hub.available(category)):
            return 0
        added = 0
        known = {r["input_hash"] for r in self.m.examples(category)}
        for r in self.m.q("SELECT DISTINCT input FROM episodes WHERE category=? AND user_id='default' "
                          "AND solver='none'", (category,)):
            if stable_hash(r["input"]) in known:
                continue
            ans, t = self.hub.ask(category, r["input"], for_training=True)
            if ans is None:
                break
            self.m.add_example(category, r["input"], ans, "teacher", False)
            if self.m.category(category)["kind"] == "qa":
                self.m.add_fact(r["input"], ans, source=f"model:{t.id}", confidence=TEACHER_FACT_CONF,
                                acquired_mode="online", category=category)
            known.add(stable_hash(r["input"]))
            added += 1
        # calibration: measure the teacher on verified dev data it has not labelled yet
        teacher_inputs = {r["input_hash"] for r in self.m.examples(category) if r["source"] == "teacher"}
        for r in self.m.examples(category, "dev", verified_only=True)[:10]:
            if r["input_hash"] in teacher_inputs:
                continue
            ans, t = self.hub.ask(category, r["input"], for_training=True)
            if t is None:
                break
            self.hub.grade_teacher(t.id, ans, r["expected"])
            if ans is not None:
                self.m.add_example(category, r["input"], ans, "teacher", False)
        return added

    def _learn_category(self, category: str) -> dict:
        t0 = time.perf_counter()
        calls0 = self.hub.calls_this_cycle
        cat = self.m.category(category)
        added = self._gather(category)
        data = (self.m.q("SELECT COUNT(*) AS c FROM examples WHERE category=?", (category,))[0]["c"]
                + self.m.q("SELECT COUNT(*) AS c FROM semantic WHERE category=? AND archived=0", (category,))[0]["c"])
        if data == cat["seen_data"]:
            return {"category": category, "status": "no_new_data", "labelled": 0}
        self.m.x("UPDATE categories SET seen_data=? WHERE name=?", (data, category))
        min_ho = int(self.m.setting("min_heldout"))
        if len(self.heldout(category)) < min_ho:
            return {"category": category, "status": "insufficient_heldout",
                    "note": f"يلزم {min_ho} مثالًا مُتحققًا للاختبار المستقل على الأقل", "labelled": added}
        if cat["kind"] == "transform":
            res = self._learn_transform(category, t0, calls0)
        else:
            res = self._learn_qa(category, t0, calls0)
        res["labelled"] = added
        if res.get("status") == "rejected":
            self.m.x("UPDATE categories SET failed_attempts=failed_attempts+1 WHERE name=?", (category,))
            if self.m.category(category)["failed_attempts"] >= int(self.m.setting("max_failed_attempts")):
                self.m.x("UPDATE categories SET frozen=1 WHERE name=?", (category,))
                self.m.log("learning_engine", "goal_frozen", category, {"reason": "repeated failures"})
                res["frozen"] = True
        elif res.get("status") in ("accepted", "pending_approval"):
            self.m.x("UPDATE categories SET failed_attempts=0 WHERE name=?", (category,))
        return res

    def _cost(self, t0: float, calls0: int) -> dict:
        calls = self.hub.calls_this_cycle - calls0
        t = self.hub.best_teacher()
        return {"compute_seconds": round(time.perf_counter() - t0, 3), "teacher_calls": calls,
                "money_usd": round(calls * (t.cost_per_call if t else 0), 4)}

    def _origin(self, category: str) -> str:
        srcs = {r["source"] for r in self.m.examples(category, "dev")}
        return "model" if srcs == {"teacher"} else ("mixed" if "teacher" in srcs else "human")

    def _learn_transform(self, category: str, t0: float, calls0: int) -> dict:
        dev = self._dev_pairs(category)
        if len(dev) < 3:
            return {"category": category, "status": "need_data", "note": "أمثلة التطوير قليلة"}
        baseline = self.skills.active(category)
        base_prog = json.loads(baseline["program"]) if baseline else None
        # a skill may not use its own tool (no self-reference)
        own = self.toolbox.tools.pop(_tool_name(category), None)
        try:
            prog, dev_score = synthesize(self.toolbox, dev[:80])
        finally:
            if own is not None:
                self.toolbox.tools[_tool_name(category)] = own
        if prog is None or dev_score < 0.8:
            return {"category": category, "status": "rejected", "type": "B",
                    "note": f"لم يُعثر على برنامج يفسر الأمثلة (أفضل دقة {dev_score:.0%})"}
        if prog == base_prog:
            return {"category": category, "status": "no_change"}
        ho = self.heldout(category)
        b_ok, n, b_hits = self._eval(category, base_prog, ho)
        c_ok, _, c_hits = self._eval(category, prog, ho)
        lost = sum(b and not c for b, c in zip(b_hits, c_hits))
        # regression guard on categories that reuse this skill as a tool
        regressions = []
        for dep in self.skills.dependents(category):
            dep_prog = self.skills.active_program(dep)
            dho = self.heldout(dep)
            before = self._eval(dep, dep_prog, dho)[0]
            after = self._eval(dep, dep_prog, dho, tool_override=(_tool_name(category), prog))[0]
            if dho and (before - after) / len(dho) > float(self.m.setting("max_regression_drop")):
                regressions.append({"category": dep, "before": before / len(dho), "after": after / len(dho)})
        sid = self.skills.add_candidate(category, prog, dev_score, self._origin(category))
        results = {"baseline_acc": b_ok / n, "candidate_acc": c_ok / n, "n": n, "lost_cases": lost,
                   "ci": wilson_interval(c_ok, n), "dev_score": dev_score, "program": describe(prog),
                   "regressions": regressions}
        cost = self._cost(t0, calls0)
        better = diff_is_significant(b_ok, n, c_ok, n) and c_ok / n >= 0.6
        drop_ok = lost / n <= float(self.m.setting("max_regression_drop"))
        risk = "medium" if self.skills.dependents(category) else "low"
        if better and drop_ok and not regressions:
            decision = "pending_approval" if self._needs_approval(risk, cost["money_usd"]) else "accepted"
        else:
            decision = "rejected"
            self.skills.reject(sid)
        type_ = "F" if self._origin(category) != "human" else "B"
        reason = (f"دقة الاختبار المستقل الحالية {b_ok / n:.0%}" if baseline else "لا توجد مهارة لهذه الفئة")
        iid = self._record(type_, category, f"مهارة: {category}", reason, self._origin(category),
                           baseline["id"] if baseline else None, sid, results, cost, risk, decision)
        if decision == "accepted":
            self._apply(type_, category, sid, results)
        return {"category": category, "status": decision, "type": type_, "improvement": iid, **results}

    def _learn_qa(self, category: str, t0: float, calls0: int) -> dict:
        out = {"category": category, "status": "no_change", "steps": []}
        # validate model-sourced facts against verified human data first
        verified = {r["input_hash"]: r["expected"] for r in self.m.examples(category, "dev", verified_only=True)}
        pending = self.m.q("SELECT * FROM semantic WHERE category=? AND archived=0 AND verified=0 AND confidence<?",
                           (category, ACTIVE_FACT_CONF))
        good = []
        for f in pending:
            truth = verified.get(f["key_hash"])
            if truth is not None and not answers_match(truth, f["answer"]):
                self.m.x("UPDATE semantic SET archived=1 WHERE id=?", (f["id"],))
                self.m.record_error(category, "teacher_fact_wrong", cause=f["source"], fix=truth)
            else:
                good.append(f["id"])
        proof = self.heldout(category, "proof")
        strat = self._qa_strategy(category)
        if good and proof:
            b_ok, n, b_hits = self._eval(category, strat, proof, min_conf=ACTIVE_FACT_CONF)
            c_ok, _, c_hits = self._eval(category, strat, proof, min_conf=TEACHER_FACT_CONF)
            lost = sum(b and not c for b, c in zip(b_hits, c_hits))
            results = {"baseline_acc": b_ok / n, "candidate_acc": c_ok / n, "n": n, "lost_cases": lost,
                       "facts": len(good), "promote_facts": good}
            cost = self._cost(t0, calls0)
            t = self.hub.best_teacher()
            t_row = self.m.q("SELECT checked, correct FROM models WHERE id=?", (t.id,)) if t else []
            reliable = bool(t_row) and t_row[0]["checked"] >= 10 and t_row[0]["correct"] / t_row[0]["checked"] >= 0.9
            results["teacher_reliable"] = reliable
            # knowledge is accepted when it causes no harm AND either measurably helps
            # or comes from a teacher whose *measured* accuracy is high
            if lost == 0 and (c_ok > b_ok or reliable):
                decision = "pending_approval" if self._needs_approval("low", cost["money_usd"]) else "accepted"
            else:
                decision = "rejected"
            iid = self._record("F", category, f"معرفة: {len(good)} معلومة من نموذج آخر",
                               "معلومات لم يُتحقق منها بعد", "model", None, None, results, cost, "low", decision)
            if decision == "accepted":
                self._apply("F", category, None, results)
            out["steps"].append({"type": "F", "status": decision, "improvement": iid,
                                 **{k: v for k, v in results.items() if k != "promote_facts"}})
            out["status"] = decision
        # Strategy improvement (D): tune retrieval on the tuning half, prove on the proof half
        tune = self.heldout(category, "tune")
        if tune and proof:
            best = max(QA_SIM_GRID, key=lambda s: (self._eval(category, {"strategy": "recall", "min_sim": s}, tune)[0],
                                                   -abs(s - strat["min_sim"])))
            cand = {"strategy": "recall", "min_sim": best}
            if cand != strat:
                b_ok, n, _ = self._eval(category, strat, proof)
                c_ok, _, _ = self._eval(category, cand, proof)
                results = {"baseline_acc": b_ok / n, "candidate_acc": c_ok / n, "n": n,
                           "from": strat["min_sim"], "to": best}
                cost = self._cost(t0, calls0)
                sid = self.skills.add_candidate(category, cand, 0.0, "self")
                decision = "accepted" if c_ok > b_ok else "rejected"
                if decision == "accepted" and self._needs_approval("low", cost["money_usd"]):
                    decision = "pending_approval"
                if decision == "rejected":
                    self.skills.reject(sid)
                base = self.skills.active(category)
                iid = self._record("D", category, "استراتيجية الاسترجاع", f"عتبة التشابه {strat['min_sim']}",
                                   "self", base["id"] if base else None, sid, results, cost, "low", decision)
                if decision == "accepted":
                    self._apply("D", category, sid, results)
                out["steps"].append({"type": "D", "status": decision, "improvement": iid, **results})
                if decision != "rejected" or out["status"] == "no_change":
                    out["status"] = decision
        return out

    # ================================================================ graduation (independence)
    def _check_graduation(self, category: str) -> dict | None:
        cat = self.m.category(category)
        if cat["graduated"] or not self.hub.teachers:
            return None
        st = self.category_stats(category)
        if st["heldout_acc"] is None or st["heldout_n"] < int(self.m.setting("min_heldout")):
            return None
        threshold = float(self.m.setting("graduation_threshold"))
        margin = float(self.m.setting("graduation_margin"))
        t = self.hub.best_teacher()
        t_acc = self.hub.measured_accuracy(t.id) if t else None
        if st["heldout_acc"] >= threshold and (t_acc is None or st["heldout_acc"] >= t_acc - margin):
            self.m.x("UPDATE categories SET graduated=1, graduated_at=? WHERE name=?", (now(), category))
            self.m.log("learning_engine", "graduated", category,
                       {"student_acc": st["heldout_acc"], "teacher_acc": t_acc})
            return {"category": category, "status": "graduated", "student_acc": st["heldout_acc"],
                    "teacher_acc": t_acc}
        return None

    def ungraduate(self, category: str, actor: str = "user") -> None:
        """Only the user can re-enable a teacher for a graduated category."""
        self.m.x("UPDATE categories SET graduated=0, frozen=0, failed_attempts=0 WHERE name=?", (category,))
        self.m.log(actor, "ungraduated", category, {})

    def unfreeze(self, category: str, actor: str = "user") -> None:
        self.m.x("UPDATE categories SET frozen=0, failed_attempts=0 WHERE name=?", (category,))
        self.m.log(actor, "unfrozen", category, {})

    # ================================================================ dashboard
    def dashboard(self) -> dict:
        cats = [self.category_stats(c["name"]) for c in self.m.categories()]
        imps = [dict(r) for r in self.m.q("SELECT * FROM improvements ORDER BY created_at DESC LIMIT 100")]
        for i in imps:
            i["results"] = json.loads(i["results"])
            i["results"].pop("promote_facts", None)
            i["cost"] = json.loads(i["cost"])
        models = []
        for r in self.m.q("SELECT * FROM models"):
            d = dict(r)
            d["measured_accuracy"] = (r["correct"] / r["checked"]) if r["checked"] else None
            models.append(d)
        dec = {d: 0 for d in ("accepted", "rejected", "pending_approval")}
        for i in imps:
            dec[i["decision"]] = dec.get(i["decision"], 0) + 1
        sources = [dict(r) for r in self.m.q(
            "SELECT source, acquired_mode, COUNT(*) AS n, ROUND(AVG(confidence),2) AS avg_conf "
            "FROM semantic WHERE archived=0 GROUP BY source, acquired_mode")]
        spend = self.m.q("SELECT COALESCE(SUM(cost),0) AS c FROM episodes")[0]["c"]
        regressed = [c["name"] for c in cats if c["active_skill"] is None and
                     self.m.q("SELECT 1 FROM skills WHERE category=? AND status='rolled_back'", (c["name"],))]
        total_tasks = sum(c["total_tasks"] for c in cats) or 0
        total_teacher = sum(c["teacher_calls"] for c in cats)
        return {
            "summary": {
                "categories": len(cats), "graduated": sum(c["graduated"] for c in cats),
                "active_skills": sum(1 for c in cats if c["active_skill"]),
                "tools": len(self.toolbox.tools), "experiments": dec,
                "tasks": total_tasks, "teacher_calls": total_teacher,
                "teacher_spend_usd": round(spend, 4), "online": self.m.setting("online"),
                "allow_teacher": self.m.setting("allow_teacher"),
            },
            "categories": cats,
            "improvements": imps,
            "pending": [i for i in imps if i["decision"] == "pending_approval"],
            "models": models,
            "tools": [dict(r) for r in self.m.q("SELECT name, origin, version, created_at FROM tools")],
            "knowledge_sources": sources,
            "rolled_back": regressed,
            "errors": [dict(r) for r in self.m.q("SELECT * FROM errors ORDER BY count DESC LIMIT 20")],
            "verify_queue": [dict(r) for r in self.m.q("SELECT * FROM verify_queue ORDER BY created_at DESC LIMIT 20")],
            "jobs": [dict(r) for r in self.scheduler.jobs(20)],
            "changelog": [dict(r) for r in self.m.q("SELECT * FROM changelog ORDER BY seq DESC LIMIT 40")],
            "device": self.scheduler.device,
        }


def _language_level(acc: float | None, n: int) -> str:
    """No claim of mastery without evaluation evidence."""
    if acc is None or n < 10:
        return "unsupported"
    if acc >= 0.95 and n >= 30:
        return "supported"
    if acc >= 0.85:
        return "beta"
    return "experimental"
