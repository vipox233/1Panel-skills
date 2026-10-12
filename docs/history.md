# History

## 1.1.2

Live reads identified a second integration error: website configuration type must
be openresty, not nginx. Corrected the operations endpoint and bridge allowlist;
retained the upstream generic query unchanged. The real openresty read succeeds.

## 1.1.1

Installed and configured the managed Skill on AgentDock 1.0.1. Authenticated
container, website and cron queries passed. An idempotent cron status write passed
plan/apply/readback without changing its schedule or script. Fixed existing-site
identities containing a port (for example example.test:443), preserving exact
ID/domain matching and keeping new-site ports in the dedicated field.
Deployment, restart and rollback flows are not claimed live-validated.

## 1.1.0

Added practical scripts for Compose create/update/rebuild, exact container lifecycle,
site/proxy/HTTPS edits, HTTP diagnosis, proxy candidates, cron management and static
releases. Preserved the original client, registry, modules and administrator CLI.

Used small configuration/change records rather than a new service or approval UI.
No volume/database/site deletion, global prune, sudo policy, firewall modification,
backup platform or production-site migration was introduced.

Validation distinguishes local and mock tests, actual Compose parser checks, and
pending authenticated live-panel integration. See docs/testing.md; installation is
not evidence that production write flows have been verified.
