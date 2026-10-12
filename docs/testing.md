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

## Live integration

API contracts were prepared from pinned 1Panel v2.3.2 source. Local Swagger required
authentication; credential access was blocked by the calling platform. Authenticated
real-panel reads/writes and destructive fault injection were not performed.

When credentials are available through an approved configuration flow, begin with
one read-only query, then use a disposable project/site for create/update/readback,
invalid configuration and restore checks. Do not use production services as test
fixtures, infer production validation from package installation, or bypass a
platform denial to complete integration.
