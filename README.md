# AgentCLI Bridge

从手机/桌面飞书**远程查看与控制**局域网服务器上多个 agent CLI（GitHub Copilot、Claude Code、Codex）的会话——像操作一个在线 AI 应用。一个页面里切换管理多个 agent 的对话，双向实时同步。

> 服务器不需要公网 IP、不需要端口穿透、不需要自建鉴权/数据库。手机在任意网络下打开飞书即可。

---

## 解决什么痛点

你在内网服务器（开发机/训练机）上用命令行 agent（Copilot / Claude Code / Codex）写代码、跑任务，会面临一组矛盾：

- **人是移动的，机器是固定的。** 训练跑了几小时、agent 在终端里继续工作，你却必须回到工位盯屏幕才能看进度、回话。下班/出门后基本失联。
- **多个 agent、多个项目目录、多个 session 散落在各终端窗口里。** 想找"刚才那个会话"要翻 tmux/终端历史，没有统一视图。
- **想从手机发一句话让 agent 继续，却没有现成通道。** 手机直接 SSH 进内网服务器不现实（无公网 IP、企业网隔离）。
- **现有官方远程方案不通用、不合规。** Copilot `--remote` 走 GitHub 自家 App、Claude/Codex 各有各的云，彼此不统一，且有些在企业内网不通。
- **自建 Web 服务把服务器暴露到公网风险大、要备案、要 HTTPS、要自己写鉴权。** 对个人开发者是负担。

**AgentCLI Bridge 的目标**：把"内网服务器上的 agent 会话"镜像到一个托管在飞书云的应用里，手机打开飞书就能像刷聊天一样看进度、发指令。不暴露服务器、不自建后端、不碰各 agent 的云账号。

---

## 架构设计

```
   ┌──────────── 内网服务器 (LAN，无公网IP、不可穿透) ────────────┐
   │                                                              │
   │  [Bridge 守护进程]  (Python，纯标准库，常驻)                   │
   │   ┌──────────┬───────────┬──────────────┐                   │
   │   │ Indexer  │  Tailer   │  Injector    │                   │
   │   │ 会话索引  │ 对话事件流 │ 指令注入      │                   │
   │   └────┬─────┴─────┬─────┴───────┬──────┘                   │
   │        │ 读 ↓      │ 读 ↓       │ 写 ↓                      │
   │   各 agent 的本地文件（events.jsonl / session-store.db /     │
   │     ~/.claude/projects/*/*.jsonl / ~/.codex/sessions/...）    │
   │   注入：live 会话 → tmux send-keys；离线 → 各 CLI headless   │
   │                                                              │
   │   全部经 lark-cli apps +db-execute（出站 HTTPS，无监听端口）   │
   └──────────────────────────┬───────────────────────────────────┘
                              │ 仅出站
                              ▼
   ┌──────────── 飞书云 (妙搭 aPaaS，托管) ──────────────────────┐
   │  妙搭 full_stack 应用 (NestJS + React + Drizzle)             │
   │   GET /api/sessions   会话列表                                │
   │   GET /api/events     对话事件（按 session，轮询）            │
   │   POST /api/commands  发指令（写 commands 表）               │
   │   托管 Postgres：sessions / events / commands / renames      │
   │   平台注入：DB 凭证 + 飞书用户身份（req.userContext）         │
   └──────────────────────────┬───────────────────────────────────┘
                              │ 手机/桌面飞书客户端打开
                              ▼
                      飞书工作台 / 应用 URL
```

**三个角色**：

1. **Bridge 守护进程**（你的服务器，Python，纯标准库无第三方依赖）：三个循环——
   - **Indexer**（每 60s）：扫各 agent 的本地 session 索引，把 id/cwd/摘要/在线状态 upsert 进妙搭 `sessions` 表。
   - **Tailer**（实时，全 session）：按 byte offset 增量读各 agent 的 append-only 事件日志，解析成统一格式，批量写进 `events` 表。零侵入（只读文件）。
   - **Injector**（每 2s 轮询）：取 `commands` 表里未消费的指令，按目标 session 是否在线二选一注入——live 会话用 `tmux send-keys` 键入打开的终端（双向实时），离线会话用各 CLI 的 headless resume（追加到同一 transcript）。

