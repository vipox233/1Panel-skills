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

API contracts were prepared from pinned 1Panel v2.3.2 source. Local Swagger required
authentication; credential access was blocked by the calling platform. Authenticated
real-panel reads/writes and destructive fault injection were not performed.

The current chat exposed removed skill_package and the old exec_command.skill
argument, while AgentDock 1.0.1 expects skill_manage and skill_ref. Therefore managed
installation and isolated environment setup were not completed. Refresh the connection
and start a new chat; see docs/guidebook/agentdock-1.md. Do not manually overwrite
managed storage to hide an unsuccessful installation.

When credentials are available through an approved configuration flow, begin with
one read-only query, then use a disposable project/site for create/update/readback,
invalid configuration and restore checks. Do not use production services as test
fixtures, infer production validation from packaging, or bypass a platform denial.
