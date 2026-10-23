"""Own a callback-only quick tunnel; never tunnel the local control dashboard."""
import asyncio
import contextlib
import re
import shutil


class QuickTunnel:
    def __init__(self, runtime, on_status):
        self.runtime = runtime
        self.on_status = on_status
        self.process = None
        self.reader_task = None
        self.ready = asyncio.Event()
        self.lock = asyncio.Lock()
        self.url = None
        self.closing = False

    async def ensure_ready(self):
        async with self.lock:
            if self.process is None or self.process.returncode is not None:
                executable = shutil.which("cloudflared") or "/opt/homebrew/bin/cloudflared"
                self.ready.clear()
                self.url = None
                self.closing = False
                self.process = await asyncio.create_subprocess_exec(
                    executable, "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:18766",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
                self.reader_task = asyncio.create_task(self.read_output())
            try:
                await asyncio.wait_for(self.ready.wait(), 45)
            except asyncio.TimeoutError:
                raise RuntimeError("Grok 结果回传入口启动超时，尚未派发任务")
            if not self.url or self.process.returncode is not None:
                raise RuntimeError("Grok 结果回传入口未连接，尚未派发任务")
            return self.url

    async def read_output(self):
        try:
            log_file = self.runtime / "grok-callback-managed.log"
            if log_file.exists() and log_file.stat().st_size > 1_000_000:
                log_file.replace(log_file.with_suffix(".log.1"))  # keep one previous log
            with log_file.open("ab") as log:
                while raw := await self.process.stdout.readline():
                    log.write(raw); log.flush()
                    line = raw.decode(errors="replace")
                    match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
                    if match:
                        self.url = match.group(0)
                    if "Registered tunnel connection" in line and self.url:
                        self.ready.set()
                        await self.on_status(True)
            await self.process.wait()
        finally:
            self.url = None
            self.ready.set()  # Wake a pending startup so it can fail without dispatching.
            if not self.closing:
                await self.on_status(False)

    async def close(self):
        self.closing = True
        if self.process and self.process.returncode is None:
            self.process.terminate()
            with contextlib.suppress(ProcessLookupError):
                await self.process.wait()
        if self.reader_task:
            await asyncio.gather(self.reader_task, return_exceptions=True)
