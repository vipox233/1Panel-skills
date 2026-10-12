# Verification boundaries

Run `python3 -m unittest discover -s tests -v` in a complete checkout.

- test_ops.py: local validation, private records, target/drift/replay checks,
  mocked workflows, rollback, archive safety and proxy helpers.
- test_contracts.py: fixture-based image-pinned Compose undo, environment preservation,
  nested HTTPS certificate IDs, Chinese cron names, script drift, rollback rechecks.
- test_bridge.py: the retained real Node client talks to a mock local HTTP server;
  signature/error-envelope/endpoint checks. Missing upstream dist or Node is a skip.
- test_compose_cli.py: real Docker Compose parser, no daemon writes or containers.
  Missing Docker is an explicit skip, never a fabricated syntax-check pass.
- ops-ci.yml builds unchanged TypeScript, runs tests and packages complete skill.zip.
  It does not deploy production services or publish to a Skill marketplace.

## Completed checks

At code commit 37fff2f2cd852db570c05f64725d65f94a3a3760:
- On the target Linux host: npm ci --ignore-scripts, npm run typecheck, npm run build passed.
- 56 tests passed with zero skips, using portable Node 24, Python/PyYAML and the actual
  installed Docker Compose CLI. No production container was started/stopped.
- GitHub Actions Operations checks run 38199560457 completed successfully.
- The retained upstream client, query modules and generated JavaScript were unchanged.
- AgentDock authoring lint reported portable=true, zero errors. The only warning was
  host-specific exec_command usage in the explicit AgentDock adaptation section.

Later documentation-only corrections explain AgentDock 1.0.1's current-content and
skill_ref model. They do not change the tested operations code.

## Live integration and installation

On 2026-10-12 the tool schema was refreshed and the managed Skill was installed
using skill_manage. Isolated configuration was populated through env_set; no key
was printed or committed. Node, PyYAML, Docker Compose and curl checks passed.

Authenticated container, website and cron list queries succeeded. A cron.status
plan/apply/readback preserved the existing Enable state and returned verified.
This is an idempotent API write smoke check, not deployment or rollback coverage.
No container was started/stopped and no site configuration was written.

Existing website identities can include :443. Version 1.1.1 accepts an explicit
valid port for existing sites while retaining exact ID/domain matching. New-site
creation still uses the separate port field.

Actual container creation/update/restart, website writes, fault injection and
rollback remain unverified against the live panel. Use scoped real tasks or
disposable resources for those checks; do not infer coverage from this smoke test.

Version 1.1.2 corrects the website config type to openresty, verified with a real
read. The earlier nginx type returned a business 500 despite HTTP 200.
