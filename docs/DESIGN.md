# 架构设计（Design）

> 2026-07-01 修订：交互形态从"飞书群+话题文本命令"改为"飞书妙搭 full_stack 应用 + DB 中继"。该架构已用 spike 端到端验证（见文末"spike 验证记录"）。

## 一句话

一个跑在 LAN 服务器上的 **Bridge 守护进程**（出站 only）把本机 Copilot session 的对话事件写进**飞书妙搭托管的 Postgres**；一个部署在飞书云上的**妙搭 full_stack 应用**（NestJS+React）读同一个 DB，在飞书客户端里渲染成"像操作软件"的会话管理界面；手机端发指令写回 DB，Bridge 轮询取出注入 Copilot。

## 总体架构

```
   ┌──────────────── 服务器 (LAN, 无公网IP, 不能穿透) ────────────────┐
   │                                                                  │
   │  [Bridge 守护进程]  (Python, 常驻)                                │
   │   ┌──────────┬───────────┬──────────────┐                       │
   │   │ Indexer  │  Tailer   │  Injector    │                       │
   │   │ (sessions│ (events   │ (commands    │                       │
   │   │  表)     │  表)      │  轮询)       │                       │
   │   └────┬─────┴─────┬─────┴───────┬──────┘                       │
   │        │ 读 ↓       │ 读 ↓       │ 写 ↓                         │
   │   session-store.db  events.jsonl  copilot -p --resume / tmux     │
   │   inuse.<pid>.lock                                                │
   │                                                                   │
   │   全部经 lark-cli apps +db-execute （出站 HTTPS）                 │
   └──────────────────────────┬───────────────────────────────────────┘
                              │ 出站 only（无入站端口）
                              ▼
   ┌──────────────── 飞书云 (妙搭 aPaaS) ────────────────────────────┐
   │                                                                  │
   │  妙搭 full_stack app  (app_id: 见实施)                            │
   │   ┌─────────────────────────────────────────────┐                │
   │   │ NestJS 后端 (Drizzle ORM)                    │                │
   │   │   GET /api/sessions   → sessions 表          │                │
   │   │   GET /api/events     → events 表 (按 session)│                │
   │   │   POST /api/commands  → commands 表           │                │
   │   │   (可选) GET /api/stream → SSE 推新事件       │                │
   │   ├─────────────────────────────────────────────┤                │
   │   │ React 前端 (axiosForBackend)                 │                │
   │   │   会话列表页 / 对话详情页 / 指令输入          │                │
   │   └─────────────────────────────────────────────┘                │
   │   托管 Postgres:  sessions / events / commands 表                │
   │   平台注入: DB 凭证(DRIZZLE_DATABASE) + 用户身份(req.userContext) │
   └──────────────────────────┬──────────────────────────────────────┘
                              │ 手机/桌面飞书客户端打开
                              ▼
                      飞书工作台 / URL
```

**关键性质**：bridge 纯出站（`lark-cli` 长连/HTTPS），无入站端口、无公网 IP、无穿透；手机任意网络打开飞书即可；前端不持密钥（平台注入 DB 凭证），无需自建鉴权后端（平台注入飞书 open_id）。

## 两个部分

### A. Bridge 守护进程（LAN 服务器，Python）

四个组件（保留原 DESIGN 职责，输出端从"飞书群消息"改为"妙搭 DB 表"）：

1. **Indexer（会话索引）**
   - 读 `~/.copilot/session-store.db` 的 `sessions` 表 → id/cwd/summary/updated_at。
   - 扫 `session-state/*/inuse.<pid>.lock` 校验 pid 存活 → 在线状态。
   - 全量/增量 upsert 进妙搭 `sessions` 表（`lark-cli apps +db-execute`，INSERT ... ON CONFLICT）。

