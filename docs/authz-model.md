# Who decides what: Entra ID authenticates, LibreChat authorises, the MCP server isolates

This is the deployed model. Read it before changing auth, because the easy version of it
("LibreChat already checked the user") is also the version that leaks decks.

## The three layers

| Layer | Question it answers | Where it lives |
|---|---|---|
| **Entra ID** | *Is this a real, logged-in subject in our tenant?* | OIDC login for LibreChat; MCP access token (`aud` = this API, `tid` = our tenant, `scp`/`roles`) |
| **LibreChat** | *What may this subject do in the product?* — which agents, which MCP servers, which skills, which models, sharing/ACLs | MongoDB: user, role, `Agent` docs, `Skill` docs, `mcpServers` config, `interface` permissions |
| **MCP server (`ppt`)** | *May this exact call touch this exact object?* — deck ownership, path scoping, render quotas | `ppt-deck-mcp` itself |

Entra answers "user is user" and nothing else: it does not know that Alice may build decks and Bob may
only view them, that Alice may use the Deck Builder agent, or that a given `.pptx` belongs to Alice.
Those are **application entitlements**, and in this deployment they are stored in LibreChat.

## Why that is not enough on its own

LibreChat is the *intended path*, not a boundary around the MCP server:

- The MCP server listens on its own port with its own HTTP surface (`/mcp`, `/decks/...`, `/apps/...`).
  Anything that can reach that port does not go through LibreChat's RBAC at all. Measured on this box:
  `preview_deck` accepted any absolute `.pptx` path with no credentials, and the copy was downloadable.
- A **shared service token** (LibreChat `headers: Authorization: Bearer …`) makes every user
  indistinguishable to the server: no per-user ownership, no audit trail that means anything, and one
  stolen token is every user.
- A model reading user-supplied content is an **attacker-controlled client** (prompt injection). If the
  server trusts "LibreChat asked me", a malicious deck can ask it for a file it was never meant to see.

Implemented 2026-10-26 in `auth.py` + `server.AuthGate` (see `docs/security.md`). 

1. **Entra proves identity to the MCP server too** — not just to LibreChat. `PPT_AUTH_MODE=entra`
   validates signature, issuer, `tid`, `aud` and scope/role on **every** `/mcp` request, and the
   server derives the subject from `oid`/`sub`. The LibreChat service-token mode exists for
   development and must never be the production mode.
2. **LibreChat maps subject → entitlement** (agent available, tools visible, skills enabled). That is a
   product decision, and it is fine for it to live only in LibreChat — as long as the answer is not
   security-critical for a *different* object. Which tools exist is not a secret; which deck is whose is.
3. **The MCP server enforces object-level rules itself**, because it is the only component that can:
   - deck ownership keyed on the token subject (`oid`/`sub`), and `404` (not `403`) for someone else's id;
   - path resolution confined to configured roots, symlinks resolved, `.pptx`/`.potx` only;
   - preview images behind **signed URLs** (`HMAC(subject, deck_id, slide, exp)`) because the browser
     fetches them without a bearer token — the signature *is* the authorisation, and it expires;
   - an audit record per call: `oid`, `tid`, `azp`, tool, deck hash, slide count, result.

## Consequences worth writing into the ticket

- **Cache must be namespaced per subject.** Today the per-slide cache is global; identical content means
  one shared PNG. That is fine for a template, not for a user's deck. Either namespace the cache by
  subject or keep only template previews shared.
- **The reverse proxy is not an authorisation point** for images. It can restrict the *port* (allow only
  the LibreChat host); it cannot decide whether this person may see this slide.
- **Deck ids are opaque and are the only handle the tools hand out** (`d<16 hex>`, plus `i<16 hex>` for
  rendered images). No tool returns a filesystem path: a path tells the caller where the box keeps its
  data and invites a follow-up call with that path — the pattern that made `preview_deck` a file-read
  primitive. The id -> file mapping is `previews/.index/<id>` and is not served.
- **`deck.json` and `deck.pptx` must not be served from the asset route.** `deck.json` leaks absolute
  host paths; `deck.pptx` is the user's content. Serve images only (signed), and a separate download
  route that checks the same signature.
- **Roles live in LibreChat, so keep them tiny.** If somebody later wants "managers may publish decks to
  a share", that is a LibreChat-side entitlement with an MCP-side consequence (a *scope*), not a new
  table in the MCP server.

## Failure modes this model deliberately avoids

| Temptation | Why it is wrong here |
|---|---|
| "LibreChat is authenticated, so the MCP server can be open" | Direct callers bypass LibreChat entirely; this is exactly the hole found in the 2026-10-26 review. |
| "Put the user's id in a header from LibreChat" | A header is client-asserted. It is fine for *logging*, never for authorisation. |
| "One service token for all users" | No per-user ownership, no meaningful audit, one token = everyone. Acceptable only on a single-user dev box. |
| "Validate `resource` (RFC 8707) and call it audience binding" | Entra does not support the `resource` parameter; validate the `aud` claim against the API's client id (v2) or app ID URI (v1). |
| "Check `preferred_username` / `email`" | Not unique, mutable, and Microsoft explicitly says not to use them for authorisation. Use `oid`/`sub`. |
