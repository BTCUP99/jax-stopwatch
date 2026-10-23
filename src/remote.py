"""Network device link: an authenticated WebSocket that carries the same SCU1 frames as USB.

Only this listener (127.0.0.1:18767) is published through Tailscale Funnel on :8443. Each
device holds its own random token, written over USB; the Mac keeps only its SHA-256.
"""
import asyncio
import hashlib
import hmac
import json
import secrets
import shutil
import ssl
import subprocess
import time
from collections import defaultdict, deque

from aiohttp import web, WSMsgType

from device import Link

PORT = 18767
FUNNEL_PORT = 8443
LAN_PORT = 18768
MAX_FRAME = 64 * 1024


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Registry:
    """Per-device tokens in runtime/devices.json (0600)."""
    def __init__(self, runtime):
        self.file = runtime / "devices.json"
        self.loaded = None
        self.devices = {}
        self.reload()

    def reload(self):
        """Pick up changes made by other tools (pairing over USB, manual revocation)."""
        mtime = self.file.stat().st_mtime_ns if self.file.exists() else None
        if mtime != self.loaded:
            self.devices = json.loads(self.file.read_text()).get("devices", {}) if mtime else {}
            self.loaded = mtime

    def save(self):
        saved = self.file.with_suffix(".tmp")
        saved.write_text(json.dumps({"devices": self.devices}, ensure_ascii=False, indent=2))
        saved.chmod(0o600)
        saved.replace(self.file)
        self.loaded = self.file.stat().st_mtime_ns

    def issue(self, name, hardware=""):
        self.reload()
        device_id, token = "dev_" + secrets.token_hex(6), secrets.token_urlsafe(32)
        self.devices[device_id] = {"name": name, "hardware": hardware, "sha256": digest(token),
                                   "created": time.time(), "lastSeen": None, "revoked": False}
        self.save()
        return device_id, token

    def verify(self, device_id, token):
        self.reload()
        entry = self.devices.get(device_id)
        active = bool(entry) and not entry.get("revoked")
        # Compare even for unknown ids so timing does not reveal which ids exist.
        expected = entry["sha256"] if active else digest(secrets.token_hex(16))
        return hmac.compare_digest(digest(token or ""), expected) and active

    def revoke(self, device_id):
        self.reload()
        if device_id not in self.devices:
            raise ValueError("没有这台设备")
        self.devices[device_id]["revoked"] = True
        self.save()

    def forget(self, device_id):
        self.reload()
        self.devices.pop(device_id, None)
        self.save()

    def retire_others(self, device_id, hardware):
        """Re-pairing a device revokes its earlier keys."""
        self.reload()
        for key, entry in self.devices.items():
            if key != device_id and hardware and entry.get("hardware") == hardware:
                entry["revoked"] = True
        self.save()

    def seen(self, device_id):
        self.reload()
        self.devices[device_id]["lastSeen"] = time.time()
        self.save()

    def listing(self):
        self.reload()
        return [{"id": k, "name": v["name"], "created": v["created"], "lastSeen": v["lastSeen"],
                 "revoked": v.get("revoked", False)} for k, v in self.devices.items()]


class Throttle:
    """Failed-auth limits: per client (5 per 10 min → 15 min block) and overall (30 per minute)."""
    def __init__(self):
        self.failures = defaultdict(deque)
        self.blocked = {}
        self.recent = deque()

    def allowed(self, client):
        now = time.monotonic()
        while self.recent and now - self.recent[0] > 60:
            self.recent.popleft()
        return self.blocked.get(client, 0) <= now and len(self.recent) < 30

    def fail(self, client):
        now = time.monotonic()
        self.recent.append(now)
        attempts = self.failures[client]
        attempts.append(now)
        while attempts and now - attempts[0] > 600:
            attempts.popleft()
        if len(attempts) >= 5:
            self.blocked[client] = now + 900
            attempts.clear()
        if len(self.failures) > 1000:  # bound memory under a scan
            self.failures.clear()
            self.blocked = {k: v for k, v in self.blocked.items() if v > now}


