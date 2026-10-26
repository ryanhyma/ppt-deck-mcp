# Authenticating gateway (LibreChat → deck MCP)

A ~30-line nginx container that sits on the LibreChat docker network and injects the bearer token the
MCP client should have sent itself.

## Why this exists

LibreChat v0.8.9-rc1 does **not** apply `headers:` to the MCP transport: with `Authorization: 'Bearer
${PPT_MCP_TOKEN}'` in `librechat.yaml` it still sends `POST /mcp` with no header, gets a 401, and then
concludes "OAuth required" — so token-mode tool calls fail. Verified live (server logs: `401` on every
`HEAD`/`POST /mcp`), with the variable present in both the container environment and the resolved config.

Rather than weaken the server, the credential moved **in front of** it:

```
browser ──signed URLs──► ppt-mcp :8931            (slide images, HMAC + expiry + subject)
LibreChat container ──plain HTTP──► mcp-gateway:8931 ──Bearer──► ppt-mcp :8931/mcp
                                     (docker network only, no host port)
```

The MCP server stays in `PPT_AUTH_MODE=token` and validates every request. The gateway is the only
holder of the token: it never enters the model's context, the browser, or LibreChat's config, and it is
not reachable from the host or the LAN — only from containers on `librechat_default`.

## Run it

```sh
cd deploy/mcp-gateway
printf 'MCP_TOKEN=%s\n' "$(openssl rand -hex 24)" > .env    # same value as the server's PPT_MCP_TOKEN
chmod 600 .env                                              # gitignored
docker compose up -d
```

Point LibreChat at it (`librechat.yaml`):

```yaml
mcpServers:
  ppt:
    type: streamable-http
    url: http://mcp-gateway:8931/mcp
```

and allow the name in `mcpSettings` (LibreChat's SSRF guard):

```yaml
mcpSettings:
  allowedDomains: ['mcp-gateway', 'http://mcp-gateway:8931']
  allowedAddresses: ['mcp-gateway:8931']
```

Then restart the api container. Success looks like `[MCP] Initialized with 2 configured servers and 48
tools` and `[MCP][ppt] Tool list changed; refreshed 48 tools`.

## Security properties

| Property | How |
|---|---|
| Every MCP request is authenticated | server runs `PPT_AUTH_MODE=token`; a wrong or missing bearer is 401 |
| Credential exposure is minimal | only the gateway env holds it; LibreChat's config has no token |
| No new network surface | container publishes **no** host port; reachable only on `librechat_default` |
| Streaming works | `proxy_buffering off` + `chunked_transfer_encoding on` (MCP's SSE responses) |
| Images stay end-to-end signed | `PPT_PUBLIC_BASE` points at the server, and slide/template URLs are HMAC-signed |
| Audit keeps the actor | the server records `subject` from the verified token (`service-token` in token mode) |

## Limits worth stating

- **Token mode is a single-identity mode.** Every call is the same subject, so per-user ownership is not
  meaningful; use `PPT_AUTH_MODE=entra` (Keycloak rehearsal in `../oidc/keycloak/`) when you need real
  per-user identity inside LibreChat.
- The gateway is a *credential injector*, not a policy engine: it does not decide who may use the tools —
  LibreChat's agent/tool configuration does, and the server enforces object access.
- Rotate by changing the `.env` value here **and** `PPT_MCP_TOKEN` on the server, then
  `docker compose up -d` here and restart the server.

## Verified (2026-10-26)

```
LibreChat -> mcp-gateway -> ppt-mcp   [MCP] 48 tools discovered
chat turn: list_templates -> build_deck -> preview_deck, 3-slide deck, PASS
audit.jsonl: tool.call subject=service-token auth=token outcome=ok
direct http://172.17.0.1:8931/mcp without a token -> 401
docker port mcp-gateway -> (none)
```
