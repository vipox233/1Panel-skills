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

## 1.2.1 — 最后一轮 bot 意见取舍

用户要求优先处理实际 P1，避免防御性扩张，本轮后停止跟随审查 bot。
采纳健康检查预检和状态目录权限两项 P1：所有探测参数在计划和执行前校验，
探测程序异常会保存可恢复的失败状态；已有目录不再 chmod，错误路径保持原权限。
顺带采纳代理切换 P2，清理旧 HTTP/SOCKS 大小写变量，保留无关环境设置。

不采纳“撤销客户端扩展”P1：1.2.0 的可选响应上限保持现有调用默认行为，
保留签名/查询实现；为了形式上的零差异另做流量拦截收益不足。本轮未再改客户端。
有意保留这一处最小上游差异，后续同步需关注；不声称上游文件逐字未改。

其余五项 P2 暂缓：旧 Compose 端口插值、未启用 profile 的镜像验证、静态旧版本
回滚完整性、创建站点/代理的逐字段验证、示例滚动 release 改独立标签。
前两项可能拒绝有效配置或误报失败；创建操作当前验证只确认资源存在；静态回滚
不应被当作完整性证明；示例尚未启用，滚动标签不是固定发布版本。
这些限制不扩展为新框架或额外守护进程，今后有实际使用需求再单独处理。

验证：96 项目标主机测试（含真实 Compose 配置解析）、TypeScript 检查和构建；
模拟检查覆盖错误健康参数零写入、探测异常后人工回滚、公共目录权限不变及代理切换。
保持原 PR 未合并。没有生产容器、站点或定时任务配置写入。
