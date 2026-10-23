# 交给 Agent 安装 · Agent-guided setup

这份文档写给 **AI 编程助手**（Claude Code、Codex 等）。用户把仓库地址发给你、请你帮忙安装时，按下面的步骤带着用户完成。用户可能完全不懂命令行：每一步先用一句白话说明要做什么，需要用户动手（插线、点按钮、登录）时停下来等他确认。

> 给用户：你只需要把这句话发给你的 Agent ——
> “请按 https://github.com/BTCUP99/jax-stopwatch 的 docs/agent-install.md 帮我安装 Jax StopWatch。”

## 原则

- **先问再做**：刷写固件前、清空设备前、开启公网入口前，都要得到用户明确同意。
- **先备份**：第一次刷机前必须完成原厂固件备份（第 4 步），并告诉用户备份文件在哪里。
- **不泄露**：不要在对话里显示 `runtime/` 里的内容、Wi-Fi 密码、Webhook key、X 凭据或设备密钥。需要用户填写的凭据，引导他在本机控制台 <http://127.0.0.1:18765> 里填，不要让他贴进聊天。
- **出错先诊断**：命令失败时读错误信息和 `runtime/bridge.log`，用白话解释原因和办法，不要盲目重试刷机。
- 本仓库的约束见 [AGENTS.md](../AGENTS.md)，原理见 [architecture.md](architecture.md)。

## 步骤

1. **检查环境**：`uname -sm` 必须是 `Darwin arm64`（Apple silicon Mac）。没有 [uv](https://docs.astral.sh/uv/) 就帮用户安装（`curl -LsSf https://astral.sh/uv/install.sh | sh`）。确认用户手上是 M5Stack **StopWatch** 和一根能传数据的 USB-C 线。
2. **获取代码**：`git clone --recursive https://github.com/BTCUP99/jax-stopwatch.git`，进入目录。
3. **安装**：运行 `scripts/setup.sh`（会下载约 500MB 模型，告诉用户需要几分钟）。然后 `scripts/start.command`，请用户在浏览器打开 <http://127.0.0.1:18765> 确认控制台能打开。
4. **备份原厂固件**（首次必做）：请用户插上手表并亮屏。按 [setup.md 第 2 节](setup.md#2-备份原厂固件) 读出整片 Flash，保存到用户指定的位置（不要放进仓库），并生成 SHA-256。备份失败就停下，不要继续刷机。
5. **刷入固件**：说明“这一步会清空手表并装上新系统，备份可以随时还原”，得到同意后运行 `scripts/flash.sh --first-install`（脚本里还会再确认一次）。完成后看控制台是否显示“设备已连接”，请用户按住 A 键说一句话试试。
6. **按需配置**（逐项问用户要不要）：
   - Codex：是否已安装并登录 Codex CLI（`codex login`），见 [assistants.md](assistants.md#codex)。
   - 开机自启动：`.venv/bin/python src/manage.py autostart on`。
   - 拔线也能用：[wireless.md](wireless.md)。在外使用要开 Tailscale Funnel，这是对公网开放端口的操作，先解释清楚再征得同意。
   - Webhook 助手（如 Grok Bot）：帮用户把 [例程指令模板](assistants.md#例程指令模板) 交给他的 Agent 平台，Webhook URL 和 key 让用户在控制台里填。
   - X 提醒：按 [assistants.md](assistants.md#x-提醒) 引导用户在 X 开发者后台操作，凭据在控制台里填。
7. **换形象**：控制台“形象管理”里选一套点“上传并使用”。

## 自制形象

用户想要自己的形象时，按 [avatars.md](avatars.md) 制作：先和用户聊清楚角色（性格、配色、是人物还是动物），用你的出图能力生成主视觉，再只在眼睛和嘴的区域合成 16 帧和开心表情，放到 `avatars/<id>/`，用规范第 7 节逐条自检后再让用户在控制台上传、到手表上看效果，按用户反馈迭代。

## 收尾

告诉用户：日常怎么用（[usage.md](usage.md)）、控制台地址、出了问题可以随时回来找你。