class RemoteDevice(Link):
    """WebSocket transport; one live session, a newer authenticated one replaces it."""
    name = "remote"

    def __init__(self, on_event, on_audio):
        super().__init__(on_event, on_audio)
        self.ws = None
        self.device_id = None
        self.task = None
        self.via = None  # "lan" or "funnel"

    async def transmit(self, data):
        if self.ws is None or self.ws.closed:
            raise ConnectionError("无线设备未连接")
        await self.ws.send_bytes(data)

    async def read(self, ws):
        async for message in ws:
            if message.type != WSMsgType.BINARY:
                break
            await self.receive(message.data)

    async def serve(self, ws, device_id, via):
        await self.drop()
        self.ws, self.device_id, self.via = ws, device_id, via
        self.task = asyncio.current_task()
        reader = asyncio.create_task(self.read(ws))
        try:
            await self.session(reader)
        except asyncio.CancelledError:
            pass
        except Exception as error:
            await self.on_event(self, {"type": "bridge.disconnected", "message": f"无线连接断开：{error}"})
        else:
            await self.on_event(self, {"type": "bridge.disconnected", "message": "无线连接断开"})
        finally:
            self.fail_waiters()
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
            if not ws.closed:
                await ws.close()
            if self.ws is ws:
                self.ws = self.device_id = self.task = None

    async def drop(self):
        """Close the current session (USB took over, token revoked, or a reconnect replaced it)."""
        task = self.task
        if task and task is not asyncio.current_task() and not task.done():
            if self.ws is not None and not self.ws.closed:
                await self.ws.close()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def client_of(request):
    # Funnel terminates TLS and forwards from 127.0.0.1 with the public client in X-Forwarded-For.
    # Anything else (the LAN entrance) is a direct peer whose header must not be trusted.
    if request.remote in ("127.0.0.1", "::1"):
        forwarded = request.headers.get("X-Forwarded-For", "")
        return forwarded.split(",")[0].strip() or request.remote
    return request.remote or "?"


def ingress_app(registry, link, can_attach, attempts=None, via="funnel"):
    throttle = Throttle()
    attempts = attempts if attempts is not None else deque(maxlen=20)

    def note(result):
        # Outcome only (time, entrance, result); never headers or keys.
        attempts.append({"time": time.time(), "via": via, "result": result})

    async def device(request):
        client = client_of(request)
        if not throttle.allowed(client):
            note("throttled")
            raise web.HTTPTooManyRequests()
        device_id = request.headers.get("X-StopWatch-Device", "")
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if not registry.verify(device_id, token):
            note("bad_key")
            throttle.fail(client)
            await asyncio.sleep(1)
            raise web.HTTPUnauthorized()
        if not can_attach():
            note("usb_active")
            raise web.HTTPConflict(text="USB link active")
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=MAX_FRAME)
        if not ws.can_prepare(request).ok:
            note("not_websocket")
            raise web.HTTPBadRequest()
        note("accepted")
        await ws.prepare(request)
        registry.seen(device_id)
        await link.serve(ws, device_id, via)
        return ws

    async def nothing(request):
        raise web.HTTPNotFound()

    app = web.Application(client_max_size=MAX_FRAME)
    app.router.add_get("/device", device)
    app.router.add_route("*", "/{tail:.*}", nothing)
    return app


def lan_certificate(runtime):
    """Self-signed certificate for the LAN entrance; the device pins this exact certificate."""
    key, cert = runtime / "lan-key.pem", runtime / "lan-cert.pem"
    if not (key.exists() and cert.exists()):
        subprocess.run(["/usr/bin/openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout",
                        "-out", str(key)], check=True, capture_output=True)
        key.chmod(0o600)
        subprocess.run(["/usr/bin/openssl", "req", "-new", "-x509", "-key", str(key), "-out", str(cert),
                        "-days", "3650", "-subj", "/CN=StopWatch Bridge LAN"], check=True, capture_output=True)
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(cert, key)
    return cert.read_text(), context


def lan_address():
    """This Mac's private IPv4 on the wired or Wi-Fi interface, for the device's LAN attempt."""
    for interface in ("en0", "en1"):
        result = subprocess.run(["/usr/sbin/ipconfig", "getifaddr", interface], capture_output=True, text=True)
        address = result.stdout.strip()
        if address.startswith(("192.168.", "10.")) or address.startswith(tuple(f"172.{n}." for n in range(16, 32))):
            return address
    return None


async def funnel_status():
    """Read Tailscale's serve config for the :8443 device entrance (never touches :443)."""
    executable = shutil.which("tailscale") or "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
    try:
        process = await asyncio.create_subprocess_exec(executable, "serve", "status", "--json",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(process.communicate(), 10)
        config = json.loads(out or b"{}")
    except (OSError, ValueError, asyncio.TimeoutError):
        return {"ok": False, "url": None}
    for host_port, allowed in (config.get("AllowFunnel") or {}).items():
        if allowed and host_port.endswith(f":{FUNNEL_PORT}"):
            handlers = ((config.get("Web") or {}).get(host_port) or {}).get("Handlers") or {}
            proxy = (handlers.get("/") or {}).get("Proxy", "")
            host = host_port.rsplit(":", 1)[0]
            return {"ok": proxy.rstrip("/") == f"http://127.0.0.1:{PORT}",
                    "url": f"wss://{host}:{FUNNEL_PORT}/device"}
    return {"ok": False, "url": None}
