# Operations contract and primary sources

## Reuse boundary

Original client, CLI, registry, eight query modules and reserved mutation definitions
are retained. The Python workflows use scripts/ops-bridge.mjs to import the original
built client. No second signing implementation or server is introduced.

Baseline: 1Panel-skills commit 33d74c9502e7ae509d8c1d7f3f1c78fd13ac23c6.
Write schemas were inspected in 1Panel tag v2.3.2, not inferred from endpoint names.

## Primary sources

- https://github.com/1Panel-dev/1Panel/blob/v2.3.2/agent/app/dto/container.go
- https://github.com/1Panel-dev/1Panel/blob/v2.3.2/agent/app/service/container_compose.go
- https://github.com/1Panel-dev/1Panel/blob/v2.3.2/agent/app/service/container.go
- https://github.com/1Panel-dev/1Panel/blob/v2.3.2/agent/app/dto/request/website.go
- https://github.com/1Panel-dev/1Panel/blob/v2.3.2/agent/app/dto/response/website.go
- https://github.com/1Panel-dev/1Panel/blob/v2.3.2/agent/app/dto/request/nginx.go
- https://github.com/1Panel-dev/1Panel/blob/v2.3.2/agent/app/service/nginx.go
- https://github.com/1Panel-dev/1Panel/blob/v2.3.2/agent/app/service/website_proxy.go
- https://github.com/1Panel-dev/1Panel/blob/v2.3.2/agent/app/dto/cronjob.go
- https://github.com/1Panel-dev/1Panel/blob/v2.3.2/agent/app/service/cronjob_helper.go
- https://docs.fit2cloud.com/1panel/dev_manual/api_manual/
- https://docs.docker.com/reference/cli/docker/compose/config/
- https://wiki.metacubex.one/config/proxies/dialer-proxy/

## Non-obvious contracts

HTTP 200 may contain business 401. Check both layers. Compose creation uses from=edit,
file, env, name, dirName, taskID. Update needs inventory-derived path and detailPath;
these calls submit asynchronous tasks. Container inspect is a JSON string of Docker
inspection data, including current image ID and Compose project/service labels.
TestCompose is not a complete safe-Compose validator; use the actual Compose parser.

Website config updates use id; proxy-file updates use websiteID. HTTPS reads contain
SSL.id whereas writes use websiteSSLId; never copy certificate private keys into
reports. OpenResty /file writes content, checks nginx and reloads, with backup support;
it is not a side-effect-free preview API. Shell cron executor is the interpreter
(such as bash), not the string host; the supplied execution user must be preserved.

## Approval boundary

AgentDock v1.0.1 ACP tools handle adapter/session interactions:
https://github.com/uvwt/agentdock/blob/v1.0.1/internal/app/specs_acp.go
They are not generic permission prompts for ordinary exec_command or Skill calls.
Use additional caller confirmation UI only when actually available. A plan digest
is not human authentication, and chat confirmation is a soft workflow check.

This is accident prevention, not hard API confinement. A retained administrator CLI
and a full API key can bypass workflow scripts; do not claim IAM-like guarantees.