2. **Tailer（读方向，实时同步对话）**
   - 对被"关注"的 session，tail `events.jsonl`，按 **byte offset** 增量读（offset 持久化在本地，不进 DB）。
   - 解析事件 → 格式化为行 → 批量写进妙搭 `events` 表（按 ~1s 聚批，一条 `db-execute` 多行 INSERT，降 subprocess 次数）。
   - 零侵入：只读文件，服务器终端里发起的对话同样被捕获。

3. **Injector（写方向，发送指令）**
   - 轮询妙搭 `commands` 表（`db-execute SELECT ... WHERE consumed=false`，~1-2s 一次）。
   - 拿到未消费指令 → 按 lock 状态二选一：
     - 离线 session：`copilot -p "<指令>" --resume <id> --output-format json --allow-all-tools`
     - 在线活跃 session（tmux 托管）：`tmux send-keys -t <target> "<指令>" Enter`
   - 标记 commands 行 consumed + 写入对应 session 的 events（resume 的 JSONL 输出复用 Tailer 格式化逻辑）。

4. **Mapper/权限**（轻量）
   - 飞书用户身份由平台 `req.userContext` 注入，前端发指令时 open_id 随 commands 行入库；Bridge 侧校验 open_id 白名单（复用 feishu-remote 的 pin）。
   - session ↔ 飞书用户的关联在前端处理（列表页选 session），无需群/话题映射。

### B. 妙搭 full_stack 应用（飞书云，NestJS+React+Drizzle）

- **后端**：NestJS 路由读 `sessions`/`events` 表（Drizzle），平台注入 `DRIZZLE_DATABASE`。可选 SSE（`GET /api/stream?session_id=`）把新事件推前端（准实时，优于前端轮询）。
- **前端**：React（`axiosForBackend`，自动带 `CLIENT_BASE_PATH` 前缀+CSRF+凭证）。会话列表页 → 点进对话详情页（事件流）→ 底部输入框发指令（POST `commands` 表）。
- **DB schema**：见下。
- **鉴权**：平台自动（飞书 open_id），无需自建。访问范围设 tenant 或 specific(白名单 open_id)。

## 数据库 schema（妙搭托管 Postgres）

```sql
-- 会话索引
CREATE TABLE sessions (
  id           TEXT PRIMARY KEY,        -- copilot session id
  cwd          TEXT,
  summary      TEXT,
  updated_at   TEXT,                     -- ISO 字符串
  online       BOOLEAN DEFAULT FALSE,    -- pid 存活
  pid          INT,
  indexed_at   TEXT                      -- bridge 最近一次刷新时间
);

-- 对话事件流（一行 = 一个 event）
CREATE TABLE events (
  seq          BIGINT PRIMARY KEY,       -- 全局自增或 snowflake
  session_id   TEXT NOT NULL,
  type         TEXT,                      -- user.message/assistant.message/tool.*/result
  role         TEXT,                      -- user/assistant/tool
  content      TEXT,                      -- 格式化后的人类可读文本
  raw          TEXT,                      -- 原始 JSON（可选，用于回显结构）
  ts           TEXT,                      -- 事件 timestamp
  turn_id      TEXT
);
CREATE INDEX idx_events_session ON events (session_id, seq);

-- 指令队列（前端写、bridge 消费）
CREATE TABLE commands (
  id           BIGINT PRIMARY KEY,
  session_id   TEXT NOT NULL,
  content      TEXT NOT NULL,
  sender_open_id TEXT,                   -- 发指令的飞书用户
  created_at   TEXT,
  consumed     BOOLEAN DEFAULT FALSE,
  consumed_at  TEXT,
  result       TEXT                       -- 可选：执行结果摘要回写
);
CREATE INDEX idx_commands_pending ON commands (consumed) WHERE consumed = false;
```

> 建表流程（实测）：dev 用 `db-execute` DDL → `fullstack-cli gen-db-schema` 反生成 `schema.ts` → **release 时平台自动迁移到 online**（online 禁 DDL，DML 允许）。DML（INSERT/SELECT/UPDATE）两端都允许。

## 实时性策略

