# 给 AI Agent 的安装任务说明

> 这是一份**写给 AI agent**（Claude Code / Copilot / Codex 等）看的安装 runbook。终端用户把它丢给你，你在他的内网服务器上把 AgentCLI Bridge 装起来。
>
> 你（agent）的目标：在一台内网 Linux 服务器上完成 AgentCLI Bridge 的端到端安装，让它跑起来并验证。过程中**只有一处必须由人类介入**：用飞书 App 扫码授权 lark-cli。其余全部由你完成。

## 你需要知道的项目事实

- **架构**：Bridge（Python，纯标准库）跑在服务器，纯出站经 `lark-cli apps +db-execute` 写飞书妙搭托管的 Postgres；一个妙搭 full_stack 应用（`app/` 目录）读同一 DB 渲染 UI。服务器无公网 IP、无入站端口。
- **仓库布局**：`bridge/`（Python 守护进程）、`app/`（妙搭应用源码，需作为独立 git 仓库推到妙搭 git）、`db/schema.sql`（建表 DDL）、`scripts/`（启停）、`install.sh`（一键脚本，已实现本文档大部分逻辑）。
- **已知的 lark-cli 坑**（1.0.59，本机实测）：
  1. `apps` 域所有子命令必须 `--as user`（`--as bot` 被拒）。
  2. `apps +git-credential-init` 内部子调用不透传 `--profile`，导致 `git push` 报 `current login does not match initialized credential`。**绕法**：手动把全局 git credential helper 写成带 `--profile <p>` 的版本（见步骤 6）。
  3. 默认 profile 的 device flow 坏了（`client_secret` 缺失）。**用 `--profile hermes` 跑**就正常。
  4. `db-execute` 的 SELECT 也要 `--yes`（high-risk）。
  5. db-execute 返回结构：SELECT 的行在 `data.results[*].data`（一个 JSON 字符串），不是 `data.rows`。
- **Python 无第三方依赖**：不要装 pip 包；`bridge/` 全是标准库。
- **Codex/Claude/Copilot 的 headless 注入**需要各自的高危标志（`--dangerously-bypass-approvals-and-sandbox` / `IS_SANDBOX=1 --dangerously-skip-permissions` / `--allow-all-tools`），这些由 Bridge 代码自己加，**你不需要**在安装时处理。

## 执行策略

**首选**：直接跑 `./install.sh`。它已实现下述全部步骤、是幂等的、带阶段跳过。你的主要工作是：

1. 先 `git clone` 仓库并 `cd` 进去。
2. 跑 `./install.sh`，在它停在"扫码授权"那一步时，**把 verification_url 摆给人类**，等他扫码完成后让脚本继续。
3. 如果 `install.sh` 某阶段失败，按下面"分步"对应章节排错，修完重跑（已完成的阶段会跳过）。

`install.sh` 支持的 flag：`--skip-app`（只装服务器侧，应用你自己搞过）、`--skip-systemd`、`--app-id <id>`（复用已有应用）。

**只有在 `install.sh` 不适用或你想逐步控制时**，才按下述分步执行。

---

## 分步执行（install.sh 失败时的人工等价物）

### 0. 前置检查
- OS：读 `/etc/os-release` 的 `ID`。Ubuntu/Debian 用 apt；RHEL/Alibaba/Anolis/CentOS 用 yum。
- 你需要有 sudo 权限（装系统包、npm 全局包、systemd unit）。
- 不要 `echo`/打印任何 `~/.copilot/config.json` 内容——里面有 GitHub token。

### 1. 系统依赖
```bash
# apt 系
sudo apt-get update && sudo apt-get install -y tmux sqlite3 python3 git curl
# yum 系
sudo yum install -y tmux sqlite python3 git curl
python3 --version   # 需 ≥ 3.10
```

### 2. Node ≥ 22
```bash
node -p 'process.versions.node.split(".")[0]' 2>/dev/null  # 若 ≥22 跳过
# apt 系
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt-get install -y nodejs
# yum 系
curl -fsSL https://rpm.nodesource.com/setup_22.x | sudo -E bash -
sudo yum install -y nodejs
```

### 3. lark-cli + 登录
```bash
sudo npm install -g @larksuiteoapi/lark-cli
# 检查是否已登录
lark-cli auth status --profile hermes | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d["identities"]["user"].get("available"))'
```
若不是 True，做 device flow：
```bash
lark-cli auth login --domain apps --profile hermes --no-wait --json
# 解析输出里的 verification_url 和 device_code
# ⬇️ 把 verification_url 给人类，让他用飞书 App 扫码 ⬇️
lark-cli auth login --device-code <device_code> --profile hermes
lark-cli auth status --profile hermes   # 复核 identities.user.status == ready
```
**这是唯一必须人类介入的步骤。** 不要尝试跳过或自动化扫码。

### 4. agent CLI 检测（只检测，不强制装）
```bash
for a in copilot claude codex; do command -v $a >/dev/null && echo "$a ok" || echo "$a missing"; done
```
缺失的就报给人类，让他自己装+登录（每个 CLI 各有登录流程，你不要替他登）。

### 5. 创建妙搭应用
```bash
lark-cli apps +create --profile hermes --as user \
  --name "AgentCLI Bridge" --app-type full_stack \
  --description "Remote view/control of LAN agent CLI sessions"
# 解析输出 data.app_id，记为 APP_ID
```
若失败（平台限流/权限），让人类在妙搭控制台手动建 full_stack 应用，把 app_id 给你，跳到步骤 6。

