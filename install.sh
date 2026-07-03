#!/usr/bin/env bash
# AgentCLI Bridge — interactive guided installer.
#
# Walks you through the whole setup one stage at a time, prompting for any
# input it needs. You can answer the prompts, press Enter for the [default],
# or skip a stage. Re-running resumes (completed stages are skipped).
#
# The only step that MUST be done by a human is scanning a QR code to
# authorize lark-cli (Feishu OAuth). Everything else is automated.
#
# Usage:
#   ./install.sh                 # full guided install
#   ./install.sh --skip-app      # skip the Miaoda app stage (already set up)
#   ./install.sh --skip-systemd  # skip the systemd unit
#   ./install.sh --app-id <id>   # reuse an existing Miaoda app
#   ./install.sh --non-interactive  # take all defaults, no prompts (for agents)
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PROFILE="${COPILOT_BRIDGE_PROFILE:-hermes}"
APP_ID_OVERRIDE=""
SKIP_APP=0
SKIP_SYSTEMD=0
NON_INT=0
DB_ENV="online"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-app)         SKIP_APP=1; shift;;
    --skip-systemd)     SKIP_SYSTEMD=1; shift;;
    --app-id)           APP_ID_OVERRIDE="$2"; shift 2;;
    --profile)          PROFILE="$2"; shift 2;;
    --non-interactive)  NON_INT=1; shift;;
    -h|--help)          sed -n '2,18p' "$0"; exit 0;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done

# ---- helpers ---------------------------------------------------------------
C_G='\033[32m'; C_Y='\033[33m'; C_R='\033[31m'; C_B='\033[1m'; C_C='\033[36m'; C_D='\033[2m'; C_0='\033[0m'
say()  { printf "${C_C}▶ %s${C_0}\n" "$*"; }
ok()   { printf "${C_G}✓ %s${C_0}\n" "$*"; }
warn() { printf "${C_Y}! %s${C_0}\n" "$*" >&2; }
die()  { printf "${C_R}✗ %s${C_0}\n" "$*" >&2; exit 1; }
hdr()  { printf "\n${C_B}════════ %s ════════${C_0}\n" "$*"; }

have() { command -v "$1" >/dev/null 2>&1; }

# Guided prompt: prompt "label" "default" -> echoes value (blank allowed)
prompt() {
  local label="$1" def="${2:-}" v
  if [[ $NON_INT -eq 1 ]]; then echo "$def"; return; fi
  read -r -p "${C_B}${label}${C_0}${def:+ [${C_D}${def}${C_0}]}: " v || true
  echo "${v:-$def}"
}

# Yes/no: confirm "question" [default-y]
confirm() {
  local q="$1" d="${2:-y}" v
  if [[ $NON_INT -eq 1 ]]; then [[ "$d" =~ ^[yY] ]]; return; fi
  read -r -p "${C_B}${q}${C_0} (${C_D}${d}/n${C_0}) " v || true
  v="${v:-$d}"
  [[ "$v" =~ ^[yY] ]]
}

# A guided stage: stage "N/7" "title" "what this does" — prints header + blurb,
# asks to proceed (or skip), returns 0 to run, 1 to skip.
stage() {
  local num="$1" title="$2" blurb="$3"
  hdr "Stage $num — $title"
  printf "${C_D}%s${C_0}\n" "$blurb"
  if [[ $NON_INT -eq 0 ]]; then
    if ! confirm "Run this stage now?" y; then
      warn "skipped"
      return 1
    fi
  fi
  return 0
}

# ============================================================================
printf "\n${C_B}╔════════════════════════════════════════════════════════════╗${C_0}\n"
printf "${C_B}║          AgentCLI Bridge — 安装向导                          ║${C_0}\n"
printf "${C_B}╚════════════════════════════════════════════════════════════╝${C_0}\n\n"
cat <<'INTRO'
把内网服务器上的 agent CLI 会话（Copilot / Claude Code / Codex）镜像到飞书，
手机打开飞书即可查看进度、发指令。服务器无需公网 IP / 端口穿透。

