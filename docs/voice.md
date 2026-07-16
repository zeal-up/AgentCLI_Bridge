# 语音输入（Voice Input）

会话页的 🎤 按住说话功能有两条路径，**默认零配置走免费方案，云端 ASR 是可选项**。

## 两条路径

| | 浏览器 Web Speech（**默认，免费，零配置**） | Bridge WSS 中继（可选） |
|---|---|---|
| 需要后端 | 否 | 是（bridge + cloudflared） |
| 需要密钥/云服务 | 否 | DashScope API key（或本地 FunASR） |
| 桌面浏览器 | ✅ 实时 interim | ✅ 实时 |
| 飞书 Android WebView | ⚠️ 只在松手时出一坨文字（interim 不实时） | ✅ 实时流式出字 |
| 配置 | 无 | 见下 |

**为什么飞书 Android 上 Web Speech 不实时**：飞书 Android WebView 虽然 `webkitSpeechRecognition` 存在，但 `interimResults` 不会在说话过程中实时触发，只在结束时一次性吐结果。要做真·实时流式，必须页面采集 PCM → 中继到服务端 ASR → 流式回文字。这就是 Bridge 中继方案存在的理由。

**默认行为**：`COPILOT_BRIDGE_VOICE_ASR_BACKEND=none`（见 `.env.example`）。此时 bridge 不起语音中继，`/api/voice/config` 返回 `enabled:false`，页面自动用 Web Speech。别人 clone 下来、不配任何东西，拿到的就是免费 Web Speech。

**要飞书 Android 实时出字**：把后端切成 `dashscope`（云端）或 `funasr`（本地 GPU），见下。

## 架构（中继方案）

```
飞书 WebView (HTTPS 页面)
  │  1. GET /api/voice/config  (cookie → NestJS)  → {enabled, backend, wssUrl, token}
  │  2. WSS 连 wssUrl (cloudflared)，发 {auth, token}
  │  3. getUserMedia + ScriptProcessor → 降采样 16k Int16 → 二进制 PCM 帧
  ▼
cloudflared 隧道（TLS 终结，转发到 localhost:VOICE_RELAY_PORT）
  ▼
Bridge WSS 中继（asyncio + websockets）
  │  验 HMAC token + 查 userId ∈ ALLOWED_OPEN_IDS
  │  每个 utterance 连 ASR 后端，PCM↔文字泵送
  ▼
ASR 后端：DashScope paraformer-realtime-v2（云端）或 FunASR（本地 GPU）
```

- 妙达 aPaaS 是 HTTP-only 且云端托管，无法承载流式 WS，所以中继放 LAN bridge（GPU 也在同机）。
- cloudflared 提供公网 WSS 入口（HTTPS 页面只能连 WSS）。
- 鉴权复用既有 `COPILOT_BRIDGE_ALLOW_OPEN_IDS` 白名单：NestJS 用 `VOICE_RELAY_SECRET` 给 userId 签 HMAC 短时 token，bridge 验签 + 查白名单。无新增鉴权面。

## 启用 DashScope（云端，最简单）

**1. 生成共享 secret**（别提交、别贴对话）：
```bash
python3 -c "import secrets;print(secrets.token_urlsafe(32))"
```

**2. bridge `.env.local`**（gitignored）：
```bash
COPILOT_BRIDGE_VOICE_ASR_BACKEND=dashscope
COPILOT_BRIDGE_VOICE_RELAY_SECRET=<上面的 secret>
COPILOT_BRIDGE_DASHSCOPE_API_KEY=<你的百炼 API key>
# COPILOT_BRIDGE_VOICE_RELAY_PORT=8765   # 可选，默认 8765
```
装依赖 + 重启：
```bash
pip install -r bridge/requirements.txt   # websockets；dashscope 后端还需 pip install dashscope
./scripts/bridge-stop.sh && ./scripts/bridge-start.sh
./scripts/bridge-status.sh   # 应见 voice PID ✅ running (backend=dashscope)
```

**3. cloudflared 隧道**（公网 WSS 入口；临时隧道用 trycloudflare，生产用 named tunnel）：
```bash
# 临时（测试）：会打印 https://<随机>.trycloudflare.com
cloudflared tunnel --url http://localhost:8765
```

