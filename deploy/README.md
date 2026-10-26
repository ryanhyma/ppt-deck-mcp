# Deploying this server safely

A one-page checklist for a real deployment. Everything here is implemented and covered by tests; the
evidence and the remaining backlog live in [`../docs/security.md`](../docs/security.md), the identity model
in [`../docs/authz-model.md`](../docs/authz-model.md), and the Windows runbook in
[`../docs/windows-service.md`](../docs/windows-service.md).

## 1. Before anything else

| Decision | Setting | Why |
|---|---|---|
| Who may call the tools? | `PPT_AUTH_MODE=entra` (production) or `token` (single user) | `none` is refused when `PPT_ENV=prod` |
| Which tenant / API? | `PPT_ENTRA_TENANT_ID`, `PPT_ENTRA_AUDIENCE` (this API's client id) | Entra signs other tenants' tokens too; `aud` is compared exactly because Entra ignores RFC 8707 `resource` |
| Which permission? | `PPT_ENTRA_REQUIRED_SCOPES=Decks.Read`, `PPT_ENTRA_REQUIRED_ROLES=Decks.Access` | delegated scope **or** app role; a token with neither is a 403 |
| Signing key for links | `PPT_URL_SECRET` (32+ random bytes) | without it, download/image links die on every restart |
| Where may decks come from? | `PPT_OUTPUT_DIR`, `PPT_UPLOAD_DIR`, `PPT_TEMPLATE_PATH`; `PPT_ALLOWED_ROOTS` empty | resolved + containment checked; a symlink cannot escape |
| Where does the audit trail go? | `PPT_AUDIT_LOG`, `PPT_AUDIT_MAX_BYTES` | JSONL `tool.call` / `asset.get` / `auth.reject`, redacted, rotated; ship to the SIEM |

Copy [`windows/ppt-mcp.env.example`](windows/ppt-mcp.env.example) as the starting point.

## 2. Wire the client

`deploy/librechat/mcpServers.ppt.yaml`, pick one:

```yaml
  ppt:
    type: streamable-http
    url: http://host.docker.internal:8931/mcp
    headers: { Authorization: 'Bearer ${PPT_MCP_TOKEN}' }        # token mode
    # oauth: { … scope: 'api://<audience>/Decks.Read' … }        # entra mode
```

`/apps/` stays public on purpose: the Deck Studio pane is static HTML/JS fetched by the browser with no
bearer, and it holds no user data. Slide images are authorised by signed, expiring URLs instead.

## 3. Network

- The MCP port is for **LibreChat only**. On Windows the installer adds an inbound rule limited to that
  host, plus an outbound block on `soffice.exe` — untrusted decks must not reach the network.
- Single-user deployment? Use `--transport stdio`: no listening socket at all.
- Never expose `/decks/` directly to the LAN or a WAF without the signature check intact.

## 4. Validate the deployment

```sh
# 1. unauthenticated requests must be refused, with a challenge
curl -si -X POST https://mcp.example.gc.ca/mcp | head -3          # 401 + WWW-Authenticate

# 2. a Graph token must NOT be accepted (the classic misconfiguration)
curl -si -X POST https://mcp.example.gc.ca/mcp \
     -H "Authorization: Bearer $GRAPH_TOKEN" | head -1            # 401

# 3. the audit log fills up, and contains no paths or credentials
tail -3 /var/log/ppt-mcp/audit.jsonl
```

Then run the suite that pins all of it:

```sh
.venv/bin/python -m unittest discover -s tests      # 163 tests, no services needed
```

## 5. Still the operator's job

- **LibreOffice sandbox** (`docs/security.md` item 3): dedicated low-privilege account, no egress,
  render timeouts, patching. The audit log records what was rendered, not what it contained.
- **Retention and disposal** of `data/uploads`, `data/previews` and the audit files (MP-6 / SI-12 in
  ITSG-33 terms) — nothing here deletes user content except the render cache pruner.
- **Encryption at rest** for those directories and the audit log, with keys managed per ITSP.50.106.
- **Rate limits** in front of the port if it is reachable by more than one client.
