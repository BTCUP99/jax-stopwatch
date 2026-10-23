<p align="center">
  <img src="docs/images/banner.png" alt="Jax StopWatch" width="100%">
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-3b82f6"></a>
  <img alt="macOS Apple silicon" src="https://img.shields.io/badge/Mac-Apple%20silicon-111827?logo=apple">
  <img alt="M5Stack StopWatch" src="https://img.shields.io/badge/device-M5Stack%20StopWatch-f97316">
  <img alt="Firmware 0.3.0" src="https://img.shields.io/badge/firmware-0.3.0-10b981">
  <img alt="Python 3.12" src="https://img.shields.io/badge/python-3.12-3776ab?logo=python&logoColor=white">
</p>

<p align="center"><a href="README.md">中文</a> · <b>English</b></p>

**Jax StopWatch** turns an [M5Stack StopWatch](https://docs.m5stack.com/) round-screen watch into a pocket AI remote. **Hold A, talk, release.** Your Mac transcribes the audio locally, hands the text to Codex or your own webhook agent, and speaks the answer back through the watch. It keeps working away from home over a phone hotspot, buzzes for new X followers, mentions and replies, and its on-screen companion can change outfits.

"Jax" (贾克斯) is the name of the desk pet. The UI and voice are in Chinese by default; docs are mostly Chinese with this English overview.

## Meet the companions

<table>
  <tr>
    <td align="center"><img src="docs/images/nuo-courier.gif" width="220" alt="Nuo"><br><b>Nuo</b> · pocket courier</td>
    <td align="center"><img src="docs/images/orbit-warden.gif" width="220" alt="Orbit"><br><b>Orbit</b> · quiet star mage</td>
    <td align="center"><img src="docs/images/little-host.gif" width="220" alt="Little Host"><br><b>Little Host</b> · show host</td>
  </tr>
</table>

Lips follow the voice level, eyes blink, and a happy face shows when a task is done. All three avatars are open source; [make your own](docs/avatars.md).

## Features

- **Push-to-talk** — hold A to speak; tap B to switch assistant, double-tap B to stop.
- **Two kinds of assistant** — [Codex](https://github.com/openai/codex) via `codex app-server` (uses your ChatGPT login, works on files in a local workspace), or any cloud agent reachable by webhook (Grok Bot routines, Cursor Automations, …) with a one-time callback.
- **Private speech** — Whisper small on MLX, offline on the Mac; replies via macOS `say`. Audio never leaves the machine.
- **Works anywhere** — USB when plugged in; otherwise home Wi-Fi straight to the Mac over pinned TLS, or a phone hotspot through Tailscale Funnel. Per-device keys, written over USB only.
- **X alerts** — follows, mentions, replies, reposts and quotes in real time: vibration, speech and an icon that expires after 30 minutes.
- **Swappable avatars** — 16 mouth × eye frames plus a happy face; upload and switch from the console without reflashing.
- **Local console** at `http://127.0.0.1:18765` for history, Codex approvals, wireless pairing and avatars.

## How it works

```mermaid
flowchart LR
  W["⌚ StopWatch"] -- "USB serial / Wi-Fi WSS<br/>(SCU1 frames)" --> B["Mac bridge<br/>(Python · aiohttp)"]
  B --> ASR["Whisper small (MLX)"]
  B --> C["Codex app-server"]
  B -- "task + one-time callback" --> A["Webhook agent"]
  A -. "result (Cloudflare quick tunnel)" .-> B
  X["X Activity API"] --> B
  B --> TTS["macOS say"] --> W
```

The firmware is a fork of [stackchan-pet-fw](https://github.com/Corvelis/stackchan-pet-fw), kept as the [`firmware/`](https://github.com/BTCUP99/stackchan-pet-fw/tree/mac-bridge) submodule. See [architecture](docs/architecture.md) for ports, files and the device protocol.

## Quick start

You need an M5Stack StopWatch, a USB-C data cable, an Apple silicon Mac and [uv](https://docs.astral.sh/uv/).

```sh
git clone --recursive https://github.com/BTCUP99/jax-stopwatch.git
cd jax-stopwatch
scripts/setup.sh                    # Python env + pinned, checksummed speech models (~500 MB)
scripts/start.command               # console at http://127.0.0.1:18765
scripts/flash.sh --first-install    # first flash (erases the watch; back it up first)
```

Back up the factory firmware before the first flash ([how](docs/setup.md#2-备份原厂固件)). Later updates use plain `scripts/flash.sh`, which keeps Wi-Fi pairing and avatars.

## Docs (Chinese)

[Setup & flashing](docs/setup.md) · [Daily use](docs/usage.md) · [Wireless](docs/wireless.md) · [Assistants & X alerts](docs/assistants.md) · [Avatar spec](docs/avatars.md) · [Architecture](docs/architecture.md)

## Security

The console listens on localhost only and rejects cross-site requests. Only `/device` (device key, rate-limited) and one-time callback URLs are reachable from outside. Secrets live in `runtime/` (mode 700) and are git-ignored. The watch never listens on a port.

## Credits & license

Firmware derived from [Corvelis/stackchan-pet-fw](https://github.com/Corvelis/stackchan-pet-fw) (MIT, © あいろぐ). Speech models: [Whisper](https://github.com/openai/whisper) via [mlx-community](https://huggingface.co/mlx-community/whisper-small-mlx), [Silero VAD](https://github.com/snakers4/silero-vad). See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Code and avatars are released under the [MIT License](LICENSE). M5Stack, StopWatch, Codex, Grok, X and Tailscale are trademarks of their owners; this project is not affiliated with them.
