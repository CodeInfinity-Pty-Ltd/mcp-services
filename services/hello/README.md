# mcp-hello

Template MCP server. Two tools:

- `ping()` → `"pong"` — proves the protocol is wired correctly.
- `whoami()` → returns the OAuth subject + email from the validated JWT, so
  you can confirm the auth chain end-to-end.

Hosted at `https://mcp.c8eapps.co.za/hello/`. See the top-level
[README](../../README.md) for how to register it in Claude.ai or copy this
service as a starting point for a new integration.
