# Keycloak — local stand-in for Entra ID

A real OIDC provider with **Entra-shaped claims**, so the `entra` code path (issuer, JWKS, `aud`, `tid`,
`scp`, `roles`, key rotation) is exercised for real without a cloud tenant.

```sh
cd deploy/oidc/keycloak
KC_ADMIN_PASSWORD="$(openssl rand -hex 12)" docker compose up -d
curl -s http://host.docker.internal:8898/realms/deck/.well-known/openid-configuration | head
```

## ⚠️ The credentials in `realm-deck.json` are demo values, on purpose — never reuse them

| What | Value | Where it comes from |
|---|---|---|
| Realm | `deck` | `realm-deck.json` |
| User | `alice` / `Alice!2026` | the realm file |
| Public client (browser/PKCE) | `librechat-deck-studio` | the realm file |
| Confidential client (broker) | `deck-service` / `deck-service-secret` | the realm file |
| Admin console | `admin` / `KC_ADMIN_PASSWORD` | env at first start only |

This box's `deploy/mcp-gateway/.env` (gitignored) uses those demo values so the stack runs out of the box.
In production there is **no** Keycloak: Entra ID issues the tokens, the gateway uses a real client secret
from a secret store, and none of the values above exist. If you copy this realm anywhere shared, rotate
the client secret and the user password first.

## What the realm models

- `aud` = `api://deck-mcp` and `tid` = a fixed GUID — the two claims Entra puts in an access token and the
  server validates.
- `scp` = `Decks.Read`, `roles` = realm role `Decks.Access`, `ver` = `2.0` — Entra's shape, via
  hardcoded-claim mappers on the `entra-shape` client scope.
- `deck-audience` client scope adds the audience via an `oidc-audience-mapper`, which is how Keycloak
  expresses what Entra does with `scope=api://…/.default`.
- Access-token lifetime is **300 s**: short on purpose, to exercise the broker's refresh path.

## Ports

`8898` is published on `127.0.0.1` and `172.17.0.1` only — the browser and the gateway containers both
reach it, nothing on the LAN does. `KC_HOSTNAME` is fixed to `http://host.docker.internal:8898` so the
issuer is identical from both sides (the mock IdP it replaced derived the issuer from the request host,
which made tokens unverifiable across hostnames).
