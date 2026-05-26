# Feature: Combine MCP services into a single pod

Replaces the per-service-per-pod scaffold (`services/hello/`, `services/postaction/`, …)
with **one Tina4-Python app** that hosts every integration. Tools are namespaced by
integration (`hello.ping`, `postaction.list_tenants`, `fxcm.get_open_orders`) so adding
a new integration is one file, no infra changes.

Repo: `CodeInfinity-Pty-Ltd/mcp-services`.
Hosted at: `https://mcp.c8eapps.co.za/` (drop the per-service path prefix — there is
now only one pod).

## Why

Per-pod was overkill: every integration would run a copy of Tina4 + waste a pod + need
its own Keycloak client + need its own deployment manifest. For internal low-traffic
tools, one process is cheaper, simpler ops, one OAuth client.

## Criteria

- [ ] Single Tina4 app at the root of the repo (drop the `services/<name>/` layer)
- [ ] Routes are thin — MCP protocol logic lives in `src/app/mcp_server.py`, JWT
  validation in `src/app/auth.py`, helpers in `src/app/`
- [ ] Integrations live in `src/integrations/<name>.py`, each exposing a `TOOLS` list
  + handler functions
- [ ] First integration ported: `hello` (`hello.ping`, `hello.whoami`, `hello.echo`)
- [ ] Landing page on `/` uses **Tina4CSS classes** — no inline `style="..."`
- [ ] Tests in `tests/` cover: unauthenticated request → 401 with WWW-Authenticate,
  authenticated `tools/list` returns the catalogue, `tools/call hello.ping` returns
  `pong`, JWT signature failure → 401
- [ ] Ingress drops the `/hello/*` path prefix — single rule routes `/` →
  `mcp-services` pod; OAuth-protected-resource doc now points at
  `https://mcp.c8eapps.co.za/mcp`
- [ ] CI workflow is a single Docker build (no matrix), pushes
  `ghcr.io/codeinfinity-pty-ltd/mcp-services:v<run>-<sha>`
- [ ] Infrastructure: single Deployment `mcp-services` (rename from `mcp-hello`),
  single Service, single Ingress at `mcp.c8eapps.co.za/`
- [ ] Keycloak client renamed `mcp-hello` → `mcp-services` (or new client created
  alongside; old one deprecated)
- [ ] Live-verify: `POST /mcp tools/list` returns `hello.*` tools, `tools/call`
  works, `whoami` shows the JWT claims

## Approach

API-only (no Frond templates for app UI — only the small landing page on `/`).
Server-rendered choice doesn't apply because there's no app UI here — all clients are
machine-to-machine MCP.

Layout (post-refactor):

```
mcp-services/
├── plan/
├── README.md
├── Dockerfile
├── pyproject.toml
├── uv.lock
├── app.py                                # Tina4 entrypoint
├── src/
│   ├── routes/
│   │   ├── mcp.py                        # @post("/mcp") — thin, delegates to app/mcp_server.py
│   │   ├── wellknown.py                  # OAuth PRM + Keycloak discovery passthrough
│   │   ├── landing.py                    # @get("/") small Tina4CSS landing page
│   │   └── health.py                     # @get("/health")
│   ├── app/
│   │   ├── auth.py                       # Keycloak JWKS Bearer validator
│   │   └── mcp_server.py                 # JSON-RPC dispatcher, tool registry, error mapping
│   └── integrations/
│       └── hello.py                      # TOOLS = [...] + ping/whoami/echo handlers
└── tests/
    └── test_mcp_server.py
```

## Tooling rules from the Tina4 skill applied

- Routes are thin — no business logic in route handlers
- No inline styles — Tina4CSS classes only
- No code without tests — `tests/test_mcp_server.py` ships in the same commit
- Convention over configuration — files in `src/routes/`, `src/app/`, `src/integrations/`
  auto-load
- Less code wins — return dicts, framework auto-encodes JSON

## Status: Approved (pending execution)

Approved verbally by user 2026-05-26 ("We can combine services in this pod / Use the
tina4 skills"). Proceeding with execution.
