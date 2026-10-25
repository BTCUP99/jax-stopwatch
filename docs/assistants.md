# 助手与提醒

手表上说的话先在 Mac 本地转成文字，再交给当前助手。单击 B 在两类助手之间切换，或在一句话开头说“让 Codex……”“让 Grok……”。

## Codex

用官方 [Codex CLI](https://github.com/openai/codex) 的 `codex app-server`（JSON-RPC over stdio），沿用你在终端里的 ChatGPT 登录。

```sh
npm install -g @openai/codex   # 或 brew install codex
codex login
```

- 工作区：仓库里的 `workspace/`，沙箱为 workspace-write；需要权限的操作在控制台弹出审批，不会自动放行。
- 设备使用一个专用会话，编号存在 `runtime/codex-session.json`，后台重启后继续同一段对话。
- 默认要求用简体中文简短回复、适合语音播报；详细内容保存到工作区并说明位置。可在 `src/codex_client.py` 的 `developerInstructions` 修改。
- 费用：消耗你 ChatGPT 订阅里的 Codex 额度。

## Webhook 助手（Grok Bot 等）

任何能“收到 HTTPS 请求 → 执行任务 → 把结果 POST 回来”的云端 Agent 都可以接入，例如 Grok Bot 的例程、Cursor Automations 等。控制台和代码里这一路叫 “Grok”，但协议是通用的。

### 准备

```sh
brew install cloudflared
```

结果回传用 Cloudflare 的临时隧道（`cloudflared tunnel --url`），把一个只接受回调的本机端口 `127.0.0.1:18766` 暴露成随机 HTTPS 地址。隧道每次启动地址都会变，后台会自动把新地址随任务发出，Agent 那边不需要改配置。控制台本身永远不经过隧道。

> 为什么不用 Tailscale Funnel 回传？很多云端 Agent 的运行环境只能访问 443 等标准端口，访问不到 8443。

### 请求格式

Mac 向你配置的 Webhook 地址发送：

```http
POST <Webhook URL>
Authorization: Bearer <Webhook key>
Content-Type: application/json

{
  "source": "stopwatch-mac-bridge",
  "requestId": "4f1c…",
  "text": "帮我拟一段会议开场白，五十字以内",
  "callback": {
    "url": "https://<random>.trycloudflare.com/result/4f1c…",
    "token": "<一次性令牌>",
    "format": {"status": "completed", "text": "结果文本"}
  }
}
```

Agent 完成后回调：

```http
POST <callback.url>
Authorization: Bearer <callback.token>
Content-Type: application/json

{"status": "completed", "text": "简短的中文结果"}
```

失败时 `{"status": "failed", "text": "失败原因"}`。只有带正确任务编号和一次性令牌的回调才会被接受；Webhook 返回 HTTP 200 只代表“已接收”，不代表完成。Mac 最多等 15 分钟，不会自动重发。

### 例程指令模板

在你的 Agent 平台新建一个 Webhook 触发的例程，把下面这段作为指令（按需修改）：

```text
这是我的 StopWatch 语音任务入口，只在收到 Webhook 时运行，不设置定时执行。

每次触发时读取 JSON 请求体：
- source 应为 stopwatch-mac-bridge。
- requestId 是唯一任务编号；相同编号不要重复执行。
- text 是我这次的任务。
- callback 可选，含 url、token、format，用来把结果送回设备。

执行 text 指定的任务，沿用你现有的权限和审批规则；需要我确认时正常询问，不要自动绕过审批。

完成后，如果有 callback，用 HTTPS POST 向 callback.url 发送 JSON：
{"status":"completed","text":"简短的中文结果，适合小屏幕和语音播报"}；
失败则发送 {"status":"failed","text":"失败原因"}。
请求头 Authorization: Bearer <callback.token>。
只发送本次任务的结果，不附带其他聊天记录、凭据或文件。callback.token 是本次专用凭据，不要展示或写进结果。
```

建好后，把例程的 Webhook URL 和 key 填进控制台底部“Grok Bot 接入设置”。它们保存在 `runtime/grok.json`（权限 600），不会回显。

### 注意

- 每次任务都是 Agent 平台上一次独立运行，**不带之前的对话上下文**；消耗的是该平台的额度。
- 双击 B 只能停止 Mac 这边的等待，已派发的云端任务需要到平台上取消。

## X 提醒

新粉丝、@ 提及、回复、转发、引用会实时推到手表：振动 + 播报 + 首页右侧图标（每条 30 分钟后消失）。不订阅点赞。

五种提醒可以在控制台“X 提醒”里分别勾选。取消勾选的类型会从 X 上退订，不再推送，也不占 API 额度；重新勾选后自动订阅回来。

用的是 X API 的 Activity 长连接（`/activity/stream`），需要一个 X 开发者应用：

1. 在 [X 开发者后台](https://developer.x.com/) 的应用里打开 **User authentication settings**：类型选 Web App，权限 Read，回调地址填 `http://127.0.0.1:18765/x/callback`。
2. 从 **Keys and tokens** 复制 Bearer Token、OAuth 2.0 Client ID、Client Secret，填进控制台“X 提醒”并保存。
3. 点 **授权 X 账号**，在 X 页面同意后自动回到控制台。权限范围：`tweet.read users.read follows.read offline.access`，令牌自动续期。
4. 勾选“接收提醒”，点“测试提醒”确认设备能收到。

细节：

- 录音、执行任务或播报期间提醒会排队，空闲后再播；超过 6 小时的不再播报。
- **摇一摇重播**：首页还有提醒图标时，摇一摇手表会重播最新一条（振动一下再播）。图标 30 分钟后清除，之后摇晃不再响应；没有图标、设备忙或距上次重播不到 10 秒时也不响应，避免走路甩手误触。只在 Mac 记住的最新一条上生效，重启后台后要等下一条新提醒。控制台“形象管理”可关闭；“预览提醒标识”会附带一条可重播 2 分钟的测试提醒。
- 提醒正文只显示和播报，**不写入日志，也不会交给助手执行**，防止别人通过 @ 你来下指令。
- 凭据和令牌在 `runtime/x.json`（权限 600）。
- 费用按 X API 用量计（帖子类事件按条计费），请以 X 开发者后台的价格为准。

## 语音

- 识别：MLX 版 Whisper small，在 Mac 上离线运行；`src/speech.py` 会丢弃低置信度、复述提示词和字幕套话之类的幻觉转写。
- 合成：macOS 自带 `say`，声音和语速在控制台“播报声音”设置，保存在 `runtime/voice.json`。
- 叫名字唤醒（默认关闭）：设备持续收音 → Mac 本地 Silero VAD 检测人声 → Whisper 转写 → 匹配名字“贾克斯”和助手规则。它不是设备端唤醒，未唤醒的语音不保存、不发送。在控制台“叫名字唤醒”开启。