- **读方向（事件 → 前端）**：Tailer ~1s 聚批写库；前端用 SSE（NestJS 推）或 1-2s 轮询 `GET /api/events?since=<seq>`。SSE 优先（同源、平台注入身份可用）。
- **写方向（指令 → bridge）**：前端 POST `commands` 表（即时）；Bridge 1-2s 轮询未消费指令。延迟可接受（非实时控制场景）。
- 如需更低延迟，bridge 侧可用 `lark-cli event consume im.message.receive_v1` 让前端改发飞书消息推指令（push），但一期用轮询足够。

## 为什么是这个方案

| 备选 | 否/缓理由 |
|---|---|
| 飞书群+话题文本命令（原 A） | 交互像聊天机器人，非"操作软件"；已放弃。 |
| 微信小程序 | 正式版要 ICP 备案域名+HTTPS；个人自用仅体验版+调试模式可绕，但每次开调试、且 bridge 要被手机连。一期不做。 |
| 飞书 H5 + bridge 作 HTTP/WS 服务端 | 体验最好，但 bridge 要被手机直连 → 需公网/穿透，违反"纯内网"。 |
| 官方 `--remote`/`--acp` | 绑 GitHub App，不适配飞书；`--acp` 适合自建前端深度集成但更重。列为后续备选。 |
| **妙搭 full_stack + DB 中继（本方案）** | bridge 纯出站、无公网 IP；前端真应用 UI、平台托管鉴权与 DB；spike 已验证全链路。 |

## 风险与对策

| 风险 | 对策 |
|---|---|
| `db-execute` 每次 subprocess 开销 | Tailer 聚批（~1s 一条多行 INSERT）；Injector 轮询用单条 SELECT。监控 QPS。 |
| online DB DDL 禁止 | dev 建表 + gen-db-schema + release 迁移；schema 变更走 release。 |
| `db-execute` SELECT 也要 `--yes` | bridge 调用统一带 `--yes`。 |
| database_url 过期（env-pull 有 expires_at） | NestJS 侧用平台注入的连接（自动刷新）；bridge 侧走 `db-execute`（经 spark API，不直连 DB URL），不受影响——M1 验证。 |
| events 表膨胀 | 按 session 归档/清理；前端按 `since=seq` 增量拉；长输出截断+分片入库。 |
| 并发写活 session | lock 门控：活走 tmux，闲走 resume。 |
| 越权控制 | commands 行带 sender_open_id，bridge 校验白名单。 |
| 长输出刷屏 | content 截断（首/尾 N 字符）+ 折叠工具细节；raw 字段可选保留全量。 |
| Bridge 崩溃 | Tailer offset 持久化在本地 sqlite；commands 消费幂等（consumed 标记 + 事务）。 |

## 技术选型

- **Bridge**：Python 3（stdlib sqlite3 + subprocess 调 lark-cli/copilot/tmux；asyncio）。本地自用 SQLite 存 offset/审计。
- **妙搭 app**：NestJS + React + Drizzle（平台模板 `nestjs-react-fullstack`，TS）。
- **外部二进制**：`copilot`、`lark-cli`、`tmux`。
- **lark-cli profile**：用 `hermes`（默认 profile 的 device flow 在 1.0.59 坏；建议升 1.0.61）。

## spike 验证记录（2026-07-01）

- bridge `lark-cli apps +db-execute` INSERT/SELECT 出站读写妙搭 DB ✅
- 妙搭 full_stack = NestJS+React+Drizzle+托管 Postgres ✅
- 后端 `@Inject(DRIZZLE_DATABASE)` 平台注入、前端 `axiosForBackend` 自动带前缀+CSRF ✅
- release 自动迁移 schema 到 online ✅
- 飞书内打开 `online_url` 看到服务器写入的行 ✅（spike app `<your_app_id>`，URL `https://<your_feishu_domain>/app/<your_app_id>`）
- spike 代码在 `~/copilot-spike-app`（git 仓库 `sprint/default` 分支），可演进为正式 app。
