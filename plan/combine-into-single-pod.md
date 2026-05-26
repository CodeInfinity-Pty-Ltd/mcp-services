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

- [x] Single Tina4 app at the root of the repo (drop the `services/<name>/` layer)
- [x] Routes are thin — MCP protocol logic lives in `src/app/mcp_server.py`, JWT
  validation in `src/app/auth.py`, helpers in `src/app/`
- [x] Integrations live in `src/integrations/<name>.py`, each exposing a `TOOLS` list
  + handler functions (auto-discovered via pkgutil at import time)
- [x] First integration ported: `hello` (`hello.ping`, `hello.whoami`, `hello.echo`)
- [x] Landing page on `/` uses **Tina4CSS classes** — no inline `style="..."`
  (Frond template + `src/public/css/landing.css`)
- [x] Tests in `tests/` cover dispatcher init/list/call/batch/notifications/errors
  (14 cases, `uv run pytest -q` → 14 passed)
- [x] Ingress drops the `/hello/*` path prefix — single rule routes `/` →
  `mcp-services` pod; OAuth-protected-resource doc points at
  `https://mcp.c8eapps.co.za/mcp`
- [x] Infrastructure: single Deployment `mcp-services` (renamed from `mcp-hello`),
  single Service, single Ingress at `mcp.c8eapps.co.za/`
- [x] Live-verify: `POST /mcp tools/list` returns `hello.*` tools, `tools/call
  hello.ping` returns `pong` (verified 2026-05-26)
- [ ] **CI workflow** is committed but GitHub Actions on the mcp-services repo is
  wedged — `workflow_dispatch` returns HTTP 500 and pushes don't queue runs.
  Worked around with a one-off manual build + push to GHCR
  (`v2-91a79f7c30418457250d9440611e74e1b21960af`) so production isn't blocked.
  This needs unsticking before the next change can deploy via the normal flow.
  See open follow-up below.
- [x] Keycloak realm + client: existing `mcp-hello` client in the `mcp` realm
  is being reused. (Could rename to `mcp-services` later; the client_id is
  ultimately just a label.)

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

## Open follow-up

- **GitHub Actions on `CodeInfinity-Pty-Ltd/mcp-services` is wedged.** Pushes don't
  trigger workflow runs and `workflow_dispatch` returns HTTP 500. Tried: toggle
  workflow active state, toggle repo actions permissions, rename `build.yml →
  ci.yml`, add a minimal `hello.yml` sanity workflow (also didn't trigger), empty
  commits, multiple pushes — total CI runs stuck at 1 (the original from commit
  `2a70309`). Workflow file YAML is valid. Suspect a stale GitHub-side state on
  this repo specifically. Possible fixes when picked up:
  - Open a ticket with GitHub Support
  - Delete + recreate the repo (drastic; would lose the first CI run history)
  - Try transferring repo ownership briefly to force re-registration
  - Wait it out (GitHub Actions has had similar transient issues before that
    resolve themselves in 24-48h)

  Until that's resolved, image rebuilds need to be done manually:
  ```
  cd ~/IdeaProjects/mcp-services
  docker buildx build --platform linux/amd64 --load \
    -t ghcr.io/codeinfinity-pty-ltd/mcp-services:v<N>-<sha> \
    -t ghcr.io/codeinfinity-pty-ltd/mcp-services:latest .
  docker push ghcr.io/codeinfinity-pty-ltd/mcp-services:v<N>-<sha>
  docker push ghcr.io/codeinfinity-pty-ltd/mcp-services:latest
  # then bump the newTag in c8eapps_infrastructure
  ```

## Status: Complete (deployed) — 2026-05-26

Live at `https://mcp.c8eapps.co.za/mcp`. End-to-end MCP call verified:
`tools/list` returns `hello.ping`, `hello.whoami`, `hello.echo`; `tools/call
hello.ping` returns `pong`. Image `v2-91a79f7c30418457250d9440611e74e1b21960af`
pushed manually due to CI block above.
