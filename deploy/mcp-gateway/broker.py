#!/usr/bin/env python3
"""Authenticating gateway: obtains a real OAuth token, injects it, refreshes it, proxies MCP traffic.

Why a broker instead of a static bearer: production issues short-lived access tokens (Entra ID, 60 min).
A hand-pasted token in a proxy config is a shared secret that expires, cannot be rotated, and gives every
caller the same identity forever. This process does what a production client does:

    client_credentials grant  ->  access_token (+ expires_in)
        refresh at ~80% of the lifetime, retry once on a 401
        inject `Authorization: Bearer …` on every proxied request

It is protocol-agnostic on purpose: point it at Entra ID and it works unchanged.

Configuration (env):

    MCP_UPSTREAM        host:port of the MCP server          (required, e.g. host.docker.internal:8931)
    TOKEN_URL           OAuth token endpoint                 (required)
    CLIENT_ID           client id for the grant               (required)
    CLIENT_SECRET       client secret (client_secret_post)    (required; or use CLIENT_ASSERTION)
    SCOPE               requested scope(s)                    (default: empty)
    AUDIENCE            optional `audience` parameter (RFC 8693 style providers)
    LISTEN_PORT         port to listen on                     (default 8931)
    TLS_VERIFY          "1"/"0" for upstream+token (default 1; never 0 in production)
    PPT_DEBUG           verbose logging

Security posture: listens only on the container network, holds the only credential, forwards the token to
exactly one upstream, never logs the token, and refuses to start without TLS verification unless
explicitly disabled for a local test.
"""
from __future__ import annotations

import http.client
import json
import os
import ssl
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = os.environ["MCP_UPSTREAM"]
TOKEN_URL = os.environ["TOKEN_URL"]
CLIENT_ID = os.environ["CLIENT_ID"]
CLIENT_SECRET = os.environ["CLIENT_SECRET"]
SCOPE = os.environ.get("SCOPE", "")
AUDIENCE = os.environ.get("AUDIENCE", "")
PORT = int(os.environ.get("LISTEN_PORT", "8931"))
TLS_VERIFY = os.environ.get("TLS_VERIFY", "1") not in ("0", "false", "no")
DEBUG = bool(os.environ.get("PPT_DEBUG"))

HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
    "transfer-encoding", "upgrade", "host", "authorization", "content-length",
}


def log(message: str) -> None:
    print(f"[gateway] {message}", file=sys.stderr, flush=True)


