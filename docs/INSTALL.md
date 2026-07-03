# 安装指南（人工分步）

> 想省事直接跑 `./install.sh`（见 README）。本文档是给想逐步理解/手动执行的人，也是 `install.sh` 失败时的排错参考。
>
> 如果你想让一个 AI agent 帮你装，看 [INSTALL_FOR_AGENT.md](INSTALL_FOR_AGENT.md)。

整个安装分两块：**A. 服务器侧**（Bridge 守护进程）和 **B. 飞书侧**（妙搭 full_stack 应用）。两边通过同一个托管 Postgres 中继。

---

## 前置总览

| 组件 | 要求 |
|---|---|
| 服务器 | 内网 Linux（Ubuntu/Debian 或 RHEL 系），能出站访问飞书域名 |
| Python | ≥ 3.10（Bridge 纯标准库，无需 pip 安装第三方包） |
| Node.js | ≥ 22（装 lark-cli + 构建妙搭应用） |
| 系统工具 | `tmux`、`sqlite3`、`git`、`curl` |
| lark-cli | npm 全局安装，需用一个 profile 授权 `spark:app:read/write` 等 |
| agent CLI | `copilot` / `claude` / `codex` 任选其一或全部（各自已登录） |
| 飞书账号 | 能创建妙搭 full_stack 应用（个人/企业账号均可） |

---

## A. 服务器侧

### A1. 装系统依赖

Ubuntu/Debian：

```bash
sudo apt-get update
sudo apt-get install -y tmux sqlite3 python3 git curl
```

RHEL/Alibaba/Anolis 系：

```bash
sudo yum install -y tmux sqlite python3 git curl
```

Python 版本检查：`python3 --version`（需 ≥ 3.10）。

### A2. 装 Node.js ≥ 22

```bash
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt-get install -y nodejs   # 或 yum install -y nodejs
node --version   # 应 ≥ v22
```

### A3. 装 lark-cli 并登录飞书

```bash
sudo npm install -g @larksuiteoapi/lark-cli
lark-cli --version
```

登录用 device flow（profile 名自取，下面用 `hermes`）：

```bash
# 1) 启动 device flow，拿到 verification_url + device_code
lark-cli auth login --domain apps --profile hermes --no-wait --json
# 输出里会有 verification_url 和 device_code

# 2) 浏览器打开 verification_url，用飞书 App 扫码授权

# 3) 扫码完成后收尾
lark-cli auth login --device-code <device_code> --profile hermes

# 4) 验证
lark-cli auth status --profile hermes
# identities.user.status 应为 "ready"
```

> lark-cli 1.0.59 有个 profile bug：`apps +git-credential-init` 内部子调用不透传 `--profile`，会导致 git push 报 `current login does not match initialized credential`。本文档 A6 会用带 `--profile` 的 credential helper 绕开。建议升级到 1.0.61+。

### A4. 装 agent CLI（按需）

```bash
sudo npm install -g @github/copilot           # GitHub Copilot CLI
sudo npm install -g @anthropic-ai/claude-code # Claude Code
sudo npm install -g @openai/codex             # OpenAI Codex
```

每个 CLI 第一次运行要各自登录（按提示）。装了哪个，Bridge 就管哪个；不装也行。

### A5. 取本仓库

```bash
git clone <this-repo-url> agentcli_bridge
cd agentcli_bridge
```

---

## B. 飞书侧（妙搭 full_stack 应用）

### B1. 创建应用

命令行创建（推荐）：

```bash
lark-cli apps +create --profile hermes --as user \
  --name "AgentCLI Bridge" --app-type full_stack \
  --description "Remote view/control of LAN agent CLI sessions"
# 输出里有 app_id（形如 app_xxxxxxxxxxxxx）
```

或在飞书「妙搭」控制台手动创建一个 full_stack 应用，记下 app_id。

记下 `APP_ID`，后面到处用。

### B2. 初始化 git 凭证 + 拿仓库地址

```bash
APP_ID=app_xxxxxxxxxxxxx   # 换成你的
lark-cli apps +git-credential-init --profile hermes --as user --app-id "$APP_ID"

# 拿仓库 URL
lark-cli apps +git-credential-list --profile hermes --as user
# 找到 app_id 对应的 repository_url，形如
# https://miaoda-git.feishu.cn/apaas4.0/-/t_xxx/code_xxx.git
```

### B3. 推送应用代码

仓库里的 `app/` 目录就是妙搭应用源码。把它作为独立 git 仓库推到上一步的 repository_url：

```bash
cd app
git init -q
git branch -m sprint/default
git remote add origin <repository_url>
git add -A
git -c user.email=bridge@local -c user.name=bridge commit -m "initial: AgentCLI Bridge app"
git push -u origin sprint/default
cd ..
```