本向导分 7 步，每步会说明要做什么、需要你输入什么。带 [默认] 的直接回车即可。
唯一需要你动手的：用飞书 App 扫一次码授权登录。

  1. 系统依赖        (tmux/sqlite3/python3/node22，自动装)
  2. lark-cli 登录   (扫码授权 — 需人工)
  3. 检测 agent CLI  (copilot/claude/codex，按需)
  4. 创建妙搭应用    (或复用已有的)
  5. 推送代码+建表+发布
  6. 配置 .env.local
  7. 守护进程+验证

INTRO
[[ $NON_INT -eq 0 ]] && { confirm "开始安装？" y || exit 0; }

# ============================================================================
stage "1/7" "系统依赖" "安装 tmux / sqlite3 / python3 / git / curl / Node ≥22。需要 sudo。" || { warn "跳过；请自行确保依赖存在。"; }

OS_ID="$(. /etc/os-release 2>/dev/null && echo "$ID")" || OS_ID=""
INST=""
case "$OS_ID" in
  alinux|alibaba*|centos|rhel|rocky|almalinux|fedora|anolis) INST="yum install -y";;
  ubuntu|debian|raspbian) INST="apt-get install -y";;
esac

need_pkgs=()
have tmux    || need_pkgs+=(tmux)
have sqlite3 || need_pkgs+=(sqlite3)
have python3 || need_pkgs+=(python3)
have git     || need_pkgs+=(git)
have curl    || need_pkgs+=(curl)

if [[ ${#need_pkgs[@]} -gt 0 ]]; then
  if [[ -z "$INST" ]]; then
    warn "无法识别发行版（$OS_ID），请手动装: ${need_pkgs[*]}"
  else
    say "缺失: ${need_pkgs[*]}（将用 $INST 安装，需要 sudo）"
    [[ "$OS_ID" =~ ubuntu|debian|raspbian ]] && sudo apt-get update -y >/dev/null
    sudo $INST "${need_pkgs[@]}" || die "安装失败: ${need_pkgs[*]}"
  fi
fi
for b in tmux sqlite3 python3 git curl; do have "$b" || die "缺少 $b（必须）"; done
python3 -c 'import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)' \
  || die "python3 ≥ 3.10（当前 $(python3 --version 2>&1)）"

if have node && [[ "$(node -p 'process.versions.node.split(".")[0]')" -ge 22 ]]; then
  ok "Node $(node --version)"
else
  say "Node ≥22 未安装。"
  if confirm "用 NodeSource 脚本自动安装 Node 22？（需要 sudo）" y; then
    case "$OS_ID" in
      ubuntu|debian|raspbian)
        curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - >/dev/null
        sudo apt-get install -y nodejs || die "Node 安装失败";;
      alinux|alibaba*|centos|rhel|rocky|almalinux|fedora|anolis)
        curl -fsSL https://rpm.nodesource.com/setup_22.x | sudo -E bash - >/dev/null
        sudo yum install -y nodejs || die "Node 安装失败";;
      *) die "请手动装 Node ≥22 (https://nodejs.org) 后重跑";;
    esac
    ok "Node $(node --version) 已安装"
  else
    die "需要 Node ≥22 才能继续。装好后重跑 ./install.sh"
  fi
fi
have npm || die "npm 缺失（应随 Node 一起）"
ok "系统依赖就绪"

# ============================================================================
stage "2/7" "lark-cli 安装与飞书登录" "装 lark-cli（npm 全局），然后用飞书 App 扫码授权。这一步必须人工扫码。" || { warn "跳过；后续 DB/git 都依赖 lark-cli 登录，无法继续。"; exit 1; }

if have lark-cli; then
  ok "lark-cli 已装 ($(lark-cli --version 2>/dev/null | head -1))"
