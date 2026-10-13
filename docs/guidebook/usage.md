# 使用说明

## 平时怎么用

直接提出任务，例如“安装这个 Docker 软件”“把这个站点反代到新端口”
“检查 502”“给这个容器配置代理”“调整用量统计脚本的执行时间”。
AI 负责选资源、校验、生成变更、执行及检查结果，不需要你手工操作 plan ID。

首次接入需要为 Skill 配置 `ONEPANEL_BASE_URL`、`ONEPANEL_API_KEY`；Node 不在
PATH 时再配置 `ONEPANEL_NODE`。API 地址是面板本机地址，不是 Swagger 网页。
使用 AgentDock 的独立环境管理；不要把真实 Key 写入包、源码或聊天记录。

`status` 只检查依赖和配置是否存在，**不代表 API 鉴权通过**。真正联通后先执行
一条只读查询，再开始写入。本版按 1Panel v2.3.2 源码适配；测试分类见
`docs/testing.md`，不能把模拟测试误认为生产面板已经联调通过。

## 运行与查询

在 Skill 包根目录执行：

```bash
printf '%s' '{"skill_action":"status"}' | python3 run.py
printf '%s' '{"skill_action":"query","module":"containers","action":"searchContainers","input":{"page":1,"pageSize":20}}' | python3 run.py
```

原有查询模块全部保留，方法名可用原 CLI 查询：

```bash
node dist/scripts/cli.js modules
node dist/scripts/cli.js actions websites
```

新增入口同时处理 HTTP 状态和业务码。容器日志不无限 follow；常见凭据字段
会脱敏，但任意日志不能仅靠字段名完美脱敏，不公开大段原始输出。

## Compose 安装与更新

先查询项目名、容器名、端口。候选配置写入工作目录，不修改已安装 Skill 包。
输入示例不会自动安装示例软件：

```json
{
  "skill_action": "plan",
  "operation": "compose.create",
  "target": {"name": "example-app"},
  "change": {"content_file": "/your/workspace/compose.yaml", "env": "", "force_pull": false},
  "health": {"url": "http://127.0.0.1:18081/", "codes": [200]}
}
```

拒绝重名覆盖、危险宿主机挂载、特权容器、宿主机 PID/IPC、设备、执行钩子、
任意外部编排文件和未解析变量。支持相对数据目录、命名卷以及明确指定的业务
数据目录。需要绝对路径时用 `options.bind_roots` 指定具体目录；确实需要 host
网络时用 `options.allow_host_network: true`。这些是部署选项，不是管理员认证。

更新使用 `compose.update`；路径由实际库存获取，不接受调用方猜测的路径。
省略 `env` 保留原值；`force_pull: true` 拉取镜像。默认要求 `image@sha256:…` 或本地镜像 ID；
版本标签同样可变，明确选择标签部署时设置 `options.allow_mutable_images: true`。
成功校验记录实际镜像 ID；标签部署不承诺重现相同字节。
已有外部网络需在 `options.allow_networks` 中逐一列出真实名称（如 `1panel-network`），
并显式声明 `external: true`；执行前再次检查网络存在。
已有服务和挂载不会被悄悄移除或替换。`compose.rebuild` 明确停止再重建项目，
会短暂中断但不删卷。应用商店资源不能通过普通 Compose 覆盖。

AI 将返回的 ID 和摘要原样用于：

```json
{"skill_action":"apply","plan_id":"计划ID","plan_digest":"计划摘要"}
```

随后验证：

```json
{"skill_action":"verify","plan_id":"计划ID"}
```

`submitted` 不是完成，`uncertain` 不是没执行。先查任务和资源，不重复 apply。
计划过期或目标变化需要重新读取。运行检查目前面向持续运行的服务；含正常
退出的一次性初始化容器时，不能把它的退出自行解释为成功，需检查任务和日志。

## 单个容器

`container.operate` 的 target 为查询所得 `name` 和完整 `container_id`；change
为 `{"operation":"start"}`、`stop` 或 `restart`。一次只操作精确对象，不支持
remove、kill、prune。不要把数据库或全站重启当作普通排障动作。