> 如果 push 报 `current login does not match initialized credential`，是 lark-cli 没带 profile。手动把 credential helper 写成带 profile 的：
> ```bash
> git config --global "credential.<repository_url>.helper" \
>   "!lark-cli apps git-credential-helper --profile hermes --app-id '$APP_ID'"
> git config --global "credential.<repository_url>.useHttpPath" true
> ```
> 再 `cd app && git push -u origin sprint/default`。

### B4. 装应用依赖

```bash
cd app
npm ci            # 或 npm install
cd ..
```

### B5. 建表（dev 库）

```bash
lark-cli apps +db-execute --profile hermes --as user --env dev \
  --app-id "$APP_ID" --sql "$(cat db/schema.sql)" --yes
```

`db/schema.sql` 建 4 张表：`sessions` / `events` / `commands` / `renames`。

### B6. 反生成 schema + 提交

```bash
cd app
npm run gen:db-schema   # 从 dev 库反生成 server/database/schema.ts
git add -A && git commit -m "db: schema" && git push origin sprint/default
cd ..
```

### B7. 发布（迁移 schema 到 online）

```bash
REL=$(lark-cli apps +release-create --profile hermes --as user \
  --app-id "$APP_ID" --branch sprint/default)
RID=$(echo "$REL" | python3 -c 'import sys,json;print(json.load(sys.stdin)["data"]["release_id"])')

# 轮询至 finished（约 90s）
while true; do
  lark-cli apps +release-get --profile hermes --as user \
    --app-id "$APP_ID" --release-id "$RID" | \
    python3 -c 'import sys,json;s=json.load(sys.stdin)["data"]["status"];print(s);exit(0 if s in ("finished","failed","succeeded") else 1)' \
    && break
  sleep 10
done
```

`finished` 后，online 库已有这 4 张表，应用也在飞书云上线了。

---

## C. 配置 + 启动 Bridge

### C1. 写 `.env.local`

```bash
cat > .env.local <<EOF
COPILOT_BRIDGE_APP_ID=$APP_ID
COPILOT_BRIDGE_PROFILE=hermes
COPILOT_BRIDGE_DB_ENV=online
COPILOT_BRIDGE_ALLOW_OPEN_IDS=
EOF
```

### C2. 找你的飞书 user id（发指令白名单）

`ALLOW_OPEN_IDS` 是允许发指令的飞书用户。先留空，**从飞书发一条消息**到应用里（会被拒绝），再到服务器查：

```bash
lark-cli apps +db-execute --profile hermes --as user --env online \
  --app-id "$APP_ID" --yes \
  --sql "SELECT DISTINCT sender_open_id FROM commands WHERE result LIKE 'forbidden%'"
```

把查到的 id 填进 `.env.local` 的 `COPILOT_BRIDGE_ALLOW_OPEN_IDS`（多个用逗号分隔），重启 Bridge。

> 这是应用作用域内的 user_id（不是 `ou_...` open_id），由妙搭平台注入（`req.userContext.userId`）。

### C3. 启动

手动：

```bash
./scripts/bridge-start.sh
./scripts/bridge-status.sh
```

systemd（生产）：

```bash
sudo cp scripts/agentcli-bridge.service /etc/systemd/system/
# 编辑该文件，把 __USER__ / __ROOT__ 换成实际值（或直接跑 install.sh 自动生成）
sudo systemctl daemon-reload
sudo systemctl enable --now agentcli-bridge
```

### C4. 验证

```bash
python -m bridge ls      # 应看到本机各 agent 的 session
python -m bridge lock    # 应看到 live 在线 session
```

在飞书（手机或桌面）→ 工作台 → 妙搭应用 → AgentCLI Bridge，即可看到会话列表。

---

## 排错速查

| 现象 | 排查 |
|---|---|
| `bridge ls` 报 db-execute 错 | 检查 `.env.local` 的 APP_ID/PROFILE；`lark-cli auth status --profile hermes` 是否 user ready |
| 飞书里发指令被拒 | `ALLOW_OPEN_IDS` 没填你的 user id（见 C2） |
| git push 报 credential 不匹配 | credential helper 没带 `--profile hermes`（见 B3 注） |
| release 卡住/failed | 妙搭控制台看构建日志；通常是 `schema.ts` 与 dev 库不一致，重跑 B6 |
| live 会话发指令 CLI 没反应 | agent 进程不在 tmux 里，或 cwd 对不上（见 `bridge/agents/live.py` 的检测逻辑）；会回退到 headless resume |
| Codex 中间对话回合发送锁迟钝 | Codex 无每回合结束事件，靠 idle 兜底（已知限制，见 README） |

日志：`~/.copilot-bridge/logs/{index,tail,inject}.log`。
