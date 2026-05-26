# Feature: Basecamp + Clockify integrations

Add two MCP integrations to the existing `mcp-services` pod so Claude can
read the c8eapps team's project and time data without separate tooling.

Repo: `CodeInfinity-Pty-Ltd/mcp-services`. Hosted at
`https://mcp.c8eapps.co.za/mcp`. New tools appear under the `basecamp.*`
and `clockify.*` namespaces.

## Criteria

### Code (mcp-services repo)

- [x] `src/integrations/basecamp.py` — Basecamp 3 client with OAuth-2 token
  refresh, exporting these tools:
  - `basecamp.list_projects`
  - `basecamp.get_project`
  - `basecamp.list_people`
  - `basecamp.list_todosets`
  - `basecamp.list_todos`
  - `basecamp.list_card_tables`           (kanban — Basecamp's `card_tables` dock)
  - `basecamp.list_cards`                 (one column on a card table)
  - `basecamp.get_card`
  - `basecamp.list_messages`
  - `basecamp.list_comments_on`           (any recording_id)
  - `basecamp.list_schedule_entries`
- [x] `src/integrations/clockify.py` — Clockify v1 client with `X-Api-Key`,
  exporting:
  - `clockify.list_workspaces`
  - `clockify.get_current_user`
  - `clockify.list_projects`              (per workspace)
  - `clockify.list_clients`               (per workspace)
  - `clockify.list_time_entries`          (date-range filtered)
  - `clockify.summary_report`             (Reports API)
- [x] Both modules read credentials from env vars only — **no hard-coded
  secrets in source**.
- [x] `tests/test_basecamp.py` + `tests/test_clockify.py` cover the tool
  contracts using mocked `requests` responses. New tests join the existing
  14 dispatcher tests — suite passes (30/30).
- [x] README updated with Basecamp OAuth one-time setup walkthrough.

### Infrastructure (c8eapps_infrastructure repo)

- [x] `infrastructure/mcp-services/base/secrets-integrations.yaml` —
  SOPS-encrypted `Opaque` Secret with the five env keys. Ships with
  placeholder values; **user fills in the real ones** after the Basecamp
  OAuth walkthrough + Clockify key generation.
- [x] `infrastructure/mcp-services/app/deployment.yaml` — `envFrom`
  reference added.

### Live verification

- [ ] After deploy: authenticated `tools/list` shows all 17 new tools
  alongside the three `hello.*` ones.
- [ ] `tools/call basecamp.list_projects` returns a non-empty array
- [ ] `tools/call clockify.list_workspaces` returns at least one workspace

## Env-var contract (shared by both modules)

| Var | Source | Purpose |
|---|---|---|
| `BASECAMP_CLIENT_ID`     | launchpad.37signals.com integration | OAuth client identity |
| `BASECAMP_CLIENT_SECRET` | launchpad.37signals.com integration | OAuth client secret |
| `BASECAMP_REFRESH_TOKEN` | one-time browser OAuth flow         | long-lived refresh token; the pod swaps it for fresh 2-week access_tokens on demand |
| `BASECAMP_ACCOUNT_ID`    | the OAuth `authorization` response  | which Basecamp account to read |
| `CLOCKIFY_API_KEY`       | Clockify Profile → API page         | static API key sent as `X-Api-Key` |

## Approach

- Match the existing `hello.py` pattern — one module per integration, each
  exports a `TOOLS` list. The dispatcher in `src/app/mcp_server.py` picks
  them up automatically via `pkgutil.iter_modules` at import time.
- Auth is module-private. Basecamp keeps a cached access_token + expiry
  and refreshes via `https://launchpad.37signals.com/authorization/token`
  60 seconds before expiry. Clockify is stateless (just a header).
- HTTP errors → bubble up as MCP tool errors (`isError=true`), not
  transport errors, so Claude sees a useful message.

## Status: Approved (in progress)

Approved verbally 2026-05-26. Basecamp OAuth setup walkthrough lands in
the README so the user can complete the one-time browser flow.
