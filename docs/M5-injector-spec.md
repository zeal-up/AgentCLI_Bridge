# M5 Injector — 实现规格（给子 agent）

## 目标
`bridge/injector.py`：轮询妙搭 `commands` 表（未消费行）→ 注入对应 Copilot session → 标记已消费 + 回写结果。零侵入写方向（执行 copilot/tmux）。

## 依赖（已就绪）
- `bridge/lark_db.py`：`query(sql)`, `execute(sql)`, `sql_str(v)`。所有 DB 访问走它（hermes profile, online env, --yes）。
- `bridge/config.py`：`COPILOT_HOME`, `SESSION_STATE_DIR`, `ALLOWED_OPEN_IDS`, `BRIDGE_STATE_DB`。
- `bridge/state.py`（Tailer 已建）：本地 sqlite offset 存储；可复用或新增 `audit` 表。
- 妙招 `commands` 表（online 已存在）：`id BIGINT PK, session_id, content, sender_open_id, created_at, consumed BOOL, consumed_at, result TEXT`。
- `bridge/indexer.py` 有 `_live_locks() -> {session_id: pid}` 可复用判定在线。

## 核心逻辑
1. `poll_once()`：
   - `SELECT id, session_id, content, sender_open_id FROM commands WHERE consumed = FALSE ORDER BY id LIMIT 50`。
   - 对每行：
     - **白名单校验**：`sender_open_id` 必须在 `config.ALLOWED_OPEN_IDS`，否则拒绝（写 result="forbidden: sender not allowed"，标记 consumed + consumed_at，记审计，跳过执行）。
     - 判定 session 在线：`indexer._live_locks()` 含该 session_id？
       - **离线**：`copilot -p <content> --resume <session_id> --output-format json --allow-all-tools`（在 session 的 cwd 下执行；cwd 从 `session-store.db` 或妙招 `sessions` 表查）。headless JSONL 输出。
       - **在线**（tmux 托管）：`tmux send-keys -t <target> <content> Enter`（target 命名约定：`copilot-<session_id>`；若 tmux pane 不存在则降级为离线 resume 并记 result 警告）。
     - 执行后：`UPDATE commands SET consumed=TRUE, consumed_at=<now>, result=<摘要> WHERE id=<id> AND consumed=FALSE`（幂等：WHERE consumed=FALSE 防重复消费）。
     - result 摘要：离线 resume 取 JSONL 末尾 `result` 事件的用量/状态；在线 tmux 取 "sent to pane <target>"。
   - 返回处理条数。
2. 离线 resume 的 JSONL 输出**不要**自己写 events 表（Tailer 会 tail 同一 session 的 events.jsonl 自动捕获 headless 输出——因为 copilot -p --resume 也写 events.jsonl）。确认这一点；若 headless 不写 events.jsonl，则 injector 直接解析 JSONL 调用 `lark_db` 批量插 events（复用 tailer 的映射逻辑——可 import tailer 的函数）。

## 安全
- `content` 注入 shell 时**必须**用 `subprocess.run([...], ...)` 列表形式或 `shlex.quote`，禁止 shell=True 拼接（命令注入）。
- `--allow-all-tools` 只在白名单用户触发时使用。
- 超时：resume 默认 600s（长任务可被 abort）。
- 审计：本地 `bridge-state.db` 的 `audit(id, session_id, sender, status, ts)` 表。

## CLI（`bridge/__main__.py` 加 `inject` 命令）
- `python -m bridge inject [--once]`：`--once` 处理当前未消费队列一次退出；否则循环（2s 间隔）。
- 不要破坏现有 `index/ls/lock/tail/events` 命令。

## 测试
1. 手动插一条测试 command（online）：用**专用测试 session** `2833f2a6-f4d9-44ef-b09a-65606d181ce6`（offline，是我之前 `copilot -p "pong"` 创建的测试 session，避免打扰真实工作 session）：
   `INSERT INTO commands (id, session_id, content, sender_open_id, created_at, consumed) VALUES (<id>, '2833f2a6-f4d9-44ef-b09a-65606d181ce6', 'reply with exactly: pong from bridge', '<your_user_id>', <now ISO>, FALSE)`。
   id 用 `Date.now()*1000+random` 风格的大整数。
2. `python -m bridge inject --once` → 应执行 copilot resume，commands 行 consumed=TRUE + result 有摘要。
3. 验证 events：`python -m bridge events --session 2833f2a6-f4d9-44ef-b09a-65606d181ce6` 看到 resume 产生的新事件（user.message "reply with exactly: pong from bridge" + assistant.message 回复）。注意：Tailer 守护进程若在跑会自动捕获；若没跑，injector 自己解析 JSONL 输出插 events（见"核心逻辑"第 2 步的说明）。
4. 白名单：插一条 sender_open_id 不在白名单的 command → inject 应拒绝执行、标记 consumed + result="forbidden: sender ..."。
5. 确认 `python -m bridge index/ls/lock/tail/events` 无回归。

## 约束
- 只改 `bridge/injector.py` + `bridge/__main__.py`（加 inject 命令）+ 可选 `bridge/state.py`（加 audit 表，若已存在则兼容）。
- 不碰 `~/copilot-spike-app`（飞书 app，主在改）。
- 不碰 `bridge/tailer.py`（如需复用映射函数，import 它的公开函数，不要改它）。
- 风格：type hints, `from __future__ import annotations`, `logging.getLogger(__name__)`。
