# AgentDock 1.0.1 接入说明

本扩展的 1.1.0 是项目发布编号，不是 AgentDock 的安装版本槽。AgentDock 1.0.1
以 content digest 管理同名 Skill 的当前内容；更新用重新安装，恢复用经过审查的
旧源码快照，不调用已移除的 activate/rollback。

## 工具定义刷新

升级服务器之后，旧 ChatGPT 会话可能仍有旧工具缓存。若出现
`unknown tool "skill_package"`，或 exec_command 报 `skill` 字段不接受，
先在 ChatGPT 的 AgentDock 连接页面刷新工具定义，再新开对话。
不要继续调用旧接口，也不需要为此重启生产容器。

刷新后应看到 `skill_manage`；运行 Skill 时用 `exec_command.skill_ref`。
具体引用必须来自 `agentdock_context` 返回值，不按名称猜目录。
服务器中遗留的旧版内置指南可能仍使用旧名称，核对 1.0.1 源码为准。

## 安装与配置

通过新版工具执行（下面是工具参数示意，不是 Shell 命令）：

```json
{"action":"install","source":"实际 skill.zip 路径","digest":"该压缩包的 SHA-256"}
```

检查返回的 `skill`、`content_digest`、`changed`，然后重新读取上下文确认来源。
通过 `skill_manage env_set` 配置 `ONEPANEL_BASE_URL`、`ONEPANEL_API_KEY`；Node
不在 PATH 时配置 `ONEPANEL_NODE`。真实 Key 只进入独立环境，不写入本仓库。
安装后以精确 skill_ref 执行 `python3 run.py`，stdin 为
`{"skill_action":"status"}`，再运行一个原模块只读查询检查真正的 API 鉴权。

恢复旧内容是重新安装经过审查的旧包，不影响 Skill 独立环境或运行记录。

## 本次交付的边界

源码已在真实服务器上通过 TypeScript 类型检查、构建和 56 项自动测试，包含
真正的 Docker Compose 解析及原客户端与模拟 HTTP 服务交互；GitHub CI 通过。
这些测试没有创建或中断生产容器。

当前会话缓存的工具定义不包含新版安装工具，旧安装调用失败；真实 API Key
读取也被调用平台拦截。因此本次不能把打包成功说成已完成 managed 安装、密钥
配置或真实 API 写入验收。刷新连接后，从本节继续，不重做已有实现。

## 许可机制

ACP 的 acp_interaction 处理托管 Coding Agent 的权限交互，不是普通 Skill/Shell
的通用审批弹窗。本扩展不新增审批 UI，不为了审批引入其他 Coding Agent。
正常任务内由脚本校验后执行；越出任务范围时沟通确认，危险删除能力不提供。

核对来源：
https://github.com/uvwt/agentdock/blob/v1.0.1/core-skills/skill-installation/SKILL.md
https://github.com/uvwt/agentdock/blob/v1.0.1/internal/app/specs_acp.go
