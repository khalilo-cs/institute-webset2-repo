"""Windows desktop shell for MOSAIC.

Runs the same local Python server as Android and opens it in a native WebView.
All state is stored under the current user's LocalAppData directory.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from http.server import ThreadingHTTPServer

from mosaic.core import Mosaic
from mosaic.server import make_handler


def main():
    app_data = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "MOSAIC"
    app_data.mkdir(parents=True, exist_ok=True)
    os.environ["MOSAIC_DB"] = str(app_data / "mosaic.sqlite3")
    engine = Mosaic(os.environ["MOSAIC_DB"])
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(engine))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/"
    try:
        import webview
        webview.create_window("MOSAIC", url, width=1320, height=900, min_size=(900, 620))
        webview.start()
    except ImportError:
        import webbrowser
        webbrowser.open(url)
        print("MOSAIC is running in your default browser. Close this window to stop it.")
        thread.join()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
