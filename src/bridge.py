import asyncio
import contextlib
import hmac
import html
import json
import os
import secrets
import signal
import time
import uuid
from collections import deque
from pathlib import Path

from aiohttp import web, ClientSession, ClientTimeout
from avatars import Library, chunks as avatar_chunks
from codex_client import CodexClient
from device import Device
from remote import (LAN_PORT, PORT as REMOTE_PORT, Registry, RemoteDevice, funnel_status, ingress_app,
                    lan_address, lan_certificate)
from speech import Speech, spoken_segments
from wake import SileroVoice, UtteranceGate, parse_voice, validate_name
from tunnel import QuickTunnel
from xwatch import XWatch

CODE = Path(__file__).resolve().parents[1]
# Private state, models and the workspace live in JAX_HOME (default: this checkout),
# so a release checkout can run against a data folder kept elsewhere.
ROOT = Path(os.environ.get("JAX_HOME") or CODE).expanduser()
RUNTIME = ROOT / "runtime"
PORT = 18765
BADGE_KINDS = ("follow", "reply", "mention", "repost", "quote")
BADGE_SECONDS = 30 * 60
MANUAL_URL = "https://github.com/BTCUP99/jax-stopwatch/blob/main/docs/usage.md"


def workspace():
    return Path(os.environ.get("JAX_WORKSPACE") or ROOT / "workspace").expanduser()


