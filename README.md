# Agent Bridge

从手机/桌面飞书远程查看与控制本服务器上多个 agent CLI（GitHub Copilot、Claude Code、Codex）的会话——像操作一个在线 AI 应用。一个页面里切换/同屏管理多个 agent 的对话，支持双向实时同步。

- 需求 `docs/REQUIREMENTS.md` · 架构 `docs/DESIGN.md` · 计划 `docs/PLAN.md`

## 架构

```
LAN 服务器 (bridge, Python, 纯出站)  ──lark-cli apps +db-execute──▶  飞书妙搭托管 Postgres
   index / tail / inject                                                  ▲
   Copilot · Claude · Codex 适配器                                         │
                                                                          │
手机/桌面飞书 ──打开妙搭 app──▶ React(NestJS) 读 DB 渲染聊天 UI + 发指令
```

- bridge 纯出站，无公网 IP / 无穿透；手机任意网络在飞书内打开。
- 支持的 agent：Copilot CLI、Claude Code CLI、Codex CLI。新增 agent 只需实现 `bridge/agents/base.py` 接口。
- 注入：live 会话（tmux 里）走 `tmux send-keys` 键入打开的终端（双向实时）；离线会话走各 CLI 的 headless resume（追加到 transcript）。

## 仓库结构

```
bridge/        Python 桥（index/tail/inject + agent 适配器）
  agents/      base 接口 + copilot/claude/codex 适配器 + live 检测
app/           飞书妙搭 full_stack 应用（NestJS + React + Drizzle + 托管 Postgres）
scripts/       守护进程编排（bridge-start/stop/status.sh, systemd unit）
docs/          设计/需求/计划
.env.example   需填的环境变量（复制为 .env.local）
```

## 前置条件

服务器（LAN，纯内网）：
- `lark-cli`（已用某 profile 授权 `spark:app:read/write`）
- agent CLI：`copilot`、`claude`、`codex`（任选其一或全部）
- `tmux`、`sqlite3`、Python 3.10+、Node 22+（妙搭 app）

飞书侧：
- 一个妙搭 full_stack app（本仓库 `app/` 目录的代码）。

## 配置

```bash
cp .env.example .env.local
# 填入：COPILOT_BRIDGE_APP_ID（妙搭 app id）、COPILOT_BRIDGE_PROFILE（lark-cli profile）、
#       COPILOT_BRIDGE_ALLOW_OPEN_IDS（允许发指令的飞书 user_id，见 .env.example 说明）
```

## 运行

```bash
# 守护进程（index 每 60s / tail 全 session 实时 / inject 轮询指令）
./scripts/bridge-start.sh
./scripts/bridge-status.sh
./scripts/bridge-stop.sh

# 一次性快照
./scripts/bridge-start.sh --once

# 单步
python -m bridge index              # session 索引 → sessions 表
python -m bridge tail --all         # 实时 tail 所有 session events
python -m bridge inject --once      # 消费待办指令
python -m bridge events --session <id>
python -m bridge ls                 # 查 sessions 表
```

systemd：`scripts/copilot-bridge.service`（改 User/WorkingDirectory 后 `systemctl link` 启用）。
日志在 `~/.copilot-bridge/logs/`，offset 状态在 `~/.copilot-bridge/bridge-state.db`。

## 飞书应用（`app/`）

- 在妙搭控制台创建 full_stack app，把 `app/` 代码作为源码。
- 改前端/后端：编辑 `app/`，`git push` + `lark-cli apps +release-create` 发布（~90s，release 自动迁移 schema 到 online）。
- DB 变更：dev 用 `db-execute` DDL → `npm run gen:db-schema` → release 迁移 online（online 禁 DDL）。
- 构建：`CLIENT_BASE_PATH=/app/<app_id> npm run build:server && npm run build:client`。

## 安全

- bridge 写 DB 用 `lark-cli`（出站，无监听端口）。
- 指令注入受 `COPILOT_BRIDGE_ALLOW_OPEN_IDS` 白名单闸门（飞书 user_id）。
- `.env.local`、本地 state（sqlite/日志）均已 gitignore，勿提交。
- license：MIT（见 `LICENSE`）。
