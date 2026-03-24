"""
Local HTTP Server for the Chrome Extension
==========================================
Receives POST /scrape requests from the Chrome extension and runs the
web scraping agent, returning structured JSON.

Usage:
    python server.py [--port 7331]
"""

import argparse
import asyncio
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from agent import scrape_url

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


class ScrapingHandler(BaseHTTPRequestHandler):
    """Handles HTTP requests from the Chrome extension."""

    # ------------------------------------------------------------------ #
    # Routing                                                              #
    # ------------------------------------------------------------------ #

    def do_OPTIONS(self) -> None:
        """CORS pre-flight."""
        self._send(200, b"")

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(200, json.dumps({"status": "ok"}).encode())
        else:
            self._send(404, json.dumps({"error": "Not found"}).encode())

    def do_POST(self) -> None:
        if self.path == "/scrape":
            self._handle_scrape()
        else:
            self._send(404, json.dumps({"error": "Unknown endpoint"}).encode())

    # ------------------------------------------------------------------ #
    # /scrape handler                                                      #
    # ------------------------------------------------------------------ #

    def _handle_scrape(self) -> None:
        # Parse request body
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            body = json.loads(raw)
        except (ValueError, json.JSONDecodeError) as exc:
            self._send(400, json.dumps({"error": f"Bad request: {exc}"}).encode())
            return

        url: str = body.get("url", "").strip()
        if not url:
            self._send(400, json.dumps({"error": "url is required"}).encode())
            return

        focus: str = body.get("focus", "all")
        log.info("Scraping  url=%s  focus=%s", url, focus)

        # Run async agent in its own event loop (we're on a sync server thread)
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(scrape_url(url, focus))
        except Exception as exc:  # noqa: BLE001
            log.exception("Scraping failed")
            self._send(500, json.dumps({"error": str(exc)}).encode())
            return
        finally:
            loop.close()

        log.info("Done  url=%s  speakers=%d", url, len(result.speakers))
        self._send(200, result.to_json().encode())

    # ------------------------------------------------------------------ #
    # Helpers                                                             #
    # ------------------------------------------------------------------ #

    def _send(self, status: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        for k, v in _CORS_HEADERS.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:  # suppress default access log
        pass


# --------------------------------------------------------------------------- #
# Main                                                                        #
# --------------------------------------------------------------------------- #

def main() -> None:
    parser = argparse.ArgumentParser(description="Web Scraping Agent local server")
    parser.add_argument("--port", type=int, default=7331, help="Port to listen on (default: 7331)")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    args = parser.parse_args()

    server = HTTPServer((args.host, args.port), ScrapingHandler)
    log.info("Web Scraping Agent server running at http://%s:%d", args.host, args.port)
    log.info("  POST /scrape  { url, focus? }  → structured JSON")
    log.info("  GET  /health                   → { status: ok }")
    log.info("Press Ctrl-C to stop.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
