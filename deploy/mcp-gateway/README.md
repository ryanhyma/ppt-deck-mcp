# Authenticating gateway — OAuth token broker for the deck MCP server

A ~300-line Python proxy that sits on the LibreChat network, obtains a **short-lived OAuth access token**
(client credentials), injects it, refreshes it before expiry, and forwards MCP traffic.

## Why it exists

LibreChat v0.8.9-rc1 cannot be trusted to deliver the credential itself, for two documented reasons:

1. **It ignores `headers:` on the MCP transport.** With `Authorization: 'Bearer ${…}'` configured it still
   sent `POST /mcp` with no header (verified server-side: bare 401s from the container IP).
2. **It probes every MCP server unauthenticated at startup** (`detectOAuthRequirement`: HEAD + an empty
   `POST`). A `401` whose `WWW-Authenticate` mentions `Bearer` — even with no OAuth metadata — marks the
   server `requiresOAuth: true` for the life of the process, and every later tool call runs an OAuth flow
   instead of using the configured URL. That is upstream behaviour, reported in
   [LibreChat discussion #9178](https://github.com/LibreChat-AI/LibreChat/discussions/9178); the fix on the
   client side is `requiresOAuth: false` in `librechat.yaml`, which we also set.

So the credential moved in front of the server, where it can be obtained properly and rotated:

```
browser ──signed URLs──► ppt-mcp :8931               (slide images: HMAC + expiry + subject)
LibreChat ──plain HTTP──► mcp-gateway:8931 ──Bearer──► ppt-mcp :8931/mcp
                            (docker network only, no host port, holds the only credential)
```

## Run it

```sh
cd deploy/mcp-gateway
cat > .env <<'ENV'
MCP_UPSTREAM=host.docker.internal:8931
TOKEN_URL=http://host.docker.internal:8898/realms/deck/protocol/openid-connect/token
CLIENT_ID=deck-service
CLIENT_SECRET=<secret>
SCOPE=openid
ENV
chmod 600 .env && docker compose up -d --build
```

For **Entra ID in production** only the identity values change:

```
TOKEN_URL=https://login.microsoftonline.com/<tenant>/oauth2/v2.0/token
CLIENT_ID=<api-app-client-id>   CLIENT_SECRET=<secret>   SCOPE=api://<api-app-client-id>/.default
```

## What it does to the protocol (all of it necessary, none of it auth-weakening)

| Behaviour | Why |
|---|---|
| `Accept: */*` → `application/json, text/event-stream` | the MCP server answers 406 to a wildcard Accept, and LibreChat then misreads it as auth failure |
| empty-body `POST /mcp` **without** a session id → local `200 {-32600 Invalid Request}` | LibreChat's liveness probe; JSON-RPC-over-HTTP says an invalid request is a 200 error object. Probes never reach the MCP server |
| `HEAD /mcp` → `200` | same probe, same reason |
| body cache reset per request | `BaseHTTPRequestHandler` reuses one handler per keep-alive connection; caching the body sent `notifications/initialized` upstream with the *initialize* body, desyncing the session and making LibreChat's ping fail (this was a real 30 s-timeout bug) |
| 401 from upstream → refresh the token, retry once | normal token rotation |
| `Connection: close` from the client is honoured | no cross-request state |

Real JSON-RPC requests (`initialize`, `notifications/*`, `tools/list`, `tools/call`) are always proxied
with a fresh token; the MCP server still validates every one of them.

## Security properties

- The client secret and access token live only in this container; LibreChat's config, the browser and the
  model never see them.
- No published host port: reachable only from containers on `librechat_default`.
- Runs as UID 10001, with a healthcheck; the token is never logged.
- `TLS_VERIFY=0` exists for a local test IdP only and logs a warning when used.

## Verified end to end (2026-10-27)

```
[MCP][ppt] Initialized in: 151ms · Tool list changed; refreshed 48 tools
chat: "Make a 2-slide deck about green tea" → PASS (2 slides)
audit.jsonl: tool.call subject=306b86ac-… tenant=1111… auth=entra → list_templates, build_deck, preview_deck
direct http://172.17.0.1:8931/mcp without a token → 401
docker port mcp-gateway → (none)
```

Regression tests: `tests/test_gateway_broker.py` (real broker subprocess + stub upstream over one
keep-alive connection: per-request bodies, bearer on every proxied call, probe answered locally).
