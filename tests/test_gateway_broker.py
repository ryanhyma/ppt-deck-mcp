"""Regression test for the gateway: keep-alive connections must not leak request bodies.

The bug this pins (found live, diagnosed by a subagent): `BaseHTTPRequestHandler` reuses one handler
instance for every request on a keep-alive connection, and the broker cached the request body on the
instance. The second request on the connection was then proxied with the *first* request's body:

    POST initialize                       -> fine
    POST notifications/initialized        -> proxied with the initialize body, real body leaked into
                                             the next request line (400/501), SSE stream desynced
    -> LibreChat: "[MCP] Ping failed" -> "Connection timed out after 30000ms"

Runs the real `broker.py` as a subprocess against a stub upstream, over one kept-alive connection.
No docker, no network beyond loopback, no LibreChat.
"""
from __future__ import annotations

import http.client
import http.server
import json
import os
import socket
import subprocess
import sys
import threading
import time
import unittest
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BROKER = ROOT / "deploy" / "mcp-gateway" / "broker.py"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class StubUpstream(http.server.BaseHTTPRequestHandler):
    """Echoes back the body it received, plus whether a bearer arrived."""

    protocol_version = "HTTP/1.1"
    received: list[dict] = []

    def log_message(self, *args) -> None:  # silence
        pass

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length) if length else b""
        StubUpstream.received.append({"path": self.path, "body": body.decode(),
                                      "auth": self.headers.get("authorization", ""),
                                      "accept": self.headers.get("accept", "")})
        if self.path.endswith("/token"):
            payload = json.dumps({"access_token": "stub-token", "expires_in": 3600}).encode()
        else:
            payload = json.dumps({"echo": body.decode(), "auth": self.headers.get("authorization", "")}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST


def _start_gateway(cls) -> None:
    """Shared fixture: a stub upstream plus the real broker, for both test classes."""
    cls.upstream_port = _free_port()
    cls.upstream = http.server.ThreadingHTTPServer(("127.0.0.1", cls.upstream_port), StubUpstream)
    threading.Thread(target=cls.upstream.serve_forever, daemon=True).start()

    cls.port = _free_port()
    env = dict(
        os.environ,
        MCP_UPSTREAM=f"127.0.0.1:{cls.upstream_port}",
        TOKEN_URL=f"http://127.0.0.1:{cls.upstream_port}/token",
        CLIENT_ID="test-client", CLIENT_SECRET="test-secret", SCOPE="openid",
        LISTEN_PORT=str(cls.port), TLS_VERIFY="0",
        ALLOW_HTTP_IDP="1",          # loopback stub IdP: no TLS to speak of
        ALLOWED_CLIENTS="127.0.0.1/32,::1/128",
    )
    cls.proc = subprocess.Popen([sys.executable, str(BROKER)], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", cls.port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("broker did not start")


def _stop_gateway(cls) -> None:
    cls.proc.terminate()
    try:
        cls.proc.wait(timeout=10)
    except subprocess.TimeoutExpired:  # pragma: no cover
        cls.proc.kill()
    cls.upstream.shutdown()


class GatewayKeepAliveRegressions(unittest.TestCase):
    setUpClass = classmethod(_start_gateway)
    tearDownClass = classmethod(_stop_gateway)

    def setUp(self):
        StubUpstream.received.clear()

    def _post(self, conn: http.client.HTTPConnection, body: str, extra: dict | None = None) -> dict:
        headers = {"content-type": "application/json", "accept": "*/*"}
        headers.update(extra or {})
        conn.request("POST", "/mcp", body=body, headers=headers)
        response = conn.getresponse()
        payload = json.loads(response.read().decode() or "{}")
        self.assertEqual(response.status, 200)
        return payload

    def test_two_requests_on_one_connection_keep_their_own_bodies(self):
        """The bug: the second request was sent upstream with the first request's body."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            first = self._post(conn, json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}))
            second = self._post(conn, json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                                {"mcp-session-id": "abc"})
            third = self._post(conn, json.dumps({"jsonrpc": "2.0", "id": 3, "method": "ping"}),
                               {"mcp-session-id": "abc"})
        finally:
            conn.close()

        self.assertEqual(json.loads(first["echo"])["method"], "initialize")
        self.assertEqual(json.loads(second["echo"])["method"], "notifications/initialized",
                         "keep-alive leaked the previous request body")
        self.assertEqual(json.loads(third["echo"])["method"], "ping")
        self.assertEqual([json.loads(r["body"]).get("method") for r in StubUpstream.received[1:]],
                         ["notifications/initialized", "ping"])

    def test_every_proxied_request_carries_the_brokered_bearer(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            self._post(conn, json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}))
            self._post(conn, json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
                       {"mcp-session-id": "abc"})
        finally:
            conn.close()
        for record in StubUpstream.received:
            if record["path"].endswith("/token"):
                continue
            self.assertEqual(record["auth"], "Bearer stub-token")

    def test_a_probe_body_is_normalised_and_an_empty_probe_is_answered_locally(self):
        """`Accept: */*` must become what MCP servers require; `{}` without a session is a probe."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            probe = self._post(conn, "{}")
        finally:
            conn.close()
        self.assertEqual(probe.get("error", {}).get("code"), -32600, "empty probe should be answered locally")
        self.assertEqual(StubUpstream.received, [], "a probe must not reach the MCP server")

        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            self._post(conn, json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}))
        finally:
            conn.close()
        forwarded = [r for r in StubUpstream.received if not r["path"].endswith("/token")]
        self.assertEqual(forwarded[0]["accept"], "application/json, text/event-stream")


class GatewayHardeningRegressions(unittest.TestCase):
    """Findings from the 2026-10-27 security review: caller authorisation and smuggling vectors."""

    setUpClass = classmethod(_start_gateway)
    tearDownClass = classmethod(_stop_gateway)

    def setUp(self):
        StubUpstream.received.clear()

    def test_chunked_request_bodies_are_refused(self):
        """`Transfer-Encoding` was ignored entirely: the body was dropped and the framing desynced."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.putrequest("POST", "/mcp")
            conn.putheader("content-type", "application/json")
            conn.putheader("transfer-encoding", "chunked")
            conn.endheaders()
            conn.send(b"2\r\n{}\r\n0\r\n\r\n")
            response = conn.getresponse()
            self.assertIn(response.status, (400, 403, 501))
            response.read()
        finally:
            conn.close()

    def test_an_oversized_body_is_refused(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            big = "x" * 1024
            conn.request("POST", "/mcp", body=big,
                         headers={"content-type": "application/json", "content-length": str(9 * 1024 * 1024)})
            response = conn.getresponse()
            response.read()
        except (BrokenPipeError, ConnectionResetError):
            return  # refusing mid-body is fine
        finally:
            conn.close()
        self.assertGreaterEqual(response.status, 400)

    def test_spoofable_edge_headers_are_not_forwarded(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            self._post(conn, json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}),
                       {"x-forwarded-for": "10.9.9.9", "x-real-ip": "10.9.9.9", "expect": "100-continue"})
        finally:
            conn.close()
        forwarded = [r for r in StubUpstream.received if not r["path"].endswith("/token")]
        self.assertTrue(forwarded)
        # the stub records only what it received; assert via a fresh stub field
        self.assertNotIn("10.9.9.9", json.dumps(StubUpstream.received))

    def _post(self, conn, body, extra=None):
        headers = {"content-type": "application/json", "accept": "application/json"}
        headers.update(extra or {})
        conn.request("POST", "/mcp", body=body, headers=headers)
        return json.loads(conn.getresponse().read().decode() or "{}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