## OpenResty / 反代 / HTTPS

先查询实际站点 ID 和域名；已有站点的 target.domain 原样使用返回值（包括可能的 :443），不要自行去掉端口。新建站点仍使用独立 port 字段：

| operation | target | change |
|---|---|---|
| `website.create` | `domain`、`alias` | `type: proxy/static`、已有 `group_id`；反代另给 `upstream` |
| `proxy.create` | `id`、`domain`、`proxy_name` | `upstream`，可选 `match`、`host` |
| `website.config` | `id`、`domain` | `content` 或 `content_file` |
| `proxy.config` | `id`、`domain`、`proxy_name` | `content` 或 `content_file` |
| `openresty.config` | `{}` | `content` 或 `content_file` |
| `website.https` | `id`、`domain` | 已存在的 `certificate_id`，可选 `http_config` |

修改超时、缓冲和 WebSocket 转发时先保存旧配置，再生成候选。普通全文修改
不允许顺带更改站点身份、监听、证书路径、文件映射或加入 Lua/Perl；相关需求
走专用操作，未覆盖项明确说明。

`nginx.validate` 是结构和差异检查，**不是 nginx -t**。1Panel 保存时才执行
实际检查和重载，脚本随后读回。提供 `health` 时还检查 HTTP；确认配置已应用
但健康检查失败时，站点/反代/全局配置更新会尝试写回旧内容，并复查恢复结果。

```json
{"skill_action":"http.check","url":"https://example.test/","resolve_ip":"127.0.0.1","codes":[200,301,302]}
```

`resolve_ip` 保留域名 Host/SNI，适合对比源站与 CDN；不跳过证书验证。
单看 HTTP 200 不能证明整个业务正常。

## 代理

`proxy.compose` 输入 Compose 内容、`service`、`proxy` URL，准备代理环境变量。
桥接容器的 127.0.0.1 不是宿主机；宿主机代理只监听回环时，host-gateway 也不能
使其可达。脚本不擅自切 host 网络或开放端口。

`proxy.chain` 输入 Mihomo 配置、落地节点 `exit_proxy`、跳板/组 `via`，设置
`dialer-proxy` 并查本地循环。不替换订阅、不修改监听、不自动重载。返回的
`candidate_path` 是完整私有候选；`content_preview` 已脱敏，不能用它恢复密钥。
Mihomo 原生校验及真实连通测试仍需执行，YAML 成功不代表代理链已跑通。

## 定时任务与统计脚本

`cron.create` 的 target 是新任务 `name`；change 包含 `spec`、已有 `.py/.sh`
脚本绝对路径 `script`、非 root `user`，以及可选 `args` 数组和 `timeout`。
只注册已审查脚本，不开放任意 root Shell；提交前复查脚本未被替换。

`cron.schedule` 调整已有 shell 任务的 `spec`，保留原用户和脚本；支持中文
任务名。特殊告警配置及其他类型的完整编辑暂不覆盖，避免漏字段覆盖。
`cron.status` 用 `Enable`/`Disable` 启停计划，不删除任务或备份数据。
用量统计脚本继续复用原项目，不复制第二份。

## 静态主页自动部署

`examples/homepage-release.yml` 是供**主页仓库**使用的模板，不是本 Skill 的
活跃工作流。它构建公开静态文件，发布 `site-deploy` release 中的 `site.zip`
和校验值，不能包含服务端密钥或个人数据。

```json
{"skill_action":"static.sync","repository":"owner/homepage","root":"/your/writable/site-release","apply":false}
```

预览通过后 `apply: true`，可将该调用包装为已审查的定时脚本。不需要新增
公网 webhook 或服务器 GitHub Token；下载失败保留现有版本。首次迁移需要
可写的独立目录并将 Web 根目录设为其 `current`；不会自动改目录所有权或接管
生产主页。`static.release` 支持本地 ZIP，要求 `artifact` 与 `sha256`。

旧 release 不删除；共享 `root/data` 接入新版本，保留动态统计。ZIP 中的
越界路径、符号链接、隐藏文件及 data/uploads 被拒绝。`static.rollback`
预览旧版本，加 `apply: true` 原子切回。发布过程不执行 ZIP 内脚本。