else
  say "全局安装 lark-cli（需要 sudo）"
  sudo npm install -g @larksuiteoapi/lark-cli || die "lark-cli 安装失败"
  have lark-cli || die "lark-cli 不在 PATH（把 npm 全局 bin 加入 PATH）"
  ok "lark-cli 已装"
fi

PROFILE="$(prompt '用哪个 lark-cli profile 名？' "$PROFILE")"

login_ok="$(lark-cli auth status --profile "$PROFILE" 2>/dev/null \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print("1" if d.get("identities",{}).get("user",{}).get("available") else "0")' 2>/dev/null || echo 0)"
if [[ "$login_ok" == "1" ]]; then
  ok "已登录（profile=$PROFILE）"
else
  printf "\n${C_B}需要扫码授权。${C_0} 接下来会给你一个 URL + 二维码：\n"
  printf "  → 在浏览器打开 URL，用 ${C_C}飞书手机 App 扫码${C_0} 授权。\n"
  confirm "准备好就开始 device flow？" y || die "需要登录才能继续"
  dr="$(lark-cli auth login --domain apps --profile "$PROFILE" --no-wait --json 2>/dev/null || true)"
  vurl="$(printf '%s' "$dr" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("verification_url") or d.get("url") or "")' 2>/dev/null || true)"
  dcode="$(printf '%s' "$dr" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("device_code") or d.get("code") or "")' 2>/dev/null || true)"
  [[ -n "$vurl" && -n "$dcode" ]] || die "device flow 启动失败；手动跑: lark-cli auth login --domain apps --profile $PROFILE"
  printf "\n${C_C}%s${C_0}\n\n" "$vurl"
  if lark-cli auth qrcode "$vurl" --output /tmp/agentcli-qr.txt >/dev/null 2>&1 && [[ -s /tmp/agentcli-qr.txt ]]; then
    cat /tmp/agentcli-qr.txt
  fi
  say "扫码完成后回到这里（脚本会等你）…"
  lark-cli auth login --device-code "$dcode" --profile "$PROFILE" || die "登录未完成/超时"
  ok "登录成功（profile=$PROFILE）"
fi

# ============================================================================
stage "3/7" "检测 agent CLI" "检查 copilot / claude / codex 是否已安装。装了哪个就管哪个。" || true

found=(); missing=()
for a in copilot claude codex; do
  if have "$a"; then found+=("$a"); ok "$a: $("$a" --version 2>&1 | head -1)"; else missing+=("$a"); fi
done
if [[ ${#missing[@]} -gt 0 ]]; then
  warn "未安装: ${missing[*]}"
  if confirm "要现在装缺失的 agent CLI 吗？（npm 全局，需 sudo；各 CLI 登录仍需你自己做）" n; then
    for a in "${missing[@]}"; do
      case "$a" in
        copilot) sudo npm install -g @github/copilot 2>/dev/null && ok "copilot 已装" || warn "copilot 装失败";;
        claude)  sudo npm install -g @anthropic-ai/claude-code 2>/dev/null && ok "claude 已装" || warn "claude 装失败";;
        codex)   sudo npm install -g @openai/codex 2>/dev/null && ok "codex 已装" || warn "codex 装失败";;
      esac
    done
  fi
