"""Android bootstrap for the MOSAIC Python engine.

The Android app embeds this package with Chaquopy and serves the existing
MOSAIC web UI on a loopback-only, ephemeral localhost port.
"""
from __future__ import annotations

import os

from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Lock, Thread

from .core import Mosaic
from .server import make_handler

_lock = Lock()
_server: ThreadingHTTPServer | None = None
_thread: Thread | None = None


def start_server(data_dir: str) -> str:
    """Start one local API/UI server and return its selected TCP port."""
    global _server, _thread

    with _lock:
        if _server is not None:
            return str(_server.server_address[1])

        root = Path(data_dir)
        root.mkdir(parents=True, exist_ok=True)
        db_path = root / "mosaic.db"
        os.environ["MOSAIC_DB"] = str(db_path)
        engine = Mosaic(str(db_path))

        class LocalServer(ThreadingHTTPServer):
            allow_reuse_address = True
            daemon_threads = True

        # Bind only to loopback. Port 0 lets Android choose a free local port.
        httpd = LocalServer(("127.0.0.1", 0), make_handler(engine))
        worker = Thread(target=httpd.serve_forever, name="MOSAIC-local-api", daemon=True)
        _server = httpd
        _thread = worker
        worker.start()
        return str(httpd.server_address[1])


def stop_server() -> None:
    """Stop the local server when explicitly requested."""
    global _server, _thread
    with _lock:
        if _server is not None:
            _server.shutdown()
            _server.server_close()
        _server = None
        _thread = None
