"""Receive person-detection JSON and print it in the terminal."""

from __future__ import annotations

import json
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import os

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass


HOST = "0.0.0.0"
PORT = int(os.getenv("TARGET_PORT", "8765"))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # quieter access log
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            print(f"[{datetime.now():%H:%M:%S}] invalid JSON: {raw!r}")
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"ok":false}')
            return

        text = json.dumps(payload, ensure_ascii=False, indent=2)
        body = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

        # print after response so a blocked console never stalls the client
        print(f"\n[{datetime.now():%H:%M:%S}] received POST {self.path}", flush=True)
        print(text, flush=True)


def main() -> None:
    server = HTTPServer((HOST, PORT), Handler)
    print(f"Listening on http://{HOST}:{PORT}  (TARGET_IP in .env should point here)", flush=True)
    print("Waiting for person-detection JSON... Ctrl+C to stop.\n", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