### 6. git 凭证 + 仓库地址
```bash
APP_ID=app_xxx
lark-cli apps +git-credential-init --profile hermes --as user --app-id "$APP_ID"
REPO_URL=$(lark-cli apps +git-credential-list --profile hermes --as user \
  | python3 -c "import sys,json;d=json.load(sys.stdin);print([c['repository_url'] for c in d['data']['credentials'] if c['app_id']=='$APP_ID'][0])")
# 关键：把 credential helper 改成带 --profile 的（绕开 lark-cli 1.0.59 bug）
git config --global "credential.$REPO_URL.helper" \
  "!lark-cli apps git-credential-helper --profile hermes --app-id '$APP_ID'"
git config --global "credential.$REPO_URL.useHttpPath" true
```

### 7. 推送应用代码
```bash
cd app
[[ -d .git ]] || { git init -q; git branch -m sprint/default 2>/dev/null || git checkout -q -b sprint/default; }
git remote remove origin 2>/dev/null
git remote add origin "$REPO_URL"
git add -A
git -c user.email=bridge@local -c user.name=bridge commit -q -m "initial: AgentCLI Bridge app" 2>/dev/null || true
git push -u origin sprint/default
cd ..
```

### 8. 装应用依赖 + 建表 + 反生成 schema + 发布
```bash
cd app && npm ci --no-audit --no-fund && cd ..

# 建 dev 表
lark-cli apps +db-execute --profile hermes --as user --env dev \
  --app-id "$APP_ID" --sql "$(cat db/schema.sql)" --yes

# 反生成 schema.ts
cd app && npm run gen:db-schema
git add -A && git -c user.email=bridge@local -c user.name=bridge commit -q -m "db: schema" 2>/dev/null
git push origin sprint/default
cd ..

# 发布（迁移 dev schema → online，约 90s）
RID=$(lark-cli apps +release-create --profile hermes --as user \
  --app-id "$APP_ID" --branch sprint/default \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["data"]["release_id"])')
# 轮询
for i in $(seq 1 20); do
  sleep 10
  ST=$(lark-cli apps +release-get --profile hermes --as user --app-id "$APP_ID" --release-id "$RID" \
    | python3 -c 'import sys,json;print(json.load(sys.stdin)["data"]["status"])')
  echo "[$i] $ST"
  [[ "$ST" == finished || "$ST" == failed || "$ST" == succeeded ]] && break
done
# ST 必须是 finished/succeeded
```

### 9. 写 .env.local
```bash
cat > .env.local <<EOF
COPILOT_BRIDGE_APP_ID=$APP_ID
COPILOT_BRIDGE_PROFILE=hermes
COPILOT_BRIDGE_DB_ENV=online
COPILOT_BRIDGE_ALLOW_OPEN_IDS=
EOF
```
`ALLOW_OPEN_IDS` 先留空——见步骤 11。

### 10. 启动 + systemd
```bash
# 若是 root 且有 systemd
UNIT=/etc/systemd/system/agentcli-bridge.service
cat > $UNIT <<EOF
[Unit]
Description=AgentCLI Bridge
After=network-online.target
Wants=network-online.target
[Service]
Type=forking
User=$(whoami)
WorkingDirectory=$(pwd)
Environment=PATH=/usr/local/bin:/usr/bin:/bin
EnvironmentFile=$(pwd)/.env.local
ExecStart=$(pwd)/scripts/bridge-start.sh
ExecStop=$(pwd)/scripts/bridge-stop.sh
PIDFile=$HOME/.copilot-bridge/bridge.pids
Restart=on-failure
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now agentcli-bridge
# 否则：./scripts/bridge-start.sh
```

### 11. 验证 + 找 user id
```bash
./scripts/bridge-start.sh --once    # 冒烟
python -m bridge ls                  # 应列出版本机各 agent 的 session
python -m bridge lock                # live 在线 session
```
然后告诉人类：去飞书打开应用，发一条消息（会被拒）。之后你查：
```bash
lark-cli apps +db-execute --profile hermes --as user --env online \
  --app-id "$APP_ID" --yes \
  --sql "SELECT DISTINCT sender_open_id FROM commands WHERE result LIKE 'forbidden%'"
```
把查到的 id 填进 `.env.local` 的 `COPILOT_BRIDGE_ALLOW_OPEN_IDS`，重启 bridge（`systemctl restart agentcli-bridge` 或 `./scripts/bridge-stop.sh && ./scripts/bridge-start.sh`）。

### 12. 收尾报告给人类
- 应用打开方式：飞书 → 工作台 → 妙搭应用 → AgentCLI Bridge。
- 若 `ALLOW_OPEN_IDS` 仍空，明确告诉他发指令会被拒，按步骤 11 补。
- 指向 `README.md` / `docs/INSTALL.md` 做日常运维。

---

## 硬性约束（不要违反）

- 不要打印或泄露 `~/.copilot/config.json`、`.env.local`、git 凭证内容。
- 不要替人类做飞书扫码登录（步骤 3）——把 URL 给他，等他扫。
- 不要替人类做 agent CLI 的账号登录（步骤 4）。
- `db-execute` 一律带 `--profile hermes --as user --yes`。
- online 库**禁止 DDL**；DDL 只在 `--env dev` 跑（步骤 8 的建表）。
- 不要装 Python 第三方包；`bridge/` 是纯标准库。
- 任何阶段失败：把**原始错误**报给人类，不要静默继续；可重试幂等步骤。
