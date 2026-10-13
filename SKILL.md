---
name: 1panel-skills
description: Use for 1Panel resource queries, Docker software installation and Compose maintenance, OpenResty website and reverse-proxy configuration, HTTPS, web diagnostics, proxy configuration and scheduled maintenance. Reuse the original modules and client; use the added scripts for checked, reversible changes rather than inventing raw API calls.
version: 1.2.2
---

# 1Panel Skills: personal operations extension

## Operating workflow

Use the original query modules to discover the exact target. Use `run.py` for added
operations: inspect -> plan -> apply -> verify -> restore the saved configuration
when appropriate. A plan digest prevents stale or mismatched input; it is not human
authentication. The assistant handles these script steps, not the user.

For an explicit user request to install, update or configure a named service,
complete the ordinary checks and execute within that request. Do not ask for
confirmation at every step. Ask only when the real target or an important new
side effect is outside the request. Never treat a failed check as permission to
bypass the scripts using raw API, sudo, Docker Socket or a different tool.

Do not implement deletion of volumes/websites/databases, global pruning, arbitrary
root scripts, firewall changes or another backup platform. Existing cloud snapshots
remain external to this Skill. Keep small prior-config records for undo.

Read [docs/guidebook/usage.md](docs/guidebook/usage.md) for exact inputs and examples.
Read [references/ops-contract.md](references/ops-contract.md) for verified API
contracts, runtime semantics and remaining integration limitations.

## Dependencies and environment

Original query/transport code requires Node.js 22 or later. Added workflows use
Python 3.10+, PyYAML, and Docker Compose CLI for real Compose configuration checks.
The CLI does not need Docker Socket write access to run `compose config`. HTTP
probes and public-release downloads use curl.

| Variable | Type | Required | Purpose |
|---|---|---|---|
| ONEPANEL_BASE_URL | config | API operations | Local panel origin, without a Swagger path |
| ONEPANEL_API_KEY | secret | API operations | 1Panel API key; never print or commit it |
| ONEPANEL_NODE | config | optional | Absolute Node executable when absent from PATH |
| ONEPANEL_TIMEOUT_MS | config | optional | Original client's request timeout |
| ONEPANEL_SKIP_TLS_VERIFY | config | optional | Original compatibility option; leave unset normally |
| ONEPANEL_OPS_STATE_DIR | config | optional | Mutable private change-record directory outside the Skill package |

Missing API credentials disable API actions, not offline validators or status.
Scripts read environment values from the current process. They never search
AgentDock private configuration files for credentials. Do not paste API keys into
chat, command-line arguments, GitHub issues, source code, or test fixtures.

## Portable entry point

Run from the package root:

```bash
printf '%s' '{"skill_action":"status"}' | python3 run.py
```

Inputs are stdin JSON. Output is JSON with stable error codes. Large configurations
may be passed using `content_file` instead of `content`. Proxy helpers save complete
candidates to private draft files and return their paths; stdout omits configuration text. Use the private candidate file as the source.

Use `status`/`actions` to discover implemented operations. `query` delegates to the
original module registry. `plan` makes no 1Panel write request; `apply` consumes one
exact plan. Follow `submitted`/`uncertain` results with `verify`, never blind retries.
For missing task receipts, see the operator-assisted reconcile procedure in the usage
guide. Never invent acknowledge_no_inflight: elapsed time and zero executing tasks alone
do not prove a delayed request cannot run. Recovery is not deployment success.
Do not claim success while an asynchronous task is pending or a business error was
returned with HTTP 200. For sites, choose a meaningful health URL and prefer an
origin probe when diagnosing CDN versus origin behaviour.

Compose images require a digest by default. Tags (including version tags) are mutable;
use options.allow_mutable_images only for an explicit tag-based choice. Existing external
networks require exact options.allow_networks names. Ordinary updates preserve the service
set and mounts; adding/removing services needs a separate deployment design.
Compose updates preserve prior local image IDs for undo. Undo does not reverse
database migrations or restore deleted application data. Do not auto-upgrade an
app-store-managed application using a generic Compose replacement. Existing
persistent mount changes require separate analysis, not a routine image update.

Proxy helpers prepare candidates; they do not silently reload Mihomo. Static
release helpers operate only in an explicitly writable release directory; they do
not seize ownership of an existing site or rewrite its 1Panel settings.

## AgentDock adaptation

On AgentDock 1.0.1 use `skill_manage` with `action: install` and a reviewed
source archive. Then call `agentdock_context` and use the exact returned `file`
and `skill_ref`. Command binding uses `exec_command.skill_ref`, not the older
`skill` argument. Configure variables with `skill_manage env_set/env_list`.
Do not hand-copy a package into managed storage or read private environment files.

Managed Skills have one current content digest, not activate/rollback/version
slots. To undo an update, reinstall a reviewed old source snapshot. The version
in this document labels this extension release only. Keep mutable operation state
outside the package; installation must not replace existing environment/data.

If ChatGPT still exposes `skill_package` or the old `exec_command.skill`, refresh
the AgentDock connection and start a new chat. Do not repeatedly call removed
tools or treat an installation attempt as success. See
[docs/guidebook/agentdock-1.md](docs/guidebook/agentdock-1.md).

AgentDock 1.0.1 ACP interactions apply to hosted ACP agents, not this ordinary
Shell/Skill workflow. Do not promise a native permission popup. Use a host-provided
confirmation UI if actually available; otherwise ordinary chat clarification is a
soft workflow check. No custom approval service or extra Coding Agent is required.

The added checks prevent common mistakes, not deliberate bypass by an agent that
already possesses full API credentials. The original administrator CLI remains
available, including `request` and `sign`; do not use them as an escape hatch during
normal assisted operations.

# Retained upstream capabilities

## Overview

Interact with a 1Panel instance through authenticated HTTP API calls. The bundled
TypeScript source is grouped by module, with original query actions implemented
and mutation endpoint definitions retained for future managed workflows.

## Original module groups

- `monitoring`: resource monitoring, current dashboard status, top processes,
  historical monitoring data and GPU history.
- `websites`: website list/detail, Nginx configuration reads, domains, HTTPS,
  SSL certificates and website logs.
- `apps`: application catalog, installed applications, services and port info.
- `containers`: container list/status/detail, inspect, stats and bounded logs.
- `logs`: operation/login logs, system log file lists and line-by-line reads.
- `cronjobs`: task list/detail, next-run previews, records and record logs.
- `task-center`: task records and executing count.
- `nodes`: node lists, options and summaries; some need Pro/XPack.

## Original resources and CLI

- [references/module-groups.md](references/module-groups.md): module boundaries.
- [scripts/client.ts](scripts/client.ts): original authenticated client.
- [scripts/cli.ts](scripts/cli.ts): original administrator CLI.
- [scripts/index.ts](scripts/index.ts): original module registry.
- `scripts/modules/*.ts`: unchanged original actions and reserved definitions.

The existing `modules`, `actions`, `run`, `request` and `sign` commands remain in
`node dist/scripts/cli.js`. Normal packaged use does not rebuild TypeScript.
After changing TypeScript, run `npm run build`; preserve the checked-in `dist/`
files. Reserved mutation definitions do not become tested write workflows merely
because an API path is listed there.