class Bridge:
    def __init__(self):
        self.state = {"phase": "starting", "agent": "codex", "connected": False,
                      "transcript": "", "reply": "", "message": "正在连接设备", "approvals": [],
                      "grokConfigured": False, "callbackReady": False, "codexReady": False, "recording": False,
                      "workdir": str(workspace())}
        self.history = []
        self.audio = bytearray()
        self.job = None
        self.record_timeout = None
        self.speak_lock = asyncio.Lock()
        self.usb = Device(self.on_link_event, self.on_link_audio)
        self.remote = RemoteDevice(self.on_link_event, self.on_link_audio)
        self.registry = Registry(RUNTIME)
        self.funnel = {"ok": False, "url": None}
        self.ingress_attempts = deque(maxlen=20)
        self.lan_cert, self.lan_ssl = lan_certificate(RUNTIME)
        self.speech = Speech(ROOT)
        self.codex = CodexClient(ROOT, self.emit, workspace())
        self.grok_config_file = RUNTIME / "grok.json"
        self.grok = json.loads(self.grok_config_file.read_text()) if self.grok_config_file.exists() else {}
        if self.grok.pop("callbackBase", None) is not None:
            self.save_grok()  # the tunnel address is managed automatically
        self.state["grokConfigured"] = bool(self.grok.get("url") and self.grok.get("key"))
        self.pending_grok = {}
        # Grok runs in a cloud sandbox that reaches only standard ports, so results return
        # through a Cloudflare quick tunnel (:443) to the callback-only listener on :18766.
        self.tunnel = QuickTunnel(RUNTIME, self.on_tunnel)
        self.tunnel_task = None
        self.device_task = None
        self.funnel_task = None
        self.wake_file = RUNTIME / "wake.json"
        wake = json.loads(self.wake_file.read_text()) if self.wake_file.exists() else {}
        self.wake_name = validate_name(wake.get("name", "贾克斯"))
        self.wake_enabled = wake.get("enabled") is True
        self.wake_gate = UtteranceGate()
        self.wake_vad = SileroVoice(ROOT / "models/silero-vad/silero_vad.jit")
        self.wake_streaming = False
        self.wake_processing = False
        self.wake_armed_until = 0.
        self.wake_ignore_until = 0.
        self.wake_epoch = 0
        self.wake_checks = 0
        self.wake_matches = 0
        self.wake_rejects = {}
        self.wake_last_heard = (0., "")
        self.wake_last_audio = 0.
        self.wake_speaker_pending = False
        self.wake_task = None
        self.x = XWatch(RUNTIME, self.queue_notice, self.emit, f"http://127.0.0.1:{PORT}/x/callback")
        self.notices = deque(maxlen=10)
        self.badges = deque(maxlen=48)  # (kind, time) shown as device badges for 30 minutes
        self.x_task = None
        self.notice_task = None
        self.shown_agent = None  # assistant last shown on the device screen
        self.avatars = Library(Path(os.environ.get("JAX_AVATARS") or CODE / "avatars").expanduser())
        self.device_avatars = None  # last avatar.list from the device
        self.avatar_upload = None  # {"id", "done", "total"} while uploading
        self.closing = False

    @property
    def device(self):
        """The active link: USB whenever it is up, otherwise the network link."""
        if self.usb.connected or not self.remote.connected:
            return self.usb
        return self.remote

    async def on_link_event(self, link, event):
        kind = event.get("type")
        if kind == "bridge.connected":
            if link is self.usb and self.remote.connected:
                asyncio.create_task(self.remote.drop())  # USB takes over
            elif link is self.remote and self.usb.connected:
                asyncio.create_task(self.remote.drop())
                return
        elif kind == "bridge.disconnected":
            if self.device.connected:
                return  # the other link is still up
        elif link is not self.device:
            return
        await self.on_device({**event, "link": link.name})

    async def on_link_audio(self, link, pcm):
        if link is self.device:
            await self.on_audio(pcm)

    async def watch_funnel(self):
        while True:
            self.funnel = await funnel_status()
            await asyncio.sleep(30)

    def wake_status(self):
        return {"enabled": self.wake_enabled, "name": self.wake_name,
                "listening": self.wake_streaming, "processing": self.wake_processing,
                "awaitingCommand": time.monotonic() < self.wake_armed_until,
                "checks": self.wake_checks, "matches": self.wake_matches, "rejects": self.wake_rejects,
                "lastNoSpeech": self.speech.last_no_speech,
                "lastHeard": self.wake_last_heard[1] if time.monotonic() - self.wake_last_heard[0] < 60 else "",
                "waitingForSpeaker": self.wake_speaker_pending,
                "level": round(self.wake_gate.last_rms, 4),
                "speechProbability": round(self.wake_vad.last_probability, 3)}

    def can_wake_listen(self):
        return (self.wake_enabled and not self.closing and self.device.connected
                and self.state["connected"] and not self.state["recording"]
                and self.state["phase"] in ("ready", "error")
                and not (self.job and not self.job.done())
                and not self.speak_lock.locked()
                and not self.wake_speaker_pending
                and time.monotonic() >= self.wake_ignore_until)

    async def pause_wake(self):
        was_streaming = self.wake_streaming
        self.wake_streaming = False
        self.wake_gate.reset()
        self.wake_vad.reset()
        if was_streaming:
            await self.device.idle()

    async def watch_wake(self):
        next_speaker_check = 0.
        while True:
            try:
                if (self.wake_speaker_pending and self.device.connected
                        and not self.speak_lock.locked() and time.monotonic() >= next_speaker_check):
                    next_speaker_check = time.monotonic() + 1
                    diag = await self.device.request("audio.playback_diag", timeout=3)
                    if not diag.get("draining") and not diag.get("speakerEnabled"):
                        self.wake_speaker_pending = False
                        self.wake_ignore_until = time.monotonic() + .35
                desired = self.can_wake_listen()
                if desired and not self.wake_streaming:
                    self.wake_gate.reset()
                    self.wake_vad.reset()
                    await self.device.send({"type": "state", "value": "listening"})
                    self.wake_streaming = True
                elif not desired and self.wake_streaming:
                    # Manual recording and playback own the audio state themselves.
                    self.wake_streaming = False
                    self.wake_gate.reset()
                    if not self.state["recording"] and not self.speak_lock.locked():
                        await self.device.idle()
                # The device stops streaming ~0.7 s after speech, short of the local silence window.
                if (self.wake_streaming and (self.wake_gate.audio or self.wake_gate.dropping)
                        and time.monotonic() - self.wake_last_audio > .3 and self.can_wake_listen()):
                    self.submit_wake(self.wake_gate.finish())
                if self.wake_armed_until and time.monotonic() >= self.wake_armed_until:
                    self.wake_armed_until = 0.
                    if self.state["phase"] == "ready":
                        self.state["message"] = f"等待呼叫{self.wake_name}，也可按住 A 讲话"
            except (ConnectionError, OSError, asyncio.TimeoutError):
                self.wake_streaming = False
                self.wake_gate.reset()
            await asyncio.sleep(.1)

    async def configure_wake(self, data):
        name = validate_name(data.get("name", self.wake_name))
        enabled = data.get("enabled", self.wake_enabled)
        if not isinstance(enabled, bool):
            raise ValueError("enabled 必须是布尔值")
        if self.state["recording"] or (self.job and not self.job.done()):
            raise RuntimeError("请等当前任务结束再修改唤醒设置")
        if enabled:
            await asyncio.to_thread(self.wake_vad.load)
        self.wake_epoch += 1
        self.wake_enabled, self.wake_name = enabled, name
        self.wake_armed_until = 0.
        await self.pause_wake()
        saved = self.wake_file.with_suffix(".tmp")
        saved.write_text(json.dumps({"enabled": enabled, "name": name}, ensure_ascii=False))
        saved.chmod(0o600)
        saved.replace(self.wake_file)
        self.state.update(phase="ready", message=f"语音唤醒已{'开启：' + name if enabled else '关闭'}")
        await self.emit("wake_configured", {"enabled": enabled, "name": name})

    async def process_wake_audio(self, pcm, epoch, armed):
        self.wake_processing = True
        try:
            await self.pause_wake()
            started = time.monotonic()
            text = await asyncio.to_thread(self.speech.transcribe, pcm, name=self.wake_name)
            self.wake_checks += 1
            if epoch != self.wake_epoch or not self.wake_enabled or not self.state["connected"]:
                return
            intent = parse_voice(text, self.wake_name, armed=armed)
            if intent is None:
                # Discard ambient transcription; never log or submit it. Kept in memory briefly for the console.
                self.wake_last_heard = (time.monotonic(), text)
                return
            self.wake_matches += 1
            self.wake_armed_until = 0.
            await self.emit("voice_wake", {"name": self.wake_name, "text": text,
                "followup": armed, "elapsed": round(time.monotonic() - started, 2)})
            await self.dispatch_voice(intent)
        except asyncio.CancelledError:
            raise
        except ValueError as error:
            # Quiet / unclear ambient speech is not a user-facing failure; count reasons only.
            self.wake_rejects[str(error)] = self.wake_rejects.get(str(error), 0) + 1
        except Exception as error:
            await self.fail(error)
        finally:
            self.wake_processing = False

    async def dispatch_voice(self, intent):
        if intent.agent:
            self.state["agent"] = intent.agent
            await self.emit("voice_agent", {"agent": intent.agent})
        if intent.kind == "wake":
            self.state.update(phase="speaking", transcript=self.wake_name, reply="我在，请说。")
            await self.speak("我在，请说。")
            self.wake_armed_until = time.monotonic() + 12
            self.state.update(phase="ready", message="请在 12 秒内开始说任务，无需再叫名字")
        elif intent.kind == "select":
            reply = "已切换到 Codex。" if intent.agent == "codex" else "已切换到 Grok Bot。"
            self.state.update(phase="speaking", transcript=f"切换到 {intent.agent}", reply=reply)
            await self.speak(reply)
            self.state.update(phase="ready", message=f"可以呼叫{self.wake_name}下达任务")
        else:
            await self.execute(intent.text)

    def save_grok(self):
        self.grok_config_file.write_text(json.dumps(self.grok))
        self.grok_config_file.chmod(0o600)

    async def on_tunnel(self, ready):
        self.state["callbackReady"] = ready
        await self.emit("grok_callback_ready" if ready else "grok_callback_disconnected", {})
        if not ready:
            for job in self.pending_grok.values():
                if not job["future"].done():
                    job["future"].set_exception(RuntimeError("结果回传连接断开；Grok 任务可能仍在运行，请在应用里查看，未自动重发"))

    async def prepare_tunnel(self):
        try:
            await self.tunnel.ensure_ready()
        except Exception as error:
            await self.emit("notice", {"text": str(error)})

    async def emit(self, kind, data):
        if kind == "codex_ready":
            self.state["codexReady"] = True
            self.state["codexPlan"] = data.get("plan")
        elif kind == "codex_delta":
            self.state["reply"] = data["text"]
            return
        elif kind == "approval":
            self.state["phase"] = "approval"
            self.state["approvals"].append(data)
        elif kind == "notice":
            self.state["message"] = data["text"]
        item = {"time": time.time(), "kind": kind, **data}
        self.history.append(item)
        self.history = self.history[-100:]
        with (RUNTIME / "events.jsonl").open("a") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    async def on_device(self, event):
        kind = event.get("type")
        if kind == "bridge.connected":
            self.wake_streaming = False
            self.wake_gate.reset()
            via = {"lan": "（局域网）", "funnel": "（公网）"}.get(self.remote.via, "") if event["link"] == "remote" else ""
            self.state.update(connected=True, link=event["link"], via=self.remote.via if event["link"] == "remote" else None,
                              message="设备已连接" + via, device=event["info"])
            if self.state["phase"] in ("starting", "disconnected"):
                self.state["phase"] = "ready"
            await self.emit("device_connected", {"version": event["info"].get("macBridgeVersion"), "link": event["link"]})
            self.shown_agent = None
            asyncio.create_task(self.button_action(self.push_badges()))
            asyncio.create_task(self.button_action(self.send_lan_hint()))
            asyncio.create_task(self.sync_clock())
            self.device_avatars = None
            asyncio.create_task(self.button_action(self.refresh_avatars()))
        elif kind == "bridge.disconnected":
            self.wake_epoch += 1
            self.wake_streaming = False
            self.wake_armed_until = 0.
            self.wake_gate.reset()
            self.state.update(connected=False, link=None, message=event.get("message") or "设备暂未响应")
            # Never submit a partial utterance after an unplug or display-off.
            if self.state["recording"]:
                self.state["recording"] = False
                self.audio.clear()
                self.state["phase"] = "disconnected"
        elif kind == "bridge.ptt":
            if event.get("pressed"):
                asyncio.create_task(self.button_action(self.start_recording(from_device=True)))
            else:
                asyncio.create_task(self.button_action(self.stop_recording()))
        elif kind == "bridge.action":
            if event.get("action") == "cancel":
                asyncio.create_task(self.button_action(self.cancel()))
            elif event.get("action") == "next_agent":
                asyncio.create_task(self.button_action(self.select_agent("grok" if self.state["agent"] == "codex" else "codex")))
        elif kind in ("display.speech_bubble.error", "error"):
            await self.emit("device_error", {"event": event})

    async def sync_clock(self):
        """The USB build has no Wi-Fi NTP, so the Mac keeps the device RTC on time."""
        try:
            before = await self.device.request("streetpass.time.get", timeout=5)
            now = int(time.time())
            drift = int(before.get("unixTime") or 0) - now
            result = {"driftSeconds": drift, "corrected": abs(drift) >= 2}
            if result["corrected"]:
                await self.device.request("streetpass.time.set", timeout=5, unixTime=now)
                after = await self.device.request("streetpass.time.get", timeout=5)
                result["afterSeconds"] = int(after.get("unixTime") or 0) - int(time.time())
            await self.emit("clock_synced", result)
        except (ConnectionError, OSError, asyncio.TimeoutError) as error:
            await self.emit("notice", {"text": f"设备时间同步未完成：{error or type(error).__name__}"})

    async def button_action(self, action):
        try:
            await action
        except Exception as error:
            await self.emit("notice", {"text": str(error)})

    async def on_audio(self, pcm):
        if self.state["recording"] and len(self.audio) < 32_000 * 30:
            self.audio.extend(pcm)
            self.state["recordedSeconds"] = round(len(self.audio) / 32000, 1)
        elif self.wake_streaming and self.can_wake_listen():
            # Capture the follow-up entitlement at speech onset, not after ASR.
            if not self.wake_gate.audio:
                self.wake_segment_armed = time.monotonic() < self.wake_armed_until
            self.wake_last_audio = time.monotonic()
            self.submit_wake(self.wake_gate.feed(pcm, self.wake_vad.probability(pcm)))

    def submit_wake(self, utterance):
        if utterance:
            self.job = asyncio.create_task(self.process_wake_audio(
                utterance, self.wake_epoch, self.wake_segment_armed))

    async def start_recording(self, from_device=False):
        if self.state["recording"]:
            return
        if not self.device.connected:
            raise RuntimeError("请先连接 StopWatch")
        if self.job and not self.job.done():
            await self.cancel()
        self.wake_epoch += 1
        self.wake_armed_until = 0.
        await self.pause_wake()
        self.audio = bytearray()
        self.state.update(recording=True, phase="recording", recordedSeconds=0,
                          message="正在录音，松开 A 键发送", reply="", transcript="")
        await self.device.send({"type": "state", "value": "listening"})
        self.record_timeout = asyncio.create_task(self.limit_recording())

    async def limit_recording(self):
        await asyncio.sleep(30)
        if self.state["recording"]:
            await self.stop_recording()

    async def stop_recording(self):
        if not self.state["recording"]:
            return
        self.state["recording"] = False
        if self.record_timeout and self.record_timeout is not asyncio.current_task():
            self.record_timeout.cancel()
        await self.device.idle()
        pcm = bytes(self.audio)
        self.audio.clear()
        self.job = asyncio.create_task(self.process_audio(pcm))

    async def process_audio(self, pcm):
        try:
            self.state.update(phase="transcribing", message="Mac 正在识别语音")
            started = time.monotonic()
            text = await asyncio.to_thread(self.speech.transcribe, pcm, name=self.wake_name)
            await self.emit("transcription", {"text": text, "audioSeconds": round(len(pcm) / 32000, 2),
                                             "elapsed": round(time.monotonic() - started, 2)})
            await self.dispatch_voice(parse_voice(text, self.wake_name, armed=True))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await self.fail(error)

    async def process_text(self, text):
        try:
            await self.execute(text)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await self.fail(error)

    async def execute(self, text):
        agent = self.state["agent"]
        self.state.update(transcript=text, reply="", phase="working", message=f"{agent} 正在处理")
        await self.emit("request", {"agent": agent, "text": text})
        if agent == "codex":
            reply = await self.codex.run(text)
        else:
            reply = await self.run_grok(text)
        self.state.update(reply=reply, phase="speaking", message="正在播报")
        await self.emit("result", {"agent": agent, "text": reply})
        await self.speak(reply)
        if self.device.connected and self.device.info.get("macBridgeVersion"):
            await self.device.send({"type": "bridge.haptic", "durationMs": 100})
        await self.show_happy()
        self.state.update(phase="ready", message=f"呼叫{self.wake_name}或按住 A 继续讲话" if self.wake_enabled else "按住 A 键继续讲话")

    async def speak(self, text):
        async with self.speak_lock:
            await self.pause_wake()
            try:
                for part in spoken_segments(text):
                    pcm = await asyncio.to_thread(self.speech.synthesize, part)
                    if self.device.connected:
                        self.wake_speaker_pending = True
                        await self.device.play(pcm, part)
                if self.device.connected:
                    # The firmware can drain past the nominal PCM duration.
                    # Wait for its speaker to stop before enabling wake capture.
                    for _ in range(30):
                        diag = await self.device.request("audio.playback_diag", timeout=3)
                        if not diag.get("draining") and not diag.get("speakerEnabled"):
                            self.wake_speaker_pending = False
                            break
                        await asyncio.sleep(.1)
            finally:
                self.wake_ignore_until = time.monotonic() + .35
                self.wake_gate.reset()

    async def queue_notice(self, kind, text):
        self.notices.append((time.time(), text))
        if kind in BADGE_KINDS:
            self.badges.append((kind, time.time()))
            await self.push_badges()

    def supports(self, capability):
        return capability in (self.device.info.get("capabilities") or [])

    async def push_badges(self):
        now = time.time()
        while self.badges and now - self.badges[0][1] >= BADGE_SECONDS:
            self.badges.popleft()
        if self.device.connected and self.supports("notify.badges"):
            await self.device.send({"type": "notify.set", "items": [
                {"kind": kind, "ttlSec": max(1, int(BADGE_SECONDS - (now - at)))} for kind, at in self.badges]})

    async def show_happy(self):
        """Task finished or notice delivered: the avatar's happy frame, once speech has drained."""
        if self.device.connected and self.supports("face.happy"):
            await self.device.send({"type": "face.happy", "durationMs": 2000})

    async def refresh_avatars(self):
        if self.device.connected and self.supports("avatar.store"):
            self.device_avatars = await self.device.request("avatar.list", timeout=10)

    def avatar_status(self):
        listing = self.device_avatars or {}
        return {"supported": self.supports("avatar.store"), "local": self.avatars.listing(),
                "device": listing.get("avatars"), "active": listing.get("active"),
                "fsTotal": listing.get("fsTotal"), "fsUsed": listing.get("fsUsed"), "upload": self.avatar_upload}

    def require_idle(self):
        if self.state["recording"] or (self.job and not self.job.done()) or self.speak_lock.locked():
            raise RuntimeError("当前任务尚未结束")

    async def upload_avatar(self, avatar_id, activate=False):
        """Convert a delivered set and write it to the device over USB, verifying each file."""
        if not (self.usb.connected and self.device is self.usb):
            raise RuntimeError("请先用 USB 连接 StopWatch 再上传形象")
        if not self.supports("avatar.store"):
            raise RuntimeError("设备固件不支持形象管理，请先刷入 0.3.0")
        files = await asyncio.to_thread(self.avatars.files, avatar_id)
        requests = [part for name, data in files for part in avatar_chunks(avatar_id, name, data)]
        self.avatar_upload = {"id": avatar_id, "done": 0, "total": len(requests)}
        try:
            for part in requests:
                result = await self.usb.request("avatar.put", timeout=10, **part)
                if not result.get("ok"):
                    raise RuntimeError(f"设备未接受 {part['file']}：{result.get('error')}")
                self.avatar_upload["done"] += 1
        finally:
            self.avatar_upload = None
        await self.emit("avatar_uploaded", {"id": avatar_id, "files": len(files),
                                            "bytes": sum(len(data) for _, data in files)})
        # Re-selecting also drops the device's decoded frames of a replaced active set.
        if activate or (self.device_avatars or {}).get("active") == avatar_id:
            await self.select_avatar(avatar_id)
        else:
            await self.refresh_avatars()

    async def select_avatar(self, avatar_id):
        if not self.supports("avatar.store"):
            raise RuntimeError("设备固件不支持形象管理")
        result = await self.device.request("avatar.select", timeout=10, id=avatar_id)
        if not result.get("ok"):
            raise RuntimeError({"incomplete": "设备上这套形象不完整，请重新上传"}.get(result.get("error"),
                               f"切换失败：{result.get('error')}"))
        await self.emit("avatar_selected", {"id": avatar_id or "built-in"})
        await self.refresh_avatars()

    async def delete_avatar(self, avatar_id):
        if not (self.usb.connected and self.device is self.usb):
            raise RuntimeError("请先用 USB 连接 StopWatch 再删除形象")
        result = await self.usb.request("avatar.delete", timeout=10, id=avatar_id)
        if not result.get("ok"):
            raise RuntimeError(f"删除失败：{result.get('error')}")
        await self.emit("avatar_deleted", {"id": avatar_id})
        await self.refresh_avatars()

    async def send_lan_hint(self):
        """Keep the device's LAN address current (DHCP may move it); the pinned cert guards it."""
        address = lan_address()
        if address and self.device.connected and self.supports("net.lan"):
            await self.device.send({"type": "net.lan.hint", "ip": address})

    async def pair_network(self, data):
        """Write Wi-Fi and a fresh device key over USB; the key never travels over the network."""
        if not (self.usb.connected and self.device is self.usb):
            raise RuntimeError("请先用 USB 连接 StopWatch 再配对")
        if not self.supports("net.config"):
            raise RuntimeError("设备固件不支持无线组网，请先刷入 0.2.0")
        if not self.funnel.get("ok"):
            raise RuntimeError("Mac 的无线入口未开启（Tailscale Funnel 8443）")
        wifi = [{"ssid": str(w.get("ssid", "")).strip(), "password": str(w.get("password", ""))}
                for w in data.get("wifi", []) if str(w.get("ssid", "")).strip()]
        saved = (self.usb.info.get("netLink") or {}).get("ssids") or []
        if len(wifi) > 4 or (not wifi and not saved):
            raise ValueError("请填写 1–4 个 Wi-Fi（留空则保留设备里已保存的网络）")
        hardware = self.usb.info.get("deviceId", "")
        device_id, token = self.registry.issue(str(data.get("name") or "StopWatch"), hardware)
        lan = {"ip": lan_address() or "", "port": LAN_PORT, "cert": self.lan_cert} if self.supports("net.lan") else None
        if not wifi:
            await self.emit("notice", {"text": "保留设备已保存的 Wi-Fi：" + "、".join(saved)})
        result = await self.usb.request("net.config.set", timeout=10, wifi=wifi, url=self.funnel["url"],
                                        deviceId=device_id, token=token, **({"lan": lan} if lan else {}))
        if not result.get("ok"):
            self.registry.forget(device_id)
            raise RuntimeError(f"设备未接受配置：{result.get('error')}")
        self.registry.retire_others(device_id, hardware)
        self.usb.info["netLink"] = result.get("netLink")
        await self.emit("network_paired", {"deviceId": device_id, "wifiCount": len(wifi)})

    async def watch_notices(self):
        """Announce X notices between tasks; never interrupt recording, work or playback.
        Also keeps the on-screen assistant name in step with every way of switching it."""
        while True:
            await asyncio.sleep(.5)
            if (self.device.connected and self.shown_agent != self.state["agent"]
                    and self.supports("display.agent")):
                with contextlib.suppress(ConnectionError, OSError):
                    await self.device.send({"type": "bridge.agent", "agent": self.state["agent"]})
                    self.shown_agent = self.state["agent"]
            while self.notices and time.time() - self.notices[0][0] > 6 * 3600:
                self.notices.popleft()
            if (self.notices and self.device.connected and self.state["connected"] and not self.state["recording"]
                    and self.state["phase"] in ("ready", "error") and not (self.job and not self.job.done())
                    and not self.speak_lock.locked() and not self.wake_processing):
                self.job = asyncio.create_task(self.announce(self.notices.popleft()[1]))

    async def announce(self, text):
        try:
            if self.device.info.get("macBridgeVersion"):
                await self.device.send({"type": "bridge.haptic", "durationMs": 200})
            self.state.update(phase="speaking", message="X 提醒", reply=text)
            await self.speak(text)
            await self.show_happy()
            self.state.update(phase="ready", message="按住 A 键讲话")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await self.fail(error)

    async def fail(self, error):
        message = str(error) or type(error).__name__
        self.state.update(phase="error", message=message)
        await self.emit("error", {"message": message})
        await self.device.idle()

    async def cancel(self):
        self.wake_epoch += 1
        self.wake_armed_until = 0.
        await self.pause_wake()
        self.state["recording"] = False
        self.audio.clear()
        if self.record_timeout:
            self.record_timeout.cancel()
        if self.job and not self.job.done():
            self.job.cancel()
            await asyncio.gather(self.job, return_exceptions=True)
        await self.device.idle()
        message = "已停止，可以重新按住 A 键讲话"
        if self.state["agent"] == "grok":
            message = "已停止本机处理；已提交的 Grok 云端任务需在 Grok Bot 中取消"
        self.state.update(phase="ready", message=message, approvals=[])

    async def select_agent(self, agent):
        if agent not in ("codex", "grok"):
            raise ValueError("不支持的 Agent")
        if self.state["recording"] or (self.job and not self.job.done()):
            raise RuntimeError("当前任务仍在进行，请先停止再切换")
        self.state.update(agent=agent, message="已选择 " + agent)
        await self.speak("已切换到 Codex。" if agent == "codex" else "已切换到 Grok Bot。")

    async def run_grok(self, text):
        if self.grok_config_file.exists():
            self.grok = json.loads(self.grok_config_file.read_text())
        self.state["grokConfigured"] = bool(self.grok.get("url") and self.grok.get("key"))
        if not self.state["grokConfigured"]:
            raise RuntimeError("请先在控制台配置 Grok Bot 的专用 Webhook")
        self.state.update(message="正在连接 Grok 结果回传入口")
        callback = await self.tunnel.ensure_ready()
        request_id = uuid.uuid4().hex
        token = secrets.token_urlsafe(32)
        future = asyncio.get_running_loop().create_future()
        self.pending_grok[request_id] = {"token": token, "future": future}
        payload = {"source": "stopwatch-mac-bridge", "requestId": request_id, "text": text}
        if callback:
            payload["callback"] = {"url": callback + "/result/" + request_id, "token": token,
                                   "format": {"status": "completed", "text": "结果文本"}}
        try:
            async with ClientSession(timeout=ClientTimeout(total=30), trust_env=True) as session:
                async with session.post(self.grok["url"], headers={"Authorization": "Bearer " + self.grok["key"]}, json=payload) as response:
                    if response.status != 200:
                        raise RuntimeError(f"Grok Bot 未接受任务（HTTP {response.status}），未自动重试")
            await self.emit("grok_accepted", {"requestId": request_id, "hasCallback": bool(callback)})
            self.state.update(phase="grok_waiting", message="Grok Bot 已接收，等待完成结果")
            if not callback:
                return "Grok Bot 已接收任务。结果回传入口未连接，请在 Grok Bot 查看结果。"
            return await asyncio.wait_for(asyncio.shield(future), 900)
        except asyncio.TimeoutError:
            raise RuntimeError("等待 Grok Bot 结果超时；任务可能仍在云端运行，请勿重复派发")
        finally:
            self.pending_grok.pop(request_id, None)

    async def start(self):
        if self.wake_enabled:
            await asyncio.to_thread(self.wake_vad.load)
        self.wake_task = asyncio.create_task(self.watch_wake())
        self.notice_task = asyncio.create_task(self.watch_notices())
        self.x_task = asyncio.create_task(self.x.run())
        # Diagnostics: STOPWATCH_DIAG_NO_USB=1 leaves the port to a serial log reader, so the device
        # runs its network link while still powered and logging over USB.
        if not os.environ.get("STOPWATCH_DIAG_NO_USB"):
            self.device_task = asyncio.create_task(self.usb.run())
        self.funnel_task = asyncio.create_task(self.watch_funnel())
        if self.grok.get("url"):
            self.tunnel_task = asyncio.create_task(self.prepare_tunnel())
        try:
            await self.codex.start()
        except Exception as error:
            await self.fail(error)

    async def close(self):
        self.closing = True
        if self.wake_task:
            self.wake_task.cancel()
            await asyncio.gather(self.wake_task, return_exceptions=True)
        for task in (self.x_task, self.notice_task):
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        await self.x.close()
        await self.cancel()
        if self.device.connected and self.device.info.get("macBridgeVersion"):
            await self.device.send({"type": "bridge.configure", "enabled": False})
        if self.tunnel_task:
            self.tunnel_task.cancel()
            await asyncio.gather(self.tunnel_task, return_exceptions=True)
        await self.tunnel.close()
        await self.remote.drop()
        if self.funnel_task:
            self.funnel_task.cancel()
        if self.device_task:
            self.device_task.cancel()
            await asyncio.gather(self.device_task, return_exceptions=True)
        await self.codex.close()


