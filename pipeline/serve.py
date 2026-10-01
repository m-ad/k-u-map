"""Local preview server with HTTP Range support (PMTiles needs byte ranges).

``python -m http.server`` ignores Range headers, so PMTiles archives cannot be
read through it. Usage: ``uv run python -m pipeline.serve [--port 8000]``.
"""

from __future__ import annotations

import argparse
import os
import re
import threading
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import dist_dir

_RANGE = re.compile(r"bytes=(\d*)-(\d*)$")


class RangeRequestHandler(SimpleHTTPRequestHandler):
    """Static file handler that honours single ``Range: bytes=a-b`` requests."""

    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".mjs": "text/javascript",
        ".js": "text/javascript",
        ".pmtiles": "application/octet-stream",
        ".geojson": "application/geo+json",
        ".woff2": "font/woff2",
    }

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - signature from base class
        pass

    def send_head(self):  # type: ignore[no-untyped-def]
        rng = self.headers.get("Range")
        path = Path(self.translate_path(self.path))
        if not rng or not path.is_file():
            return super().send_head()
        m = _RANGE.match(rng.strip())
        size = path.stat().st_size
        if not m:
            self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            return None
        start_s, end_s = m.groups()
        if start_s == "":
            start = max(0, size - int(end_s))
            end = size - 1
        else:
            start = int(start_s)
            end = min(int(end_s), size - 1) if end_s else size - 1
        if start > end or start >= size:
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return None
        fh = open(path, "rb")  # noqa: SIM115 - closed by copyfile caller
        fh.seek(start)
        self._remaining = end - start + 1
        self.send_response(HTTPStatus.PARTIAL_CONTENT)
        self.send_header("Content-Type", self.guess_type(str(path)))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(self._remaining))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        return fh

    def copyfile(self, source, outputfile):  # type: ignore[no-untyped-def]
        remaining = getattr(self, "_remaining", None)
        if remaining is None:
            return super().copyfile(source, outputfile)
        while remaining > 0:
            chunk = source.read(min(65536, remaining))
            if not chunk:
                break
            outputfile.write(chunk)
            remaining -= len(chunk)
        self._remaining = None
        return None


def start(root: Path, port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    """Serve ``root`` in a background thread; returns the server and its base URL."""
    handler = partial(RangeRequestHandler, directory=os.fspath(root))
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/"


def main() -> None:
    """Serve ``dist/`` until interrupted."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    server, url = start(dist_dir(), args.port)
    print(f"Serving {dist_dir()} at {url} (Ctrl+C to stop)")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