fi
[[ ${#found[@]} -gt 0 ]] && ok "已装: ${found[*]}" || warn "一个 agent CLI 都没装——装好后再重跑，或继续（会话列表会空）"

# ============================================================================
APP_ID=""
if [[ $SKIP_APP -eq 1 ]]; then
  warn "--skip-app：跳过妙搭应用阶段"
else
  stage "4/7" "妙达 full_stack 应用" "在飞书妙搭云创建一个 full_stack 应用（或复用已有的）。这是 Bridge 的 UI + 中继 DB。" || { warn "跳过"; }

  if [[ -n "$APP_ID_OVERRIDE" ]]; then
    APP_ID="$APP_ID_OVERRIDE"; ok "复用指定应用: $APP_ID"
  else
    if grep -q '^COPILOT_BRIDGE_APP_ID=' .env.local 2>/dev/null; then
      existing="$(grep '^COPILOT_BRIDGE_APP_ID=' .env.local | cut -d= -f2-)"
      [[ -n "$existing" ]] && confirm "检测到 .env.local 里有应用 $existing，复用它？" y && APP_ID="$existing"
    fi
    if [[ -z "$APP_ID" ]]; then
      if confirm "创建一个新应用？（选 n 则输入已有 app_id）" y; then
        appname="$(prompt '应用显示名' 'AgentCLI Bridge')"
        say "创建中（profile=$PROFILE）…"
        out="$(lark-cli apps +create --profile "$PROFILE" --as user --name "$appname" --app-type full_stack --description "Remote view/control of LAN agent CLI sessions" 2>/dev/null || true)"
        APP_ID="$(printf '%s' "$out" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("data",{}).get("app_id") or d.get("data",{}).get("id") or "")' 2>/dev/null || true)"
        [[ -n "$APP_ID" ]] || die "创建失败。可在妙搭控制台手动建 full_stack 应用后用 --app-id <id> 重跑。输出: $out"
        ok "已创建: $APP_ID"
      else
        APP_ID="$(prompt '输入已有 app_id (app_xxx)' '')"
        [[ -n "$APP_ID" ]] || die "需要 app_id 才能继续"
      fi
    fi
  fi

  # --- 5: git 凭证 + 推送 + 建表 + 发布 ---
  stage "5/7" "推送代码 + 建表 + 发布" "给应用初始化 git 凭证、推送 app/ 代码、在 dev 库建表、发布到 online（约 90s）。" || { warn "跳过；应用未上线，Bridge 跑起来也读不到表"; }

  say "初始化 git 凭证…"
  lark-cli apps +git-credential-init --profile "$PROFILE" --as user --app-id "$APP_ID" >/dev/null 2>&1 || warn "git-credential-init 失败（可能已存在）"
  repo_url="$(lark-cli apps +git-credential-list --profile "$PROFILE" --as user 2>/dev/null \
    | python3 -c "import sys,json; d=json.load(sys.stdin);
cs=[c for c in d.get('data',{}).get('credentials',[]) if c.get('app_id')=='$APP_ID'];
print(cs[0]['repository_url'] if cs else '')" 2>/dev/null || true)"
  [[ -n "$repo_url" ]] || die "拿不到 git 仓库 URL（app_id=$APP_ID）"

  # 绕开 lark-cli 1.0.59 不透传 --profile 的 bug：直接写带 profile 的 helper
  git config --global "credential.${repo_url}.helper" \
    "!lark-cli apps git-credential-helper --profile $PROFILE --app-id '$APP_ID'"
  git config --global "credential.${repo_url}.useHttpPath" true
  ok "git 凭证已配置（带 --profile $PROFILE）"

  say "推送应用代码到妙达 git…"
  if [[ ! -d app/.git ]]; then
    ( cd app && git init -q && { git branch -m sprint/default 2>/dev/null || git checkout -q -b sprint/default; } )
  fi
  ( cd app \
    && { git remote remove origin 2>/dev/null || true; } \
    && git remote add origin "$repo_url" \
    && git add -A && git -c user.email=bridge@local -c user.name=bridge commit -q -m "initial: AgentCLI Bridge app" 2>/dev/null || true \
    && git push -u origin sprint/default >/dev/null 2>&1 ) \
    || die "git push 失败。检查登录后手动: cd app && git push -u origin sprint/default"
  ok "代码已推送"

  say "安装应用 npm 依赖（npm ci）…"
  ( cd app && npm ci --no-audit --no-fund >/dev/null 2>&1 || npm install --no-audit --no-fund >/dev/null 2>&1 ) \
    || die "app/ npm install 失败"

  say "在 dev 库建表（db/schema.sql）…"
  lark-cli apps +db-execute --profile "$PROFILE" --as user --env dev --app-id "$APP_ID" \
    --sql "$(cat db/schema.sql)" --yes >/dev/null 2>&1 \
    || die "dev 建表失败。手动: lark-cli apps +db-execute --env dev --app-id $APP_ID --sql \"\$(cat db/schema.sql)\" --yes"
  ok "dev 表已建"

  say "反生成 Drizzle schema…"
  ( cd app && npm run gen:db-schema >/dev/null 2>&1 ) || warn "gen:db-schema 失败（schema.ts 未变则无妨）"
  ( cd app && git add -A && git -c user.email=bridge@local -c user.name=bridge commit -q -m "db: schema" 2>/dev/null && git push origin sprint/default >/dev/null 2>&1 ) || true

  say "发布（dev schema → online，约 90s）…"
  rel="$(lark-cli apps +release-create --profile "$PROFILE" --as user --app-id "$APP_ID" --branch sprint/default 2>/dev/null || true)"
  rid="$(printf '%s' "$rel" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("data",{}).get("release_id",""))' 2>/dev/null || true)"
  [[ -n "$rid" ]] || die "release-create 失败: $rel"
  for i in $(seq 1 20); do
    sleep 10
    st="$(lark-cli apps +release-get --profile "$PROFILE" --as user --app-id "$APP_ID" --release-id "$rid" 2>/dev/null \
      | python3 -c 'import sys,json; print(json.load(sys.stdin).get("data",{}).get("status","?"))' 2>/dev/null || echo '?')"
    printf "  [%d] %s\n" "$i" "$st"
    [[ "$st" == finished || "$st" == failed || "$st" == succeeded ]] && break
  done
  [[ "$st" == finished || "$st" == succeeded ]] || die "发布未完成（$st）。去妙搭控制台看构建日志。"
  ok "应用已发布上线"
fi

# ============================================================================
stage "6/7" "配置 .env.local" "写入 APP_ID / profile / DB 环境 / 发指令白名单。" || true

ALLOW_IDS="$(grep '^COPILOT_BRIDGE_ALLOW_OPEN_IDS=' .env.local 2>/dev/null | cut -d= -f2- || true)"
echo
say "「发指令白名单」COPILOT_BRIDGE_ALLOW_OPEN_IDS：允许哪些飞书用户向 agent 发指令。"
say "现在不知道没关系——先留空，等你从飞书发第一条消息（会被拒）后，"
say "服务器能查出你的 user id，再填进来重启即可（见 README/INSTALL.md）。"
if [[ -z "$ALLOW_IDS" ]]; then
  ALLOW_IDS="$(prompt 'COPILOT_BRIDGE_ALLOW_OPEN_IDS（可直接回车留空）' '')"
fi

if [[ -n "$APP_ID" ]]; then
  # 确认 APP_ID（若前面阶段跳过，让用户输入）
  :
else
  APP_ID="$(grep '^COPILOT_BRIDGE_APP_ID=' .env.local 2>/dev/null | cut -d= -f2- || true)"
  [[ -n "$APP_ID" ]] || APP_ID="$(prompt '你的妙搭 app_id (app_xxx)' '')"
fi

cat > .env.local <<EOF
# AgentCLI Bridge config (gitignored). Edit freely.
COPILOT_BRIDGE_APP_ID=$APP_ID
COPILOT_BRIDGE_PROFILE=$PROFILE
COPILOT_BRIDGE_DB_ENV=$DB_ENV
COPILOT_BRIDGE_ALLOW_OPEN_IDS=$ALLOW_IDS
EOF
ok ".env.local 已写入（app=$APP_ID, profile=$PROFILE）"

# ============================================================================
stage "7/7" "守护进程 + 冒烟验证" "装 systemd 服务（或直接前台启动），跑一次 index+tail+inject 确认能联通妙搭 DB。" || true

USE_SYSTEMD=1
if [[ $SKIP_SYSTEMD -eq 1 ]]; then
  USE_SYSTEMD=0
elif [[ ! -d /run/systemd/system ]] || [[ "$(id -u)" -ne 0 ]]; then
  warn "当前非 root 或无 systemd，将用 bridge-start.sh 直接启动（不装 unit）"
  USE_SYSTEMD=0
else
  if ! confirm "装 systemd 服务并开机自启？" y; then USE_SYSTEMD=0; fi
fi

if [[ $USE_SYSTEMD -eq 1 ]]; then
  UNIT="/etc/systemd/system/agentcli-bridge.service"
  cat > "$UNIT" <<EOF
[Unit]
Description=AgentCLI Bridge — mirror local agent CLI sessions to Feishu
After=network-online.target
Wants=network-online.target

[Service]
Type=forking
User=$(whoami)
WorkingDirectory=$ROOT
Environment=PATH=/usr/local/bin:/usr/bin:/bin
EnvironmentFile=$ROOT/.env.local
ExecStart=$ROOT/scripts/bridge-start.sh
ExecStop=$ROOT/scripts/bridge-stop.sh
PIDFile=$HOME/.copilot-bridge/bridge.pids
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable --now agentcli-bridge >/dev/null 2>&1 || warn "systemd enable 失败"
  ok "systemd 服务已装并启动：systemctl status agentcli-bridge"
else
  if bash scripts/bridge-status.sh 2>/dev/null | grep -q "running"; then
    ok "守护进程已在运行"
  elif confirm "现在用 bridge-start.sh 启动守护进程？" y; then
    bash scripts/bridge-start.sh >/dev/null 2>&1 || die "bridge-start.sh 失败"
    ok "守护进程已启动。停止: ./scripts/bridge-stop.sh"
  fi
fi

say "冒烟测试（index+tail+inject 一次）…"
bash scripts/bridge-start.sh --once >/tmp/agentcli-smoke.log 2>&1 || warn "冒烟有报错（见 /tmp/agentcli-smoke.log）"
sleep 2
# Load .env.local so the direct `bridge ls` call can talk to the Miaoda DB.
set -a; . ./.env.local; set +a
n="$(python3 -m bridge ls 2>/dev/null | grep -c '"id"')"
ok "已索引 $n 个 session"

# ============================================================================
printf "\n${C_G}╔════════════════════════════════════════════════════════════╗${C_0}\n"
printf "${C_G}║          ✓ 安装完成                                          ║${C_0}\n"
printf "${C_G}╚════════════════════════════════════════════════════════════╝${C_0}\n\n"
printf "打开应用：飞书（手机/桌面）→ 工作台 → 妙搭应用 → ${C_C}AgentCLI Bridge${C_0}\n"
printf "应用直链（参考）：${C_C}https://app.feishu.cn/%s${C_0}\n\n" "$APP_ID"
if [[ -z "$ALLOW_IDS" ]]; then
  warn "发指令白名单仍为空——发消息会被拒。从飞书发一条后，在服务器跑："
  printf "  ${C_C}lark-cli apps +db-execute --profile %s --as user --env online --app-id %s \\\n    --sql \"SELECT DISTINCT sender_open_id FROM commands WHERE result LIKE 'forbidden%%'\" --yes${C_0}\n" "$PROFILE" "$APP_ID"
  printf "把查到的 id 填进 .env.local 的 COPILOT_BRIDGE_ALLOW_OPEN_IDS，然后重启 bridge。\n\n"
fi
printf "文档：README.md · ${C_C}docs/INSTALL.md${C_0}（人工详解）· ${C_C}docs/INSTALL_FOR_AGENT.md${C_0}（给 agent）\n"
