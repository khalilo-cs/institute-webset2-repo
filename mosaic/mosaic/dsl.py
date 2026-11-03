"""Safe skill language + program synthesis from examples.

A *skill* is a program in this small language. Programs are data (JSON),
never executed as Python code, so a self-generated skill cannot escape its
sandbox: it can only call the whitelisted primitives below, or tools that
were themselves built from those primitives and passed tests.
"""

from __future__ import annotations

import re
from typing import Callable, Iterable

Prim = Callable[[str], str]


def _words(s: str) -> list[str]:
    return s.split()


PRIMITIVES: dict[str, Prim] = {
    "strip": lambda s: s.strip(),
    "lower": lambda s: s.lower(),
    "upper": lambda s: s.upper(),
    "title": lambda s: s.title(),
    "reverse": lambda s: s[::-1],
    "reverse_words": lambda s: " ".join(reversed(_words(s))),
    "sort_words": lambda s: " ".join(sorted(_words(s))),
    "unique_words": lambda s: " ".join(dict.fromkeys(_words(s))),
    "first_word": lambda s: (_words(s) or [""])[0],
    "last_word": lambda s: (_words(s) or [""])[-1],
    "drop_first_word": lambda s: " ".join(_words(s)[1:]),
    "drop_last_word": lambda s: " ".join(_words(s)[:-1]),
    "remove_spaces": lambda s: s.replace(" ", ""),
    "collapse_spaces": lambda s: " ".join(_words(s)),
    "digits_only": lambda s: re.sub(r"\D", "", s),
    "letters_only": lambda s: re.sub(r"[^\w\s]|\d|_", "", s),
    "remove_punct": lambda s: re.sub(r"[^\w\s]", "", s),
    "after_at": lambda s: s.split("@", 1)[1] if "@" in s else s,
    "before_at": lambda s: s.split("@", 1)[0],
    "before_dot": lambda s: s.split(".", 1)[0],
    "after_last_dot": lambda s: s.rsplit(".", 1)[-1],
    "count_words": lambda s: str(len(_words(s))),
    "length": lambda s: str(len(s)),
    "initials": lambda s: "".join(w[0] for w in _words(s)),
    "first_char": lambda s: s[:1],
    "last_char": lambda s: s[-1:],
    "dashes_to_spaces": lambda s: s.replace("-", " ").replace("_", " "),
    "spaces_to_dashes": lambda s: "-".join(_words(s)),
    "sum_numbers": lambda s: _fmt(sum(float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", s))),
    "max_number": lambda s: _fmt(max((float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", s)), default=0)),
    "min_number": lambda s: _fmt(min((float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", s)), default=0)),
    "strip_arabic_diacritics": lambda s: re.sub(r"[ً-ْٰـ]", "", s),
}

MAX_LEN = 10_000


def _fmt(x: float) -> str:
    return str(int(round(x))) if abs(x - round(x)) < 1e-9 else f"{x:.2f}".rstrip("0").rstrip(".")


class Toolbox:
    """Primitives + tools learned over time (Tool Evolution)."""

    def __init__(self) -> None:
        self.tools: dict[str, dict] = {}

    def names(self) -> list[str]:
        return list(PRIMITIVES) + [f"tool:{n}" for n in self.tools]

    def add_tool(self, name: str, program: dict) -> None:
        if "steps" not in program or "linear" in program:
            raise ValueError("only text pipelines can become tools")
        self.tools[name] = program

    def apply_step(self, name: str, s: str, depth: int = 0) -> str:
        if depth > 4:
            raise RecursionError("tool nesting too deep")
        if name in PRIMITIVES:
            out = PRIMITIVES[name](s)
        elif name.startswith("tool:") and name[5:] in self.tools:
            out = self.run(self.tools[name[5:]], s, depth + 1)
        else:
            raise KeyError(f"unknown step {name}")
        if len(out) > MAX_LEN:
            raise ValueError("output too large")
        return out

    def run(self, program: dict, s: str, depth: int = 0) -> str:
        s = str(s)[:MAX_LEN]
        if "linear" in program:
            a, b = program["linear"]
            return _fmt(a * float(s.strip().replace(",", ".")) + b)
        for step in program.get("steps", []):
            s = self.apply_step(step, s, depth)
        pre, suf = program.get("wrap", ["", ""])
        return f"{pre}{s}{suf}"


def describe(program: dict) -> str:
    if "linear" in program:
        a, b = program["linear"]
        return f"y = {a:g}·x + {b:g}"
    steps = " → ".join(program.get("steps", [])) or "identity"
    pre, suf = program.get("wrap", ["", ""])
    if pre or suf:
        steps += f"  ⟶ «{pre}…{suf}»"
    return steps


def _safe(fn: Callable[[], str]) -> str | None:
    try:
        return fn()
    except Exception:
        return None


def _match(a: str | None, b: str) -> bool:
    if a is None:
        return False
    if a.strip() == b.strip():
        return True
    try:
        return abs(float(a) - float(b)) < 1e-6
    except ValueError:
        return False


def score(toolbox: Toolbox, program: dict, examples: Iterable[tuple[str, str]]) -> tuple[int, int]:
    ex = list(examples)
    ok = sum(_match(_safe(lambda i=i: toolbox.run(program, i)), o) for i, o in ex)
    return ok, len(ex)


def _fit_linear(examples: list[tuple[str, str]]) -> dict | None:
    pts = []
    for i, o in examples:
        try:
            pts.append((float(i.strip().replace(",", ".")), float(o.strip().replace(",", "."))))
        except ValueError:
            continue  # tolerate a few noisy labels
    if len(pts) < 2 or len(pts) < 0.7 * len(examples):
        return None
    # robust: try every pair, keep the line that fits most points (tolerates noisy labels)
    best, best_ok = None, 0
    for i in range(min(len(pts), 8)):
        for j in range(i + 1, min(len(pts), 8)):
            (x1, y1), (x2, y2) = pts[i], pts[j]
            if abs(x2 - x1) < 1e-12:
                continue
            a = (y2 - y1) / (x2 - x1)
            b = y1 - a * x1
            a, b = round(a, 6), round(b, 6)
            ok = sum(abs(a * x + b - y) < 0.011 for x, y in pts)
            if ok > best_ok:
                best, best_ok = {"linear": [a, b]}, ok
    return best


def _infer_wrap(outs: list[str | None], targets: list[str]) -> tuple[str, str] | None:
    for g, t in zip(outs, targets):
        if g and g in t:
            idx = t.find(g)
            return t[:idx], t[idx + len(g):]
    return None


def synthesize(toolbox: Toolbox, examples: list[tuple[str, str]], max_depth: int = 3,
               max_programs: int = 60_000) -> tuple[dict | None, float]:
    """Search for the simplest program that best explains the examples.

    Breadth-first over pipelines with observational-equivalence pruning:
    two pipelines producing identical outputs on all inputs are merged.
    Tolerates a minority of wrong labels (noisy teachers).
    """
    if not examples:
        return None, 0.0
    inputs = [i for i, _ in examples]
    targets = [o for _, o in examples]
    n = len(examples)
    best: dict | None = None
    best_ok = -1

    def consider(prog: dict, ok: int) -> None:
        nonlocal best, best_ok
        if ok > best_ok:  # strict: earlier (simpler) programs win ties
            best, best_ok = prog, ok

    lin = _fit_linear(examples)
    if lin:
        consider(lin, score(toolbox, lin, examples)[0])

    frontier: list[tuple[list[str], tuple]] = [([], tuple(inputs))]
    seen = {tuple(inputs)}
    explored = 0
    names = toolbox.names()
    for depth in range(max_depth + 1):
        next_frontier = []
        for steps, outs in frontier:
            ok = sum(_match(o, t) for o, t in zip(outs, targets))
            consider({"steps": steps}, ok)
            wrap = _infer_wrap(list(outs), targets)
            if wrap and (wrap[0] or wrap[1]):
                prog = {"steps": steps, "wrap": list(wrap)}
                consider(prog, score(toolbox, prog, examples)[0])
            if best_ok == n:
                return best, 1.0
            if depth == max_depth:
                continue
            for name in names:
                explored += 1
                if explored > max_programs:
                    break
                new_outs = tuple(_safe(lambda o=o: toolbox.apply_step(name, o)) if o is not None else None
                                 for o in outs)
                if new_outs in seen or all(o is None for o in new_outs):
                    continue
                seen.add(new_outs)
                next_frontier.append((steps + [name], new_outs))
        frontier = next_frontier
    return best, (best_ok / n if n else 0.0)
