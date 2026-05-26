# mcp-services

MCP (Model Context Protocol) servers for the **c8eapps** stack. Each service
exposes one integration — postaction admin, etios FXCM monitoring, agile-admin
queries, etc. — as a remote MCP server reachable by Claude.ai and any other
MCP client at:

```
https://mcp.c8eapps.co.za/<service>/
```

Every service is a standalone pod on our shared MicroK8s cluster, built with
**Tina4-Python**, behind a single ingress that path-routes to the right pod.
Authentication is OAuth 2.1 via the existing Keycloak at `auth.c8eapps.co.za`
— each MCP server is a Keycloak client and validates Bearer JWTs on every
request.

## Layout

```
mcp-services/
├── README.md
├── .github/workflows/build.yml      # per-service Docker build → GHCR → infra repo bump
├── services/
│   └── hello/                        # template service — copy this to add a new one
│       ├── Dockerfile
│       ├── pyproject.toml
│       ├── uv.lock
│       ├── app.py                    # Tina4 entrypoint
│       └── src/
│           ├── routes/
│           │   ├── mcp.py            # MCP JSON-RPC endpoint (/mcp)
│           │   ├── wellknown.py      # OAuth Protected Resource Metadata
│           │   └── health.py         # /health for k8s probes
│           ├── auth.py               # Bearer-token validator (Keycloak JWKS)
│           └── tools.py              # the service's own MCP tools
└── (your new service goes here)
```

## Add a new MCP service

1. `cp -R services/hello services/<name>` and rename the package in
   `pyproject.toml`.
2. Replace the tool implementations in `src/tools.py` with whatever the
   integration needs.
3. Add the service to `.github/workflows/build.yml`'s `services` matrix.
4. Add a deployment + service manifest under
   `c8eapps_infrastructure/infrastructure/mcp-services/base/<name>/`, plus
   a path rule in the shared ingress.
5. Register a Keycloak client (see [Keycloak setup](#keycloak-setup)).
6. Push to `main` — CI builds, Flux deploys, you're at
   `https://mcp.c8eapps.co.za/<name>/`.

## How a service is wired together

| Path | Purpose |
|---|---|
| `GET  /<service>/` | redirects to `/<service>/.well-known/oauth-protected-resource` for client discovery |
| `GET  /<service>/.well-known/oauth-protected-resource` | OAuth 2.0 [RFC 9728](https://datatracker.ietf.org/doc/rfc9728/) Protected Resource Metadata — tells Claude.ai which auth server to use (Keycloak realm `mcp`) |
| `POST /<service>/mcp` | MCP Streamable HTTP transport — JSON-RPC in, JSON or SSE stream out |
| `GET  /<service>/mcp` | server-initiated SSE stream (rarely used; mostly tool invocation results) |
| `GET  /<service>/health` | k8s liveness/readiness — unauthenticated, returns `{"ok":true}` |

Every request to `/mcp` validates the `Authorization: Bearer <jwt>` header
against Keycloak's JWKS for the `mcp` realm. Unauthenticated requests get a
401 with `WWW-Authenticate: Bearer resource_metadata="…"` — the standard
challenge that points Claude.ai at the discovery doc.

## Adding the server to Claude.ai

1. Settings → Connectors → Add custom MCP server
2. URL: `https://mcp.c8eapps.co.za/<service>/`
3. Claude.ai discovers the auth server, walks you through Keycloak login,
   and stores the resulting access token.

## Keycloak setup

The MCP servers all sit behind the **`mcp`** realm at
`https://auth.c8eapps.co.za/realms/mcp/`. Each service is a Keycloak
client. To register a new client:

```bash
# Get an admin token (one-time, replace USER/PASS)
TOKEN=$(curl -sS -X POST https://auth.c8eapps.co.za/realms/master/protocol/openid-connect/token \
  -d "client_id=admin-cli&grant_type=password&username=<admin>&password=<pass>" \
  | jq -r .access_token)

# Register the client
curl -sS -X POST https://auth.c8eapps.co.za/admin/realms/mcp/clients \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "clientId": "mcp-<service>",
    "publicClient": false,
    "standardFlowEnabled": true,
    "directAccessGrantsEnabled": false,
    "redirectUris": ["https://claude.ai/api/mcp/auth_callback"],
    "webOrigins": ["https://claude.ai"]
  }'
```

Save the client's `client_id` + `client_secret` somewhere — Claude.ai will
prompt for them when first connecting.

## Local development

Inside a service directory:

```bash
cd services/hello
uv sync
KEYCLOAK_URL=https://auth.c8eapps.co.za \
KEYCLOAK_REALM=mcp \
MCP_SERVICE_NAME=hello \
MCP_BASE_PATH=/hello \
PUBLIC_BASE_URL=http://localhost:7145 \
uv run python app.py 0.0.0.0:7145
```

`MCP_DEV_BYPASS_AUTH=1` skips JWT validation so you can poke at it with curl
without spinning up Keycloak — **never** ship that to production.
