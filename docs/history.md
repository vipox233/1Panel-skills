# History

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