## 恢复与常见问题

`record` 查看变更状态；`rollback` 恢复支持的更新。Compose 回退使用先前本地
镜像 ID、`pull_policy: never` 和旧环境；旧镜像被外部清理后不能保证恢复。
不回滚数据库迁移，不通过删除新建资源来撤销。更新若增添服务，旧配置回退
可能需要另行处理新增服务；脚本不会为了回退自动删除它们。

`CONFIG` 表示独立环境缺失；`DEPENDENCY` 表示缺 Node/PyYAML/Compose/curl；
`TARGET_CHANGED` 表示目标变动，需重读；`APP_STORE_RESOURCE` 需要应用专门
升级流程；`API_SHAPE` 需要核对本机 API 文档；`PERMISSION_DENIED` 不会自动
追加 sudo。小配置记录不是云快照替代品，不公开上传整个状态目录。

## 1.2.0 审查修复后的边界

普通 Compose 更新不增减服务，不更换持久挂载；失败任务留下“旧 Compose + 新 env”
时，只有项目身份一致、旧配置匹配、env 正好等于本次候选，才允许恢复保存的配置与镜像。
其他混合状态拒绝覆盖，需先调查。数据库迁移仍不在回滚范围。

`cron.schedule` 先读取完整任务详情，仅支持普通内联 shell 任务；脚本库、专用参数、
secret、通知或其他专用配置非空时拒绝修改，避免全量更新 API 清空字段。
面板在普通 shell 任务中附带的 dbType/isDir 默认字段会原样保留，不因此拒绝调整时间。
Nginx 校验保留 server/location 作用域，不能通过互换配置位置绕过保护。
静态发布复用目录时检查内容和文件集合，篡改、额外文件和非预期链接都会被拒绝。
代理候选仅返回私有文件路径；日志 tail 默认 200、最多 1000 行，响应最多 4 MiB，
超限整次失败，不返回截断成“成功”的内容。

### 任务回执丢失后的恢复

先 `verify`。若回执缺失，不能重试 apply，也不能把等待超时当作成功。
由用户/运维人员确认旧请求与任务已经不可能延迟执行后，才可运行：

```json
{"skill_action":"reconcile","plan_id":"原计划ID","plan_digest":"原计划摘要","acknowledge_no_inflight":true}
```

这项确认必须来自用户，AI 不能根据等待时间自动填写。计划提交至少 5 分钟、
执行中任务数为零、目标完整快照等于操作前，才记录首次观察；至少 60 秒后再查一次。
如回执重新出现或任务仍在运行，清除观察并继续 verify；目标变化则保留记录并拒绝恢复。
两次观察通过才标记 `reconciled_unchanged`，解除旧记录对后续写入的阻塞；不会发出写请求，
也不声称原请求从未执行或部署成功。后续工作必须新建计划。此入口限有任务 ID 的正向
submitted/uncertain 记录；丢失回滚回执等其他不明状态仍需人工调查，不能强制解锁。

升级前的待执行计划应重新生成；不要修改旧计划文件来绕过快照或校验差异。

## 1.2.1 补充

健康检查的 URL、timeout、resolve_ip 和 codes 在写入前校验；探测工具异常会保留
失败记录，可排查后 verify 或 rollback，不会因探测异常自行发出回滚。
`ONEPANEL_OPS_STATE_DIR` 请指向专用目录；已有目录须属于运行用户且权限为 0700，
脚本不会自动修改已有目录权限。不存在的专用目录会自动创建。
切换 HTTP/SOCKS 代理时，候选会移除旧协议的代理变量，保留其他环境变量。

当前未覆盖的正常用法：已有 Compose 端口使用变量插值，或存在未启用的 profile，
可能被拒绝或误报失败。遇到这些情况先查看实际状态，不要重复 apply。
新建站点/代理的 verified 目前只确认资源存在，使用前仍需检查实际访问及配置。
静态旧版本回滚未验证内容完整性；若目录被手工修改，不要直接把目录名当作原包证明。
`examples/homepage-release.yml` 是未启用的滚动发布示例，不代表版本固定。
