# Third-Party Notices

## Firmware

The StopWatch firmware (`firmware/`, released as `jax-stopwatch-*.bin`) is a modified version of
[Corvelis/stackchan-pet-fw](https://github.com/Corvelis/stackchan-pet-fw), MIT License,
Copyright (c) 2026 あいろぐ. Its own `LICENSE` and `THIRD_PARTY_NOTICES.md` (M5Unified, M5GFX,
ArduinoJson, NimBLE-Arduino, StackChan-BSP, M5PM1, M5IOE1, efont, qrcodegen) apply to the firmware
source and binaries and ship with every release.

The firmware embeds the ISRG Root X1 and X2 certificates (public trust anchors from Let's Encrypt)
to verify the Tailscale Funnel endpoint.

## Models (downloaded by `scripts/download_models.py`, not redistributed here)

| Model | Source | License |
| --- | --- | --- |
| Whisper small (MLX conversion) | [mlx-community/whisper-small-mlx](https://huggingface.co/mlx-community/whisper-small-mlx), from [openai/whisper](https://github.com/openai/whisper) | MIT |
| Silero VAD | [snakers4/silero-vad](https://github.com/snakers4/silero-vad) | MIT |

## Python dependencies (installed by uv, see `pyproject.toml` / `uv.lock`)

aiohttp (Apache-2.0), pyserial (BSD-3-Clause), mlx-whisper (MIT), huggingface-hub (Apache-2.0),
httpx (BSD-3-Clause), NumPy (BSD-3-Clause), PyTorch (BSD-3-Clause), Pillow (MIT-CMU).

## External tools used at runtime

Codex CLI (Apache-2.0), cloudflared (Apache-2.0), Tailscale (BSD-3-Clause client), esptool (GPL-2.0,
run as a separate tool, not distributed), PlatformIO (Apache-2.0).