2. **妙搭 full_stack 应用**（飞书云托管，仓库 `app/`）：NestJS 后端用平台注入的 DB 凭证读写同一 Postgres；React 前端渲染 ChatGPT 式聊天 UI（会话列表 + 对话详情 + 指令输入 + 语音输入 + 上下文用量 + 在线状态 + terminal 提示回灌）。

3. **飞书客户端**：手机/桌面打开飞书 → 工作台 → 这个应用，即看到所有 agent 会话。

**数据流**只经一个中继：托管 Postgres。Bridge 写（出站），应用读+写（平台内），手机读应用。没有任何方向需要从外部主动连服务器。

### 多 agent 适配器

`bridge/agents/base.py` 定义统一接口（`list_sessions` / `events_path` / `map_event` / `is_turn_complete` / `inject_online` / `live_pane` …），`copilot.py` / `claude.py` / `codex.py` 各自实现。新增一个 agent CLI 只需实现这个接口。

---

## 架构优势

| 优势 | 说明 |
|---|---|
| **无需公网 IP / 无端口穿透** | Bridge 纯出站（`lark-cli` HTTPS 长连），不开任何监听端口。内网/企业网/NAT 后都能用，不碰防火墙入站策略。 |
| **无需自建 Web/鉴权/数据库** | 应用和 DB 托管在飞书妙搭云；DB 凭证与用户身份由平台运行时注入，前端不持密钥，不用自己写登录/OAuth/HTTPS/备案。 |
| **手机任意网络可用** | 走飞书客户端本身的长连接，4G/5G/任意 Wi-Fi 都行，与服务器是否在内网无关。 |
| **零侵入读** | 读方向只读 agent CLI 本地产生的文件（events.jsonl / transcript），不修改 agent 行为、不打补丁。终端里发起的对话同样被捕获。 |
| **多 agent 统一** | Copilot / Claude / Codex 一个界面管理，同一套会话列表 + 对话视图 + 指令通道，新增 agent 只改一个适配器。 |
| **双向实时** | live 会话走 tmux send-keys，手机发的指令真正键入正在运行的终端，agent 的流式输出即时回灌页面；离线会话走 headless resume 追加 transcript。 |
| **崩溃可恢复** | Tailer 的 byte offset 持久化在本地 sqlite；进程崩了重启从断点续读，事件 id 是稳定 hash（幂等 INSERT ON CONFLICT），不丢不重。 |
| **指令来源受控** | 注入带 `--allow-all-tools` 等高危标志，仅 `COPILOT_BRIDGE_ALLOW_OPEN_IDS` 白名单内的飞书用户能发指令。 |
| **可观测** | 本地日志 + 妙搭 release 历史 + DB 表可直接查；`python -m bridge ls/lock/events` 一条命令体检。 |

> 代价：依赖飞书妙搭平台（个人/小团队免费额度通常够用；适合个人开发者与内部小团队，不适合禁用飞书的组织）。

---

## 快速开始

```bash
git clone <this-repo> agentcli_bridge && cd agentcli_bridge
./install.sh
```

`install.sh` 一键完成：装系统依赖 → 装 lark-cli 并扫码登录飞书 → 检测 agent CLI → 创建妙搭应用并推送代码、建表、发布 → 生成 `.env.local` → 装 systemd 服务并冒烟测试。**唯一需要你动手的是用飞书 App 扫一次码授权 lark-cli。**

详见 **[docs/INSTALL.md](docs/INSTALL.md)**（人工步骤详解）与 **[docs/INSTALL_FOR_AGENT.md](docs/INSTALL_FOR_AGENT.md)**（交给一个 AI agent 帮你装的说明）。