class TokenBroker:
    """Holds one access token, refreshes it before it expires, and hands it out thread-safely."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at = 0.0
        self._refresh_margin = 60.0
        self.fetches = 0
        self.last_error: str | None = None

    def _tls_context(self):
        if TLS_VERIFY:
            return ssl.create_default_context()
        log("WARNING: TLS verification disabled (TLS_VERIFY=0) — acceptable only for a local test IdP")
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context

    def _fetch(self) -> tuple[str, float]:
        form = {
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        }
        if SCOPE:
            form["scope"] = SCOPE
        if AUDIENCE:
            form["audience"] = AUDIENCE
        request = urllib.request.Request(
            TOKEN_URL, data=urllib.parse.urlencode(form).encode(),
            headers={"content-type": "application/x-www-form-urlencoded", "accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=20, context=self._tls_context()) as response:
            payload = json.loads(response.read().decode())
        token = payload.get("access_token")
        if not token:
            raise RuntimeError(f"token endpoint returned no access_token: {str(payload)[:200]}")
        lifetime = float(payload.get("expires_in") or 300)
        # refresh a little before expiry, and never later than half the lifetime for short tokens
        margin = min(max(lifetime * 0.2, 15.0), max(lifetime / 2, 15.0))
        return token, time.time() + lifetime - margin

    def get(self) -> str:
        with self._lock:
            if self._token and time.time() < self._expires_at:
                return self._token
            try:
                self._token, self._expires_at = self._fetch()
                self.fetches += 1
                self.last_error = None
                log(f"token acquired (fetches={self.fetches}, valid for ~{int(self._expires_at - time.time())}s)")
                return self._token
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"{type(exc).__name__}: {exc}"
                log(f"token fetch failed: {self.last_error}")
                if self._token and time.time() < self._expires_at + 300:
                    log("serving the current token while it is still valid")
                    return self._token
                raise

    def invalidate(self) -> None:
        with self._lock:
            self._expires_at = 0.0

    def status(self) -> dict:
        return {"fetches": self.fetches, "expires_in": max(0, int(self._expires_at - time.time())),
                "last_error": self.last_error}


BROKER = TokenBroker()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "deck-mcp-gateway/1"

    def log_message(self, fmt: str, *args) -> None:  # quieter default logging
        if DEBUG:
            log(fmt % args)

    # -- helpers
    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> bytes:
        length = self.headers.get("content-length")
        if not length:
            return b""
        remaining = int(length)
        chunks = []
        while remaining > 0:                      # read() may short-read on a socket
            chunk = self.rfile.read(remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _headers(self) -> dict:
        """Forward the client's headers, merging duplicates.

        http.client takes a dict, so repeated headers collapse — and MCP clients really do send more
        than one `Accept` line. Joining them with ", " is what the wire format means, and dropping the
        second value makes the server answer 406 Not Acceptable.
        """
        merged: dict[str, str] = {}
        for key, value in self.headers.items():
            if key.lower() in HOP_BY_HOP:
                continue
            merged[key] = f"{merged[key]}, {value}" if key in merged else value
        # The MCP client in LibreChat v0.8.9-rc1 sends `Accept: */*`; the MCP server requires both
        # application/json and text/event-stream and answers 406 otherwise — which the client then
        # misreads as "needs OAuth". Normalise it here, where we already speak for the protocol.
        accept_key = next((k for k in merged if k.lower() == "accept"), "Accept")
        accept = merged.get(accept_key, "")
        if not accept or "*/*" in accept:
            merged[accept_key] = "application/json, text/event-stream"
        if DEBUG:
            log(f"forward accept={merged[accept_key]!r}")
        return merged

    def _proxy(self, retry: bool = True) -> None:
        host, _, port = UPSTREAM.partition(":")
        connection = http.client.HTTPConnection(host, int(port or 80), timeout=300)
        body = self._peek_body()
        headers = self._headers()
        try:
            headers["Authorization"] = f"Bearer {BROKER.get()}"
            connection.request(self.command, self.path, body=body, headers=headers)
            upstream = connection.getresponse()
            if upstream.status == 401 and retry:
                BROKER.invalidate()
                connection.close()
                log("upstream rejected the token; refreshing once and retrying")
                self._proxy(retry=False) if False else None
                connection = http.client.HTTPConnection(host, int(port or 80), timeout=300)
                headers["Authorization"] = f"Bearer {BROKER.get()}"
                connection.request(self.command, self.path, body=body, headers=headers)
                upstream = connection.getresponse()
            if DEBUG:
                preview = body[:160].decode("utf-8", "replace") if body else "<empty>"
                log(f"{self.command} {self.path} -> {upstream.status} body={preview}")
            self.send_response(upstream.status, upstream.reason)
            for key, value in upstream.getheaders():
                if key.lower() in {"connection", "transfer-encoding", "content-length"}:
                    continue
                self.send_header(key, value)
            self.send_header("transfer-encoding", "chunked")
            self.end_headers()
            while True:
                chunk = upstream.read(4096)
                if not chunk:
                    break
                self.wfile.write(b"%X\r\n%s\r\n" % (len(chunk), chunk))
                self.wfile.flush()          # MCP streams SSE: never buffer
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        finally:
            connection.close()

    # -- routes
    def _is_empty_probe(self) -> bool:
        """LibreChat pings the transport with an empty JSON body and treats a 4xx as 'needs OAuth'.

        JSON-RPC over HTTP says an invalid request is an *error object with HTTP 200*, so answering it
        that way is both correct and what keeps the client on the normal path.
        """
        if self.command != "POST" or not self.path.rstrip("/").endswith("/mcp"):
            return False
        # Only probes *before* a session exists. `notifications/initialized` is a POST with an empty
        # body inside a real session — intercepting it breaks the handshake (the client waits for 202).
        if self.headers.get("mcp-session-id"):
            return False
        return self._peek_body() in (b"", b"{}", b"null")

    def _peek_body(self) -> bytes:
        """The body of *this* request, read at most once.

        BaseHTTPRequestHandler reuses one handler instance for every request on a keep-alive
        connection, so this cache MUST be reset per request (see `_new_request`) — otherwise request
        N+1 is proxied with request N's body and the byte stream desynchronises.
        """
        if getattr(self, "_body_cache", None) is None:
            self._body_cache = self._body()
        return self._body_cache

    def _new_request(self) -> None:
        self._body_cache = None

    def _reply_invalid_request(self) -> None:
        self._send_json(200, {"jsonrpc": "2.0", "id": None,
                              "error": {"code": -32600, "message": "Invalid Request"}})

    def do_GET(self) -> None:
        self._new_request()
        if self.path.startswith("/gateway-health"):
            self._send_json(200, {"ok": True, "upstream": UPSTREAM, **BROKER.status()})
            return
        if self._is_empty_probe():
            self._reply_invalid_request()
            return
        self._proxy()

    do_POST = do_GET
    do_DELETE = do_GET

    def do_HEAD(self) -> None:
        self._new_request()
        self.send_response(200)
        self.send_header("content-length", "0")
        self.end_headers()


def main() -> int:
    try:
        BROKER.get()  # fail fast: no point listening without a token
    except Exception as exc:  # noqa: BLE001
        log(f"cannot start without a token: {exc}")
        return 2
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    log(f"listening on :{PORT} -> {UPSTREAM} (token: {TOKEN_URL})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
