# Mock Entra ID — a local OAuth 2.1 / OIDC authorization server

Purpose: exercise the **authorization-server side** of this deployment without a cloud tenant — the part
the offline stub in `entra_stub.py` cannot cover, because the stub mints tokens in-process while this
container serves real discovery, JWKS and token endpoints over HTTP.

Two things it proves and one it does not:

* **Proves** that `auth.EntraVerifier` works against a real JWKS over the network (key fetch, RS256
  verification, claim checks) — not just against keys injected into memory.
* **Proves** that a client (curl, LibreChat, the corpus runner) can complete a real token request and reach
  `/mcp` with the result.
* **Does not prove** Entra-specific behaviour: Conditional Access, token protection, tenant policies, or
  the fact that Entra ignores the RFC 8707 `resource` parameter. Those need one run against the real
  tenant before go-live (see `docs/authz-mode.md` / `docs/security.md`).

## Run it

```sh
cd deploy/oidc
docker compose up -d          # listens on http://localhost:8899
curl -s http://localhost:8899/default/.well-known/openid-configuration | head -20
```

Mint a token shaped like an Entra access token (the `requestMappings` in `oidc.json` inject `tid`, `oid`,
`scp` and `ver`):

```sh
curl -s -X POST http://localhost:8899/default/token \
  -d grant_type=client_credentials -d client_id=deck-studio \
  -d scope='api://deck-mcp/Decks.Read' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])'
```

## Point the MCP server at it

```sh
PPT_ENV=dev PPT_AUTH_MODE=entra \
PPT_ENTRA_TENANT_ID=11111111-1111-1111-1111-111111111111 \
PPT_ENTRA_AUDIENCE=api://deck-mcp \
PPT_ENTRA_ISSUER=http://localhost:8899/default \
PPT_ENTRA_JWKS_URL=http://localhost:8899/default/jwks \
.venv/bin/python server.py --transport http --host 127.0.0.1 --port 8933
```

Then `curl -X POST http://127.0.0.1:8933/mcp -H "Authorization: Bearer $TOKEN"` must get past the gate,
and the same request without a token must return 401 with a `resource_metadata` challenge.

## Verified flows (2026-10-26)

| Flow | Result |
|---|---|
| discovery + JWKS over HTTP | works (`tests/test_oidc_mock.py`) |
| client credentials → token → `auth.EntraVerifier` | accepted; wrong audience rejected |
| **authorization code + PKCE** (`/authorize` → login POST → code → `/token`) | works: token carries `iss`/`aud`/`tid`/`oid`/`scp`/`ver` |
| that PKCE token against a live `entra`-mode server | gate passes, 48 tools, deck built + previewed, audit `subject=user-alice auth=entra` |

**The host must be uniform.** The mock derives its issuer from the request host, so use one hostname
everywhere (`host.docker.internal` works from the container *and*, with `127.0.0.1 host.docker.internal`
in the host's `/etc/hosts`, from the browser). Mixing `localhost` and `host.docker.internal` gives tokens
whose `iss` the resource server (correctly) rejects.

### LibreChat's own OAuth client — known limitation

LibreChat discovers the authorization server from our protected-resource metadata and then does RFC 8414
discovery at the **issuer origin**. This mock serves issuer-per-path, so its origin-root metadata names a
bogus issuer (`…/.well-known/oauth-authorization-server` becomes the issuer id) and LibreChat's flow fails
with `OAuth handling failed`. That is the mock's discovery model, not our resource server:
`authorizationState` correctly reports `reauth_required` and the client is pointed at the right AS.

To exercise LibreChat's browser flow, use an AS with standard discovery — the real Entra tenant, or
Keycloak in a container (same PRM wiring, no code change).

## Point LibreChat at it (the full browser flow)

```yaml
mcpServers:
  ppt-oidc:
    type: streamable-http
    url: http://host.docker.internal:8933/mcp
    oauth:
      authorization_url: http://localhost:8899/default/authorize
      token_url: http://localhost:8899/default/token
      client_id: librechat
      redirect_uri: http://localhost:3080/api/mcp/ppt-oidc/oauth/callback
      scope: 'openid api://deck-mcp/Decks.Read'
```

LibreChat will do authorization-code + PKCE and show "Sign in" for the server; the consent screen is the
mock's login page. This is the flow that never runs in the offline tests.

## Files

| File | What it is |
|---|---|
| `docker-compose.yml` | the `mock-oidc` service (navikt/mock-oauth2-server, port 8899) |
| `oidc.json` | issuer config: interactive login + Entra-shaped claims per grant type |
| `tests/test_oidc_mock.py` | integration test: discovery → mint → verify → reject a wrong-audience token (skipped when the container is not running) |