> 不想跑脚本？`docs/INSTALL.md` 有完整的人工分步操作；`docs/INSTALL_FOR_AGENT.md` 是写给 agent 看的，丢给 Claude Code / Copilot 让它在本机替你执行。

---

## 仓库结构

```
bridge/        Python 桥（index/tail/inject + agent 适配器，纯标准库）
  agents/      base 接口 + copilot/claude/codex 适配器 + live 进程检测
app/           飞书妙搭 full_stack 应用（NestJS + React + Drizzle + 托管 Postgres）
scripts/       守护进程编排（bridge-start/stop/status.sh）+ systemd 模板
db/schema.sql  妙搭托管 Postgres 建表 DDL（install.sh 在 dev 库执行）
install.sh     一键安装器
docs/          安装与设计文档
.env.example   环境变量样板（复制为 .env.local）
```

## 运行（日常）

```bash
./scripts/bridge-start.sh           # 守护进程：index 60s / tail 实时 / inject 轮询
./scripts/bridge-status.sh
./scripts/bridge-stop.sh
./scripts/bridge-start.sh --once    # 一次性快照

python -m bridge index              # 单步：session 索引 → sessions 表
python -m bridge tail --all         # 单步：tail 所有 session events
python -m bridge inject --once      # 单步：消费待办指令
python -m bridge ls                 # 查 sessions 表
python -m bridge lock               # 查当前 live 在线 session
python -m bridge events --session <id>   # 查某 session 的事件
```

日志在 `~/.copilot-bridge/logs/`，offset 状态在 `~/.copilot-bridge/bridge-state.db`。

## 改飞书应用（`app/`）

- 改前端/后端 → `cd app && CLIENT_BASE_PATH=/app/<app_id> npm run build:client` → `git push origin sprint/default` → `lark-cli apps +release-create --profile <p> --as user --app-id <id> --branch sprint/default` → 轮询 `+release-get` 至 finished。
- DB 变更：dev 用 `lark-cli apps +db-execute --env dev` DDL → `npm run gen:db-schema` → release 自动迁移到 online（online 禁 DDL）。

## 安全

- Bridge 写 DB 走 `lark-cli`（出站，无监听端口），不暴露 DB 凭证到服务器进程环境以外。
- 指令注入带高危标志（`--allow-all-tools` / `--dangerously-skip-permissions` 等），受 `COPILOT_BRIDGE_ALLOW_OPEN_IDS` 白名单闸门——仅指定飞书用户能发指令。
- `.env.local`、本地 state（sqlite/日志/pid）均已 gitignore，勿提交。
- License：MIT（见 `LICENSE`）。

## 已知限制

- 强依赖飞书妙搭平台；禁用飞书的组织不适用。
- Codex CLI 的中间对话回合没有"回合结束"事件，发送锁对其靠"idle 静默"兜底判定（不如 Claude/Copilot 精确）。
- 注入离线会话用 headless resume，是追加一轮而非真正"接管"终端；live 会话才支持真正的双向实时键入。

## 开发文档

> 🌐 **可视化版**：所有文档也有一个带侧边栏导航、代码高亮、明暗主题的单页 HTML 版本，直接用浏览器打开 [`docs_html/index.html`](docs_html/index.html) 即可（无需联网，自包含）。源 markdown 仍在原位，改完跑 `python3 docs_html/build.py` 重新生成。

- [docs/INSTALL.md](docs/INSTALL.md) — 人工安装详解
- [docs/INSTALL_FOR_AGENT.md](docs/INSTALL_FOR_AGENT.md) — 让 agent 帮你安装
- [docs/DESIGN.md](docs/DESIGN.md) — 架构设计原档
- [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) — 需求与用户故事
- [docs/PLAN.md](docs/PLAN.md) — 实施里程碑
