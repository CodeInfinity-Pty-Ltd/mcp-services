# mcp-services

A single Tina4-Python pod that hosts every Model Context Protocol server
for the **c8eapps** stack. Reachable from Claude.ai (and any other MCP
client) at:

```
https://mcp.c8eapps.co.za/mcp
```

OAuth 2.1 is the only way in — every request validates a Bearer JWT issued
by Keycloak at `https://auth.c8eapps.co.za/realms/mcp`.

## Layout

```
mcp-services/
├── app.py                     Tina4 entrypoint
├── Dockerfile
├── pyproject.toml + uv.lock
├── plan/                      Per-feature plans (Tina4 convention)
├── src/
│   ├── app/
│   │   ├── auth.py            Bearer JWT validator (Keycloak JWKS)
│   │   └── mcp_server.py      JSON-RPC dispatcher + tool registry
│   ├── integrations/
│   │   └── hello.py           Template integration — copy this
│   ├── routes/
│   │   ├── mcp.py             POST /mcp (thin — delegates to mcp_server)
│   │   ├── wellknown.py       /.well-known/oauth-* (RFC 9728)
│   │   ├── landing.py         GET / (Frond template, Tina4CSS)
│   │   └── health.py          GET /health (unauthenticated)
│   ├── templates/landing.twig
│   └── public/css/landing.css
└── tests/
    └── test_mcp_server.py     pytest, 14 cases, no live network
```

## Add a new MCP integration

1. **Drop a file** in `src/integrations/<name>.py` exporting a `TOOLS` list:

   ```python
   from typing import Any

   def _list_things(args: dict, claims: dict) -> dict:
       return {"things": ["a", "b", "c"]}

   TOOLS: list[dict[str, Any]] = [
       {
           "name": "yourservice.list_things",
           "description": "List the things this integration knows about.",
           "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
           "handler": _list_things,
       },
   ]
   ```

2. **Run the tests** — `uv run pytest -q`. The dispatcher auto-discovers
   the new file at import time; if your tool name collides with another
   integration's, the test suite catches it.

3. **Push to main.** CI builds + pushes the image, Flux rolls the single
   `mcp-services` pod, and your tools appear in any client's `tools/list`
   on the next call.

That's it — no new deployment manifest, no new ingress rule, no new
Keycloak client. One pod, many integrations, namespaced tool names.

## Naming convention

Tool names are `<integration>.<action>` — dotted for human grouping, flat
for the MCP namespace:

```
hello.ping
hello.whoami
hello.echo
postaction.list_tenants      (when added)
fxcm.get_open_orders         (when added)
```

## How a request flows

```
Claude.ai
   │  Bearer <jwt>
   ▼
nginx ingress  ───►  Tina4 pod (mcp-services)
                       │
                       ├─► POST /mcp
                       │     │
                       │     ├─► src/app/auth.py        validate JWT (Keycloak JWKS)
                       │     │
                       │     └─► src/app/mcp_server.py  dispatch JSON-RPC
                       │             │
                       │             ├─► initialize / tools/list / tools/call / ping
                       │             │
                       │             └─► src/integrations/<name>.TOOLS[…].handler
                       │
                       ├─► GET /.well-known/oauth-protected-resource   (RFC 9728)
                       ├─► GET /.well-known/oauth-authorization-server (Keycloak passthrough)
                       ├─► GET /                                       (Frond landing page)
                       └─► GET /health                                 (k8s probe)
```

## Connecting from Claude.ai

1. Settings → Connectors → Add custom MCP server
2. URL: `https://mcp.c8eapps.co.za/mcp`
3. Claude.ai fetches `/.well-known/oauth-protected-resource`, sees the
   Keycloak realm, walks you through login, stores the token.

## Local development

```bash
uv sync
KEYCLOAK_URL=https://auth.c8eapps.co.za \
KEYCLOAK_REALM=mcp \
PUBLIC_BASE_URL=http://localhost:7145 \
MCP_DEV_BYPASS_AUTH=1 \
uv run python app.py 0.0.0.0:7145
```

`MCP_DEV_BYPASS_AUTH=1` skips JWT validation so you can curl without
spinning up Keycloak. **Never** ship that to production.

## Tests

```bash
uv run pytest -q
```

Tests cover the dispatcher in isolation — no HTTP layer, no Keycloak.
Adding an integration without tests is a violation of the project rule
(see `plan/`).

## Keycloak setup

Realm: `mcp` at `https://auth.c8eapps.co.za/realms/mcp`. One client
(`mcp-services`) for the whole pod. See
[`.important/server-credentials.md` in the infra repo] for client_id +
client_secret. Operator notes for registering new clients are in the same
file.

## Infrastructure

K8s manifests live in
[`c8eapps_infrastructure`](https://github.com/CodeInfinity-Pty-Ltd/c8eapps_infrastructure)
under `infrastructure/mcp-services/`. One deployment, one service, one
ingress for `mcp.c8eapps.co.za`. CI bumps the image tag in that repo on
every push to `main` and Flux rolls the pod within a couple of minutes.