@web.middleware
async def local_only(request, handler):
    if request.remote not in ("127.0.0.1", "::1"):
        raise web.HTTPForbidden()
    if request.host not in (f"127.0.0.1:{PORT}", f"localhost:{PORT}"):
        raise web.HTTPForbidden()
    if request.method != "GET":
        if request.headers.get("X-StopWatch-Client") != "local":
            raise web.HTTPForbidden()
        origin = request.headers.get("Origin")
        if origin and origin not in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"):
            raise web.HTTPForbidden()
    try:
        return await handler(request)
    except (ValueError, RuntimeError) as error:
        return web.json_response({"error": str(error)}, status=400)


async def main():
    os.umask(0o077)
    RUNTIME.mkdir(mode=0o700, exist_ok=True)
    events = RUNTIME / "events.jsonl"
    if events.exists() and events.stat().st_size > 5_000_000:
        events.replace(RUNTIME / "events.jsonl.1")  # keep one previous event log
    bridge = Bridge()
    app = web.Application(middlewares=[local_only], client_max_size=65536)

    async def index(request):
        return web.FileResponse(CODE / "src/dashboard.html", headers={"Cache-Control": "no-store"})

    async def manual(request):
        raise web.HTTPFound(MANUAL_URL)

    async def state(request):
        return web.json_response({**bridge.state, "service": "stopwatch-mac-bridge", "pid": os.getpid(),
            "wake": bridge.wake_status(), "x": bridge.x.status(),
            "remote": {"funnel": bridge.funnel, "devices": bridge.registry.listing(),
                       "netLink": bridge.device.info.get("netLink"), "pairable": bridge.supports("net.config"),
                       "attempts": list(bridge.ingress_attempts)[-5:],
                       "online": bridge.remote.device_id if bridge.remote.connected else None},
            "avatars": bridge.avatar_status(),
            "voice": {"voice": bridge.speech.voice, "rate": bridge.speech.rate, "voices": bridge.speech.voices},
            "history": bridge.history[-30:],
            "micPackets": bridge.device.mic_packets, "micGaps": bridge.device.mic_gaps,
            "badCrc": bridge.device.decoder.bad_crc, "threadId": bridge.codex.thread_id})

    async def action(request):
        data = await request.json()
        command = request.match_info["action"]
        if command == "record/start":
            await bridge.start_recording()
        elif command == "record/stop":
            await bridge.stop_recording()
        elif command == "cancel":
            await bridge.cancel()
        elif command == "agent":
            await bridge.select_agent(data["agent"])
        elif command == "wake/config":
            await bridge.configure_wake(data)
        elif command == "text":
            text = str(data.get("text", "")).strip()
            if not text or len(text) > 12000:
                raise ValueError("请输入 1–12000 字的指令")
            if bridge.state["recording"] or (bridge.job and not bridge.job.done()):
                raise ValueError("当前任务尚未结束")
            bridge.job = asyncio.create_task(bridge.process_text(text))
        elif command == "voice/config":
            if bridge.state["recording"] or (bridge.job and not bridge.job.done()):
                raise ValueError("当前任务尚未结束")
            bridge.speech.set_voice(data.get("voice"), data.get("rate"))
            if data.get("save") is True:
                bridge.speech.save_voice()
            bridge.job = asyncio.create_task(bridge.speak(f"我是{bridge.wake_name}，现在用这个声音播报。"))
        elif command == "speak":
            if bridge.job and not bridge.job.done():
                raise ValueError("当前任务尚未结束")
            bridge.job = asyncio.create_task(bridge.speak("StopWatch 已连接。按住 A 键说话，松开发送。"))
        elif command == "device/diagnostics":
            result = await bridge.device.request("audio.playback_diag")
            return web.json_response(result)
        elif command == "approve":
            await bridge.codex.approve(data["id"], data.get("accept") is True)
            bridge.state["approvals"] = [x for x in bridge.state["approvals"] if x["id"] != data["id"]]
            bridge.state["phase"] = "working"
        elif command == "x/config":
            bridge.x.configure(data)
        elif command == "x/test":
            await bridge.queue_notice("test", "测试提醒：X 提醒已经接到这台设备。")
        elif command == "remote/pair":
            await bridge.pair_network(data)
        elif command == "remote/badges/preview":
            for kind in BADGE_KINDS:
                bridge.badges.append((kind, time.time() - BADGE_SECONDS + 120))
            await bridge.push_badges()
        elif command == "remote/revoke":
            bridge.registry.revoke(str(data.get("id", "")))
            if bridge.remote.device_id == data.get("id"):
                await bridge.remote.drop()
        elif command == "avatar/upload":
            bridge.require_idle()
            bridge.job = asyncio.create_task(bridge.button_action(
                bridge.upload_avatar(str(data.get("id", "")), data.get("activate") is True)))
        elif command == "avatar/select":
            bridge.require_idle()
            await bridge.select_avatar(str(data.get("id", "")))
        elif command == "avatar/delete":
            bridge.require_idle()
            await bridge.delete_avatar(str(data.get("id", "")))
        elif command == "avatar/happy":
            await bridge.show_happy()
        elif command == "grok/config":
            url, key = str(data.get("url", "")).strip(), str(data.get("key", "")).strip()
            if not url.startswith("https://") or not key:
                raise ValueError("请输入 Grok Bot 的 HTTPS Webhook 地址和 key")
            bridge.grok = {"url": url, "key": key}
            bridge.save_grok()
            bridge.state["grokConfigured"] = True
        else:
            raise web.HTTPNotFound()
        return web.json_response({"ok": True})

    app.router.add_get("/", index)
    app.router.add_get("/manual", manual)
    async def x_authorize(request):
        raise web.HTTPFound(bridge.x.authorize_url())

    async def x_callback(request):
        if request.query.get("error"):
            message = "你在 X 页面取消了授权。"
        else:
            try:
                await bridge.x.complete(request.query.get("code", ""), request.query.get("state", ""))
                message = "X 账号授权完成，可以关闭此页，回到控制台查看状态。"
            except Exception as error:
                message = "授权未完成：" + str(error)
        return web.Response(text=f"<!doctype html><meta charset=utf-8><title>X 授权</title>"
                            f"<body style='font:16px -apple-system;padding:40px'>{html.escape(message)}</body>",
                            content_type="text/html")

    async def avatar_preview(request):
        folder = bridge.avatars.folder(request.match_info["id"])
        name = "happy.png" if request.query.get("happy") and (folder / "happy.png").is_file() else "base_m0_e0.png"
        return web.FileResponse(folder / name)

    app.router.add_get("/api/state", state)
    app.router.add_get("/avatars/{id}/preview", avatar_preview)
    app.router.add_get("/x/authorize", x_authorize)
    app.router.add_get("/x/callback", x_callback)
    app.router.add_post("/api/{action:.*}", action)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", PORT).start()

    # Callback-only listener for Grok results; the dashboard is never tunnelled.
    # Each job has a random id and a one-time token.
    callback_app = web.Application(client_max_size=65536)
    async def grok_result(request):
        job = bridge.pending_grok.get(request.match_info["job"])
        provided = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if not job or not hmac.compare_digest(provided, job["token"]):
            raise web.HTTPForbidden()
        try:
            data = await request.json()
        except ValueError:
            raise web.HTTPBadRequest(text="Expected JSON")
        if job["future"].done():
            return web.json_response({"ok": True, "duplicate": True})
        text = str(data.get("text", ""))[:20000]
        if data.get("status") == "failed":
            job["future"].set_exception(RuntimeError("Grok Bot：" + text))
        elif data.get("status") == "completed" and text:
            job["future"].set_result(text)
        else:
            raise web.HTTPBadRequest(text="Expected completed/failed and text")
        return web.json_response({"ok": True})
    callback_app.router.add_post("/result/{job}", grok_result)
    callback_runner = web.AppRunner(callback_app, access_log=None)
    await callback_runner.setup()
    await web.TCPSite(callback_runner, "127.0.0.1", PORT + 1).start()
    # Device entrance: the only listener published through Tailscale Funnel (:8443).
    ingress_runner = web.AppRunner(ingress_app(bridge.registry, bridge.remote, lambda: not bridge.usb.connected,
                                               bridge.ingress_attempts), access_log=None)
    await ingress_runner.setup()
    await web.TCPSite(ingress_runner, "127.0.0.1", REMOTE_PORT).start()
    # LAN entrance: same key check and limits, TLS with the device-pinned certificate.
    lan_runner = web.AppRunner(ingress_app(bridge.registry, bridge.remote, lambda: not bridge.usb.connected,
                                           bridge.ingress_attempts, via="lan"), access_log=None)
    await lan_runner.setup()
    await web.TCPSite(lan_runner, "0.0.0.0", LAN_PORT, ssl_context=bridge.lan_ssl).start()
    (RUNTIME / "bridge.pid").write_text(str(os.getpid()))
    print(f"StopWatch bridge ready: http://127.0.0.1:{PORT}", flush=True)
    await bridge.start()
    stopped = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, stopped.set)
    try:
        await stopped.wait()
    finally:
        await bridge.close()
        await runner.cleanup()
        await callback_runner.cleanup()
        await ingress_runner.cleanup()
        await lan_runner.cleanup()
        (RUNTIME / "bridge.pid").unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(main())
