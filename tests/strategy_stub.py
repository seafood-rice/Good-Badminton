"""A local OpenAI-compatible stub server for the strategy tests (no real LLM is ever called).

``stub`` is a pytest fixture (import it into a test module). ``stub.respond(status, body,
delay=0.0)`` queues one reply; ``body`` may be a callable ``(request_body) -> body``. When the
queue runs dry the last reply repeats. ``stub.requests`` records ``path``, ``body`` (parsed JSON,
or None) and ``headers`` of every request.
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _serve(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8")) if raw else None
        except ValueError:
            body = None
        stub = self.server.stub
        stub.requests.append({"path": self.path, "method": self.command, "body": body,
                              "headers": {k: v for k, v in self.headers.items()}})
        status, reply, delay = stub.next_reply()
        if callable(reply):
            reply = reply(body)
        if delay:
            time.sleep(delay)
        data = json.dumps(reply).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except OSError:
            pass                        # the client gave up (timeout test)

    do_GET = do_POST = _serve


class Stub:
    def __init__(self):
        self.responses = []
        self.requests = []
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.stub = self
        self._server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}/v1"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def respond(self, status, body, delay=0.0):
        self.responses.append((status, body, delay))

    def next_reply(self):
        with self._lock:
            if not self.responses:
                return 404, {"error": "no reply queued"}, 0.0
            if len(self.responses) > 1:
                return self.responses.pop(0)
            return self.responses[0]

    def chat_requests(self):
        return [r for r in self.requests if r["path"].endswith("/chat/completions")]

    def close(self):
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def stub():
    server = Stub()
    yield server
    server.close()


def chat_reply(doc):
    """The OpenAI chat-completions body whose message content is ``doc`` as JSON."""
    return {"choices": [{"message": {"content": json.dumps(doc, ensure_ascii=False)}}]}
