# 实施计划（Plan）

> 2026-07-01 修订：里程碑按妙搭 full_stack + DB 中继架构重排。spike 已验证主干（DB 出站读写、NestJS/React/Drizzle、release 迁移 schema、飞书内打开）。每阶段独立可验证。

## 里程碑总览

| 阶段 | 目标 | 交付物 | 验证标准 |
|---|---|---|---|
| M0 | 调研与 spike | docs/ + spike app | ✅ 已完成（F2-c 全链路验证） |
| M1 | 正式 app + DB schema + 列表页骨架 | 妙招 app `Copilot Bridge`、sessions 表/API/列表页 | ✅ 已完成（飞书内看到 sessions 表） |
| M2 | Indexer（bridge） | `bridge index` 把 session-store.db 写进 sessions 表 | ✅ 已完成（67 真实 session 入库，6 在线） |
| M3 | Tailer（bridge） | `bridge tail` 把 events.jsonl 写进 events 表 | ✅ 已完成（51419 事件入库，稳定 id 幂等） |
| M4 | 前端对话视图 + 准实时 | 对话详情页 + 2s 轮询拉 events | ✅ 已完成（两栏聊天 UI 已发布，待用户验证） |
| M5 | Injector（写方向） | commands 表轮询 + copilot resume/tmux | ✅ 已完成（resume 执行 + 白名单拒绝 验证通过） |
| M6 | 健壮性与体验 | offset 持久化/重连/截断/运维脚本/守护进程 | ✅ 已完成（bridge-start/stop/status + systemd，daemon 运行中） |

## 详细任务

### M1 — 正式 app + schema + 列表页骨架
- [ ] 演进 spike app 为正式：`apps +update --name "Copilot Bridge"`（或新建 app），清理 spike 测试表。
- [ ] dev 建 `sessions`/`events`/`commands` 三表（`db-execute` DDL）→ `fullstack-cli gen-db-schema` 生成 `schema.ts`。
- [ ] NestJS：`SessionsModule`（`GET /api/sessions`，Drizzle 查 sessions 表）。
- [ ] React：列表页（`axiosForBackend.get('api/sessions')` 渲染表格）。
- [ ] push + release（online 自动建表）→ 手动 `db-execute INSERT` 一条 session → 飞书内打开看到。
- [ ] 验证 `db-execute` 经 spark API 不受 database_url 过期影响（bridge 长跑前提）。

### M2 — Indexer（bridge 侧）
- [ ] Python bridge 骨架：配置（app_id、profile=hermes、白名单 open_id）、`lark-cli` 子进程封装（带 `--yes`）。
- [ ] 读 `~/.copilot/session-store.db` 的 `sessions` 表（sqlite3）。
- [ ] 扫 `session-state/*/inuse.<pid>.lock` + pid 存活 → online 字段。
- [ ] `bridge index`：upsert sessions 表（`INSERT ... ON CONFLICT(id) DO UPDATE`）。
- [ ] 验证：跑 `bridge index`，飞书列表页出现本机真实 session 列表（id/cwd/summary/online）。

### M3 — Tailer（bridge 侧，读方向）
- [ ] events.jsonl 增量 tail（byte offset，offset 存本地 sqlite `offsets(session_id→bytes)`）。
- [ ] 事件解析与格式化（user/assistant/tool/result → content 文本；raw 保留原始 JSON）。
- [ ] 聚批写 events 表（~1s 一条多行 INSERT，`db-execute --file` 或多值 INSERT）。
- [ ] 验证：服务器终端与一个 session 对话，飞书对话页（或直接 `db-execute SELECT`）<2s 看到新事件。

### M4 — 前端对话视图 + 准实时
- [ ] React 对话详情页：按 session_id 拉 events（`GET /api/events?session_id=&since=seq`）。
- [ ] **在线 AI 式聊天 UI**（参考 opencode / lobe-chat 的形态）：左侧 session 列表、右侧对话窗（user/assistant 气泡、tool 调用折叠卡、结果卡）、底部指令输入框（POST commands）。按 role 渲染气泡，长内容折叠+展开，工具调用配对 start/complete 显示状态。
- [ ] SSE：NestJS `GET /api/stream?session_id=` 推新事件（轮询 DB 内推）；或前端 1-2s 轮询作降级。
- [ ] 长输出截断 + 工具细节折叠（前端渲染）。
- [ ] 验证：选 session，终端持续对话，前端准实时刷新。

### M5 — Injector（写方向）
- [ ] 前端：对话页底部输入框 → `POST /api/commands`（写 commands 表，带 sender_open_id）。
- [ ] bridge `bridge inject`：1-2s 轮询 `SELECT * FROM commands WHERE consumed=false`。
- [ ] lock 门控：在线走 `tmux send-keys`，离线走 `copilot -p --resume --output-format json --allow-all-tools`。
- [ ] 消费幂等：`UPDATE commands SET consumed=true, consumed_at=?, result=? WHERE id=? AND consumed=false`。
- [ ] resume 的 JSONL 输出复用 M3 格式化逻辑入库。
- [ ] 白名单校验 sender_open_id，越权拒绝并审计。
- [ ] 验证：飞书对离线 session 发指令，看到执行 + 结果回传；在线 session 终端/手机交替发，历史一致。

### M6 — 健壮性与体验
- [ ] Tailer offset 持久化 + 崩溃重启续读。
- [ ] bridge 进程守护（systemd / nohup）+ 断线重试。
- [ ] 长输出截断（首/尾 N 字符）、工具结果折叠、敏感输出脱敏。
- [ ] events 表归档/清理策略（按 session/时间）。
- [ ] 运维脚本：`bridge start/stop/status`、日志。
- [ ] lark-cli 升 1.0.61（消除 profile bug，简化 git/auth 流程）。

## 一期 MVP 范围

M1 + M2 + M3 + M4 + M5(最小)：
- 飞书内打开 Copilot Bridge → 看到本机 session 列表 → 点进对话页看实时事件流 → 底部发指令续对话（离线走 resume）。
- 在线 tmux 路径（M5 在线分支）可后置。

## 关键决策（已定）

1. 交互形态：**妙搭 full_stack app + DB 中继**（spike 验证）。
2. bridge 语言：**Python**（读 sqlite/jsonl + subprocess 调 lark-cli/copilot/tmux）。
3. bridge → DB：**`lark-cli apps +db-execute`**（出站、hermes profile、带 `--yes`）；NestJS 只读 DB + SSE。
   - 备选（若 subprocess 开销成瓶颈）：bridge 调 NestJS API（需平台 auth gate 对 bridge 路由开例外 + shared secret）——M6 评估。
4. lark-cli profile：**hermes**（默认 profile device flow 坏）；建议升 1.0.61。
5. 实时性：**SSE 优先**，前端轮询降级。
6. 演进 spike app `<your_app_id>` 为正式 app（保留已通的 auth/scaffold），重命名 + 清测试数据。

## 环境依赖（已具备）

- `copilot` v1.0.65、`lark-cli` v1.0.59（hermes profile 已授权 `spark:app:read/write`）、`tmux`（M5 在线分支待确认）、`sqlite3`、Python 3、Node 24（妙招 app）。
- spike 产物：妙招 app `<your_app_id>`（已发布）、本地 `~/copilot-spike-app`（scaffold + git remote）。
