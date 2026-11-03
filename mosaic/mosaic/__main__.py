"""CLI:  python -m mosaic demo | serve [--demo] [--db PATH] [--port N] [--anthropic]"""

from __future__ import annotations

import argparse
import os

from . import demo
from .core import Mosaic
from .models import AnthropicTeacher
from .server import serve


def main() -> None:
    p = argparse.ArgumentParser(prog="mosaic")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("demo", help="run the offline learning → graduation demo")
    s = sub.add_parser("serve", help="start the web UI + API")
    s.add_argument("--db", default=os.environ.get("MOSAIC_DB", "mosaic.db"))
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--demo", action="store_true", help="preload the demo categories and simulated teacher")
    s.add_argument("--anthropic", action="store_true", help="use a real Claude model as teacher (ANTHROPIC_API_KEY)")
    a = p.parse_args()
    if a.cmd == "demo":
        demo.run()
        return
    if a.demo:
        mo, rng = demo.build(a.db)
        demo.user_traffic(mo, rng, per_category=5)
    else:
        mo = Mosaic(a.db)
    if a.anthropic:
        mo.hub.register(AnthropicTeacher())
    serve(mo, a.host, a.port)


if __name__ == "__main__":
    main()
