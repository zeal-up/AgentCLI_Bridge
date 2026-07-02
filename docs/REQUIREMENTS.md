# 需求（Requirements）

## 背景

本服务器上运行着 GitHub Copilot CLI（`copilot`，v1.0.65），日常会在多个项目目录下开多个 session 与 agent 对话。希望像腾讯 WorkBuddy 一样，用手机远程查看并控制这些 session。

## 核心用户故事

1. **看列表**：在手机上能看到本机当前/历史的各个 Copilot session（项目目录、摘要、最后活跃时间、是否在线）。
2. **同步对话（读方向）**：
   - 在服务器终端里用 agent 对话时，手机端能**实时**看到同一 session 的对话进度（用户消息、assistant 回复、工具调用/结果）。
   - 双向一致：不管对话是从服务器发起还是从手机发起，两端看到的历史一致。
3. **发指令（写方向）**：在手机上选中某个 session，发送文本指令，让该 session 的 agent 继续执行，并把结果实时回传到手机。

## 平台选择

- **一期只做飞书（Lark），交互形态为"飞书妙搭 full_stack 应用 + DB 中继"**。理由：本机纯内网、无公网 IP、不能穿透，妙搭 app 托管在飞书云、bridge 纯出站经 `lark-cli apps +db-execute` 写其托管 Postgres，手机任意网络在飞书内打开即可（spike 已端到端验证，见 DESIGN.md）。本机已装 `lark-cli`（v1.0.59，hermes profile 已授权 `spark:app:read/write`）。
- **不做微信控制**。个人微信自动化违反 ToS 有封号风险；小程序正式版需 ICP 备案域名+HTTPS（个人自用仅体验版+调试模式可绕，但每次开调试、且 bridge 要被手机连）。微信最多后续做"只读通知"（服务号），不纳入一期。

## 功能需求

| 编号 | 需求 | 优先级 |
|---|---|---|
| FR-1 | 列出 session（id/cwd/summary/最后活跃/在线状态） | P0 |
| FR-2 | 实时同步指定 session 的对话进度到飞书 | P0 |
| FR-3 | 从飞书向指定 session 发送指令并回传结果 | P0 |
| FR-4 | 区分"在线活跃 session"与"离线 session"，选择合适的写入方式 | P0 |
| FR-5 | 权限白名单：仅允许指定飞书用户(open_id)控制 | P0 |
| FR-6 | 长输出/工具结果的截断与格式化 | P1 |
| FR-7 | 断线重连、增量续读（按 offset） | P1 |
| FR-8 | 会话与飞书群/话题的映射管理 | P1 |
| FR-9 | 微信只读通知（服务号） | P2（暂不做） |

## 非功能需求

- **零侵入读**：读方向只读文件，不修改 Copilot CLI 行为。
- **低延迟**：入站指令到 agent 执行 < 2s；对话事件到手机 < 2s。
- **鲁棒**：Bridge 进程崩溃可重启并从 offset 续读，不丢事件。
- **安全**：远程指令携带 `--allow-all-tools`，必须严格限制来源用户；敏感输出可脱敏。
- **可纯内网运行**，不依赖公网入口。

## 约束与已知事实（已在本机实测）

- 每个 session：`~/.copilot/session-state/<id>/events.jsonl`（append-only 实时事件流）。
- 全局索引：`~/.copilot/session-store.db`（`sessions`/`turns`/`checkpoints`/`session_files` 等表）。
- 在线判定：`session-state/<id>/inuse.<pid>.lock`。
- 写入 session：`copilot -p "<指令>" --resume <id> --output-format json --allow-all-tools`（离线 session 干净恢复上下文，已验证）。
- 活跃 session 若要边终端边手机续用，用 `tmux send-keys` 注入（避免 `--resume` 与活进程冲突）。
- 官方原生远程能力：`copilot --remote`（GitHub web/mobile 控制）、`--acp`（ACP/JSON-RPC server）、`--connect=<id>`——走 GitHub 自家 App，不适配飞书，仅作参考/备选。
- 本机已存在 `feishu-remote` skill（单 session 双向回路已跑通），可作为写方向参考实现。
