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

## Cursor Cloud specific instructions

Node.js 22 is on the image and login shells load nvm. This repository requires Node.js 22 or later. Python 3.12 with PyYAML from `requirements-ops.txt` is the operations runtime.

`docker compose config` is the Compose parser. Install the Docker CLI and Compose plugin, leave the Docker daemon stopped, and leave containers untouched. `ss` from iproute2 supports host port checks.

Offline checks need no 1Panel credentials. Run `npm ci`, `npm run typecheck`, `npm run build`, and `python3 -m unittest discover -s tests -v`. Exercise `node dist/scripts/cli.js modules` and `printf '%s' '{"skill_action":"status"}' | python3 run.py`. API calls need `ONEPANEL_BASE_URL` and `ONEPANEL_API_KEY` in the process environment, never in the repository.
