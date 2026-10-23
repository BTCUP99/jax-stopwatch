# Agent notes

Guidance for AI coding agents (Codex, Claude Code, …) working in this repository.

- Mac side lives in `src/` (one aiohttp process, entry `src/bridge.py`); firmware is the `firmware/` submodule (PlatformIO, env `m5stack-stopwatch-release`). Use `.venv/bin/python`; restore with `uv sync --locked --python 3.12`.
- Run `.venv/bin/python -m unittest discover -s tests -v` after changing host logic.
- `runtime/` holds secrets and state (credentials, device key hashes, certificates, sessions, logs). Never print its contents, commit it, or loosen its permissions.
- The console on `127.0.0.1:18765` must stay local-only. Only `/device` on 18767/18768 and `/result/<job>` on 18766 may be reachable from outside, each behind its key or one-time token.
- Credentials (`net.config.set`) and avatar writes/deletes are accepted over USB only; keep that rule in firmware and host.
- Before serial work or flashing, make sure no task is running, stop the bridge (`src/manage.py stop`) so the port is free, and start it again afterwards.
- Protocol changes: gate new commands on `device.info.capabilities` and document them in `docs/architecture.md`.
- Asked to install this for a user? Follow `docs/agent-install.md` (backup before the first flash, consent before erasing or exposing ports, never echo secrets).
- Name-wake is host-side (device mic → Silero VAD → Whisper on the Mac); do not describe it as on-device wake word detection.