**4. 妙达控制台 env**（飞书妙搭 → 应用 → 环境变量，online 环境）：
```
VOICE_ASR_BACKEND=dashscope
VOICE_RELAY_PUBLIC_URL=wss://<cloudflared 域名>
VOICE_RELAY_SECRET=<与 bridge 同一个 secret>
```
设完需重新发布一次（aPaaS 启动 env 进程启动时读）：
```bash
lark-cli apps +release-create --app-id $COPILOT_BRIDGE_APP_ID --branch sprint/default --profile $COPILOT_BRIDGE_PROFILE --as user
# 轮询 +release-get 直到 finished
```

**5. 飞书 Android 测试**：进会话 → 点 🎤（进入语音模式，~0.5s 预连）→ 按住说话 → 松手。实时出中文，松手后语音模式保持开着（再点 🎤 退回键盘）。

> ⚠️ trycloudflare 域名是临时的，cloudflared 一重启就变，每次要更新妙达 `VOICE_RELAY_PUBLIC_URL` + 重新发布。生产用 named tunnel（稳定域名）。

## 启用本地 FunASR（本地 GPU，无云依赖，Phase 2）

FunASR 是 Apache 许可的自托管流式 ASR（与 DashScope 同模型族，GPU 加速）。适合不想用云的开发者。

```bash
# bridge .env.local
COPILOT_BRIDGE_VOICE_ASR_BACKEND=funasr
COPILOT_BRIDGE_VOICE_RELAY_SECRET=<secret>
COPILOT_BRIDGE_FUNASR_WSS_URL=ws://localhost:10095   # 本机 FunASR 服务
```
在 GPU 机器上跑 FunASR streaming 服务（Docker），然后同 DashScope 步骤 3-5（妙达 env `VOICE_ASR_BACKEND=funasr`）。

> 注：FunASR provider 已实现但**端到端尚未在飞书实测**（`bridge/voice/providers/funasr.py`）。握手/响应字段从 `FunASR/runtime/python/websocket/funasr_wss_client.py` 抄；Docker 镜像 tag 从 FunASR runtime 文档取。

## 环境变量总览

**bridge**（`.env.local`，gitignored）：
| 变量 | 默认 | 说明 |
|---|---|---|
| `COPILOT_BRIDGE_VOICE_ASR_BACKEND` | `none` | `none`(关/Web Speech) \| `echo`(调试) \| `dashscope` \| `funasr` |
| `COPILOT_BRIDGE_VOICE_RELAY_SECRET` | 空 | 与妙达同值；非空+backend≠none 才启用 |
| `COPILOT_BRIDGE_VOICE_RELAY_PORT` | `8765` | 本地监听口 |
| `COPILOT_BRIDGE_DASHSCOPE_API_KEY` | 空 | 仅 dashscope |
| `COPILOT_BRIDGE_FUNASR_WSS_URL` | `ws://localhost:10095` | 仅 funasr |
| `COPILOT_BRIDGE_ALLOW_OPEN_IDS` | 空 | 复用的白名单（命令注入也用它） |

**妙达**（控制台 env，不提交）：
| 变量 | 说明 |
|---|---|
| `VOICE_ASR_BACKEND` | 镜像 bridge 的 backend 选择（只影响回传给页面的标签 + enabled 判定） |
| `VOICE_RELAY_PUBLIC_URL` | cloudflared 的 `wss://...` |
| `VOICE_RELAY_SECRET` | 与 bridge 同值 |

## 开源洁癖

- 默认 `none` → Web Speech，零云依赖。fresh clone 无任何语音行为变化。
- 无密钥提交：`DASHSCOPE_API_KEY` / `VOICE_RELAY_SECRET` 只在 gitignored `.env.local` 和妙达控制台。
- 可插拔 provider：`bridge/voice/providers/{base,disabled,echo,dashscope,funasr}.py`，加新后端 = 新文件 + `make_provider` 一分支。
- DashScope 是可选项，非强制云。

## 延迟现状

- 点 🎤 进入语音模式：~0.5s（预连 WSS + 开 mic + 建音频图，前置到这一步）。
- 按住后到出第一个字：~0.7-1s（DashScope first-packet 固有延迟 + 中继往返）。
- 松手后语音模式保持开着，下次按住只付 ASR 重连（~0.3s），WSS 已温热。
