"""Offline demo scenario: MOSAIC learns from a teacher model, proves the
skill on independent held-out tasks, then graduates and stops needing it.
"""

from __future__ import annotations

import random

from .core import Mosaic
from .dsl import PRIMITIVES
from .models import SimulatedTeacher

FIRST = ["محمد", "خليل", "سارة", "ليلى", "عمر", "يوسف", "مريم", "نور", "أحمد", "هدى", "زياد", "رنا",
         "كريم", "سلمى", "طارق", "دينا", "فارس", "جنى", "حسن", "ريم", "باسل", "لينا", "ماجد", "عائشة"]
LATIN = ["ali", "sara", "omar", "lina", "karim", "huda", "nour", "zaid", "mona", "rami", "dana", "fadi",
         "salma", "tarek", "yara", "basel", "maya", "hadi", "rana", "majd", "joud", "adam", "reem", "sami"]
DOMAINS = ["Gmail.com", "Outlook.com", "Yahoo.com", "Mosaic.ai", "Uni.edu", "Hotmail.com", "Proton.me", "Inst.org"]
CAPITALS = {
    "مصر": "القاهرة", "السعودية": "الرياض", "المغرب": "الرباط", "تونس": "تونس", "الجزائر": "الجزائر",
    "الأردن": "عمان", "لبنان": "بيروت", "سوريا": "دمشق", "العراق": "بغداد", "الكويت": "الكويت",
    "قطر": "الدوحة", "عمان": "مسقط", "اليمن": "صنعاء", "ليبيا": "طرابلس", "السودان": "الخرطوم",
    "فرنسا": "باريس", "اليابان": "طوكيو", "ألمانيا": "برلين", "إيطاليا": "روما", "إسبانيا": "مدريد",
    "تركيا": "أنقرة", "البرازيل": "برازيليا", "كندا": "أوتاوا", "الهند": "نيودلهي",
}

ORACLES = {
    "email_domain": lambda s: s.split("@", 1)[1].lower(),
    "c_to_f": lambda s: PRIMITIVES["sum_numbers"](str(float(s) * 1.8 + 32)),
    "initials": lambda s: "".join(w[0] for w in s.split()).upper(),
    "greeting": lambda s: f"مرحبا يا {s.strip()}!",
    "capitals": lambda s: next((v for k, v in CAPITALS.items() if k in s), "لا أعرف"),
}


def _inputs(category: str, rng: random.Random, n: int) -> list[str]:
    if category == "email_domain":
        return [f"{rng.choice(LATIN)}{rng.randint(1, 999)}@{rng.choice(DOMAINS)}" for _ in range(n)]
    if category == "c_to_f":
        return [str(rng.randint(-40, 120)) for _ in range(n)]
    if category == "initials":
        return [f"{rng.choice(LATIN)} {rng.choice(LATIN)}" + (f" {rng.choice(LATIN)}" if rng.random() < .4 else "")
                for _ in range(n)]
    if category == "greeting":
        return [rng.choice(FIRST) for _ in range(n)]
    raise KeyError(category)


def build(path: str = ":memory:", error_rate: float = 0.05, seed: int = 3) -> tuple[Mosaic, random.Random]:
    rng = random.Random(seed)
    teacher = SimulatedTeacher(ORACLES, error_rate=error_rate)
    mo = Mosaic(path, teachers=[teacher])
    mo.add_category("email_domain", "transform", "استخرج نطاق البريد الإلكتروني بأحرف صغيرة", "en")
    mo.add_category("c_to_f", "transform", "حوّل درجة الحرارة من مئوية إلى فهرنهايت", "num")
    mo.add_category("initials", "transform", "أعد الأحرف الأولى من الاسم بأحرف كبيرة", "en")
    mo.add_category("greeting", "transform", "اكتب تحية للاسم المعطى", "ar")
    mo.add_category("capitals", "qa", "أجب عن عاصمة الدولة", "ar")
    # independent, human-verified held-out sets (never shown to the teacher)
    for cat in ("email_domain", "c_to_f", "initials", "greeting"):
        xs = list(dict.fromkeys(_inputs(cat, random.Random(1000 + len(cat)), 40)))[:20]
        mo.add_examples(cat, [(x, ORACLES[cat](x)) for x in xs], source="benchmark", split="heldout")
    mo.add_examples("capitals", [(f"اخبرني بعاصمة {k}", v) for k, v in CAPITALS.items()],
                    source="benchmark", split="heldout")
    # a handful of user-verified development examples: lets MOSAIC *measure* the teacher
    for cat in ("email_domain", "c_to_f", "initials", "greeting"):
        xs = _inputs(cat, random.Random(500 + len(cat)), 6)
        mo.add_examples(cat, [(x, ORACLES[cat](x)) for x in xs], source="human", split="dev")
    mo.add_examples("capitals", [(f"ما عاصمة {k}؟", CAPITALS[k]) for k in ("مصر", "فرنسا", "قطر")],
                    source="human", split="dev")
    return mo, rng


def user_traffic(mo: Mosaic, rng: random.Random, per_category: int = 15) -> dict:
    """Simulate real usage: tasks arrive; whoever can answer does."""
    stats: dict[str, dict] = {}
    for c in mo.m.categories():
        cat = c["name"]
        if cat == "capitals":
            xs = [f"ما هي عاصمة {k}؟" for k in rng.sample(list(CAPITALS), min(per_category, len(CAPITALS)))]
        else:
            xs = _inputs(cat, rng, per_category)
        s = {"mosaic": 0, "teacher": 0, "none": 0}
        for x in xs:
            r = mo.solve(cat, x)
            s["teacher" if r["solver"].startswith("teacher") else ("none" if r["solver"] == "none" else "mosaic")] += 1
        stats[cat] = s
    return stats


def run(path: str = ":memory:", rounds: int = 6, verbose: bool = True) -> Mosaic:
    mo, rng = build(path)
    for rnd in range(1, rounds + 1):
        traffic = user_traffic(mo, rng)
        cycle = mo.learn_cycle()
        if verbose:
            print(f"\n=== الجولة {rnd} ===")
            for cat, s in traffic.items():
                st = mo.category_stats(cat)
                acc = "—" if st["heldout_acc"] is None else f"{st['heldout_acc']:.0%}"
                print(f"  {cat:13s} حلّها MOSAIC={s['mosaic']:2d}  المعلّم={s['teacher']:2d}  "
                      f"دقة مستقلة={acc:>4s}  مستقل={'نعم' if st['graduated'] else 'لا'}")
            for r in cycle["results"]:
                print(f"    • {r['category']}: {r['status']}" +
                      (f"  ({r.get('baseline_acc', 0):.0%} → {r.get('candidate_acc', 0):.0%})"
                       if "candidate_acc" in r else ""))
    if verbose:
        d = mo.dashboard()["summary"]
        print(f"\nالخلاصة: {d['graduated']}/{d['categories']} فئات أصبحت مستقلة عن المعلّم، "
              f"{d['active_skills']} مهارات نشطة، {d['tools']} أدوات، "
              f"استدعاءات المعلّم={d['teacher_calls']}, التكلفة=${d['teacher_spend_usd']}")
    return mo


if __name__ == "__main__":
    run()
