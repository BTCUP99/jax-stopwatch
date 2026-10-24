import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp import WSMsgType
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from protocol import Decoder, json_frame
from remote import Registry, RemoteDevice, ingress_app
import bridge as module


async def fake_device(ws):
    """Answer the bridge handshake the way the firmware does over USB."""
    decoder = Decoder()
    async for message in ws:
        if message.type != WSMsgType.BINARY:
            break
        for kind, payload, _ in decoder.feed(message.data):
            command = json.loads(payload)
            if command["type"] == "ping":
                await ws.send_bytes(json_frame({"type": "pong"}))
            elif command["type"] == "device.info.get":
                await ws.send_bytes(json_frame({"type": "device.info", "requestId": command["requestId"],
                                                "macBridgeVersion": "0.2.0"}))


class IngressTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.registry = Registry(Path(self.tmp.name))
        self.device_id, self.token = self.registry.issue("测试")
        self.events = []

        async def on_event(link, event):
            self.events.append(event["type"])

        async def on_audio(link, pcm):
            pass

        self.link = RemoteDevice(on_event, on_audio)
        self.usb_up = False
        self.client = TestClient(TestServer(ingress_app(self.registry, self.link, lambda: not self.usb_up)))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    def headers(self, token=None, device=None):
        return {"Authorization": "Bearer " + (token or self.token), "X-StopWatch-Device": device or self.device_id}

    async def test_valid_token_completes_handshake(self):
        ws = await self.client.ws_connect("/device", headers=self.headers())
        answering = asyncio.create_task(fake_device(ws))
        for _ in range(50):
            if self.link.connected:
                break
            await asyncio.sleep(.05)
        self.assertTrue(self.link.connected)
        self.assertEqual(self.link.info["macBridgeVersion"], "0.2.0")
        self.assertIn("bridge.connected", self.events)
        await self.link.drop()
        answering.cancel()
        self.assertFalse(self.link.connected)

    async def test_wrong_token_unknown_device_and_revoked_are_rejected(self):
        for headers in (self.headers(token="x" * 43), self.headers(device="dev_000000000000")):
            response = await self.client.get("/device", headers=headers)
            self.assertEqual(response.status, 401)
        self.registry.revoke(self.device_id)
        self.assertEqual((await self.client.get("/device", headers=self.headers())).status, 401)

    async def test_repeated_failures_block_the_client(self):
        for _ in range(5):
            await self.client.get("/device", headers=self.headers(token="bad"))
        self.assertEqual((await self.client.get("/device", headers=self.headers())).status, 429)

    async def test_usb_link_refuses_network_session_and_other_paths_are_absent(self):
        self.usb_up = True
        self.assertEqual((await self.client.get("/device", headers=self.headers())).status, 409)
        self.assertEqual((await self.client.get("/api/state")).status, 404)

    async def test_forwarded_header_counts_only_from_the_funnel_proxy(self):
        from remote import client_of
        request = type("R", (), {"remote": "192.168.1.9", "headers": {"X-Forwarded-For": "1.2.3.4"}})()
        self.assertEqual(client_of(request), "192.168.1.9")
        request.remote = "127.0.0.1"
        self.assertEqual(client_of(request), "1.2.3.4")

    def test_registry_keeps_only_hash(self):
        stored = json.loads((Path(self.tmp.name) / "devices.json").read_text())
        self.assertNotIn(self.token, json.dumps(stored))


class PairingAndBadgeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "runtime").mkdir()
        for name, value in (("ROOT", root), ("RUNTIME", root / "runtime")):
            patcher = patch.object(module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.bridge = module.Bridge()
        usb = self.bridge.usb
        usb.connected = True
        usb.info = {"deviceId": "stackchan_hw1", "capabilities": ["net.config", "notify.badges"]}
        usb.send = AsyncMock()
        self.bridge.funnel = {"ok": True, "url": "wss://mac.example.ts.net:8443/device"}

    async def test_pairing_writes_key_over_usb_and_retires_old_keys(self):
        old_id, _ = self.bridge.registry.issue("旧", "stackchan_hw1")
        self.bridge.usb.request = AsyncMock(return_value={"ok": True, "netLink": {"configured": True}})
        await self.bridge.pair_network({"wifi": [{"ssid": "Home", "password": "12345678"}, {"ssid": " "}]})
        kwargs = self.bridge.usb.request.await_args.kwargs
        self.assertEqual(kwargs["wifi"], [{"ssid": "Home", "password": "12345678"}])
        self.assertTrue(self.bridge.registry.verify(kwargs["deviceId"], kwargs["token"]))
        self.assertTrue(self.bridge.registry.devices[old_id]["revoked"])
        self.assertNotIn("12345678", (Path(self.tmp.name) / "runtime/devices.json").read_text())

    async def test_rejected_pairing_leaves_no_key(self):
        self.bridge.usb.request = AsyncMock(return_value={"ok": False, "error": "endpoint"})
        with self.assertRaises(RuntimeError):
            await self.bridge.pair_network({"wifi": [{"ssid": "Home", "password": "12345678"}]})
        self.assertEqual(self.bridge.registry.devices, {})

    async def test_badges_carry_remaining_lifetime_and_expire(self):
        now = module.time.time()
        self.bridge.badges.extend([("follow", now - module.BADGE_SECONDS - 1), ("reply", now - 600)])
        await self.bridge.queue_notice("mention", "x")
        await self.bridge.queue_notice("test", "x")  # not a badge kind
        items = self.bridge.usb.send.await_args.args[0]["items"]
        self.assertEqual([i["kind"] for i in items], ["reply", "mention"])
        self.assertTrue(1190 <= items[0]["ttlSec"] <= 1200 and items[1]["ttlSec"] >= 1799)

    async def test_shake_replays_newest_notice_only_while_its_badge_shows(self):
        self.bridge.state["phase"] = "ready"
        self.bridge.announce = AsyncMock()
        shake = {"type": "interaction.event", "event": "shake", "phase": "start"}
        await self.bridge.replay_notice()  # nothing on screen yet
        self.bridge.announce.assert_not_awaited()
        await self.bridge.queue_notice("reply", "旧回复")
        await self.bridge.queue_notice("mention", "新提及")
        await self.bridge.on_device(shake)
        await asyncio.sleep(0)
        await self.bridge.job
        self.bridge.announce.assert_awaited_once_with("新提及", "重播 X 提醒")
        await self.bridge.replay_notice()  # cooldown
        self.assertEqual(self.bridge.announce.await_count, 1)
        self.bridge.replay_after = 0.
        self.bridge.last_notice = (module.time.time() - module.BADGE_SECONDS, "已清除")
        await self.bridge.replay_notice()  # badge expired
        self.bridge.last_notice = (module.time.time(), "新提及")
        self.bridge.set_shake(False)
        await self.bridge.replay_notice()  # turned off in the console
        self.assertEqual(self.bridge.announce.await_count, 1)


if __name__ == "__main__":
    unittest.main()
