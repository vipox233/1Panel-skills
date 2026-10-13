# History

## 1.2.0 — PR #1 审查修复

修复回执丢失恢复、关键挂载祖先、cron 全量更新字段、Nginx 作用域、Compose 部分失败回滚、
多行凭据输出、增减服务回滚不对称、静态目录篡改、外部网络、日志上限和可变镜像策略。
保留原查询模块与签名传输，客户端仅增加可选响应字节上限（原调用默认行为不变）。
恢复入口需要真实运维确认，不把超时等同成功；特殊 cron 暂拒绝修改时间；普通更新保持服务集合。
版本镜像标签也可变，默认摘要固定，显式标签部署记录实际镜像 ID。验证证据见 docs/testing.md。
原 PR 更新、不合并；生产服务写入与完整部署/回滚演练未在本轮执行。

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
