# Development notes

Keep the upstream TypeScript client, module registry, query modules and CLI intact.
Added Python workflows call the original client through scripts/ops-bridge.mjs.
Do not duplicate signing or replace this project with another management platform.

Run python3 -m unittest discover -s tests -v, npm ci, npm run typecheck, npm run build.
Actual Compose parser tests must use docker compose config, without creating or
removing production containers. Keep all test credentials explicitly artificial.

Distinguish local/unit, mock transport, host parser, and authenticated live checks.
Do not claim a mock response is a deployment. Never commit real configs, state or
logs. Deploy immutable versioned packages and verify the active installed version.

Do not add a second service, generic sudo wrapper, delete/prune/clear-data tool,
backup platform or firewall modification without a separate user request.
