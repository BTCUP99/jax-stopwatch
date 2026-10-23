import asyncio
import json
import time
import uuid
import serial
from serial.tools import list_ports
from adpcm import encode as adpcm_encode
from protocol import Decoder, frame, mic_adpcm, mic_pcm


class Link:
    """SCU1 protocol session shared by the USB and network transports."""
    name = "link"

    def __init__(self, on_event, on_audio):
        self.on_event = on_event
        self.on_audio = on_audio
        self.decoder = Decoder()
        self.seq = 0
        self.info = {}
        self.port = None
        self.connected = False
        self.waiters = {}
        self.write_lock = asyncio.Lock()
        self.last_pong = 0
        self.mic_packets = 0
        self.mic_gaps = 0
        self.last_mic_seq = None
        self.compress = False  # ADPCM audio, network link only

    async def transmit(self, data):
        raise NotImplementedError

    async def write(self, kind, payload):
        async with self.write_lock:
            encoded = frame(kind, payload, self.seq)
            self.seq += 1
            await self.transmit(encoded)

    async def send(self, data):
        await self.write(1, json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode())

    async def request(self, kind, timeout=15, **params):
        request_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.waiters[request_id] = future
        try:
            await self.send({"type": kind, "requestId": request_id, **params})
            return await asyncio.wait_for(future, timeout)
        finally:
            self.waiters.pop(request_id, None)

    async def receive(self, data):
        for kind, payload, seq in self.decoder.feed(data):
            if kind in (3, 11):
                try:
                    pcm, micseq, flags = (mic_pcm if kind == 3 else mic_adpcm)(payload)
                except ValueError:
                    continue
                if flags & 1:
                    self.last_mic_seq = None
                if self.last_mic_seq is not None and micseq != ((self.last_mic_seq + 1) & 0xFFFFFFFF):
                    self.mic_gaps += 1
                self.last_mic_seq = micseq
                self.mic_packets += 1
                await self.on_audio(self, pcm)
            elif kind in (1, 7, 9):
                try:
                    event = json.loads(payload)
                except (ValueError, UnicodeError):
                    continue
                if event.get("type") == "pong":
                    self.last_pong = time.monotonic()
                if event.get("type") == "device.info":
                    self.info = event
                waiter = self.waiters.get(event.get("requestId"))
                if waiter and not waiter.done():
                    waiter.set_result(event)
                await self.on_event(self, event)

    async def session(self, reader):
        """Handshake, then keep the link alive until the reader ends or the device stops answering."""
        self.decoder = Decoder()
        self.info = {}
        self.last_pong = 0
        self.last_mic_seq = None
        await self.send({"type": "ping", "id": "bridge-connect"})
        self.info = await self.request("device.info.get")
        # Compress only across the Funnel relay; the LAN has bandwidth for raw PCM.
        self.compress = (self.name == "remote" and getattr(self, "via", None) == "funnel"
                         and "audio.adpcm" in (self.info.get("capabilities") or []))
        self.connected = True
        await self.send({"type": "state", "value": "idle"})
        if self.info.get("macBridgeVersion"):
            await self.send({"type": "bridge.configure", "enabled": True})
        await self.on_event(self, {"type": "bridge.connected", "info": self.info})
        while not reader.done():
            await asyncio.sleep(4)
            await self.send({"type": "ping", "id": "keepalive"})
            if self.last_pong and time.monotonic() - self.last_pong > 15:
                raise ConnectionError("StopWatch 心跳超时")
        await reader

    def fail_waiters(self):
        self.connected = False
        for future in list(self.waiters.values()):
            if not future.done():
                future.set_exception(ConnectionError("设备断开"))

    async def play(self, pcm, text):
        if not pcm or not self.connected:
            return
        sequence = uuid.uuid4().hex
        # Match each short subtitle to its audio; the host already splits long replies.
        text = text.encode()[:500].decode(errors="ignore")
        await self.send({"type": "state", "value": "speaking"})
        await asyncio.sleep(0.12)
        await self.send({"type": "display.speech_bubble.cue", "version": 1,
                         "sequenceId": sequence, "segmentIndex": 0,
                         "text": text, "pcmBytes": len(pcm), "sampleRate": 16000})
        started = time.monotonic()
        # Wi-Fi jitters and the Funnel relay adds latency: run further ahead than USB, and
        # compress 4:1 across the relay (the firmware's 512 KB playback ring absorbs it).
        lead = 0.30 if self.name == "usb" else 2.0 if self.compress else 1.0
        for offset in range(0, len(pcm), 4096):
            chunk = pcm[offset:offset + 4096]
            await self.write(10, adpcm_encode(chunk)) if self.compress else await self.write(2, chunk)
            # Keep only a bounded lead over playback to avoid firmware queue overflow.
            wait = (offset + 4096) / 32000 - (time.monotonic() - started) - lead
            if wait > 0:
                await asyncio.sleep(wait)
        await self.send({"type": "display.speech_bubble.end", "version": 1,
                         "sequenceId": sequence, "holdMs": 5000})
        await self.send({"type": "state", "value": "idle"})
        await asyncio.sleep(max(0.3, len(pcm) / 32000 - (time.monotonic() - started) + 0.3))

    async def idle(self):
        if self.connected:
            await self.send({"type": "state", "value": "idle"})


class Device(Link):
    """USB CDC transport."""
    name = "usb"

    def __init__(self, on_event, on_audio):
        super().__init__(on_event, on_audio)
        self.serial = None

    async def transmit(self, data):
        if not self.serial or not self.serial.is_open:
            raise ConnectionError("StopWatch 未连接")
        await asyncio.to_thread(self.serial.write, data)

    def pick_port(self):
        matches = [p.device for p in list_ports.comports() if p.vid == 0x303A and p.pid == 0x1001]
        if len(matches) != 1:
            raise RuntimeError(f"需要连接一台 StopWatch，当前匹配 {len(matches)} 台")
        return matches[0]

    async def reader(self):
        while self.serial and self.serial.is_open:
            await self.receive(await asyncio.to_thread(self.serial.read, max(1, min(self.serial.in_waiting, 16384))))

    async def run(self):
        while True:
            read_task = None
            try:
                self.port = self.pick_port()
                ser = serial.Serial(port=None, baudrate=921600, timeout=0.1, write_timeout=3)
                ser.dtr = True
                ser.rts = False
                ser.port = self.port
                ser.open()
                self.serial = ser
                await asyncio.sleep(1)
                ser.reset_input_buffer()
                read_task = asyncio.create_task(self.reader())
                await self.session(read_task)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                await self.on_event(self, {"type": "bridge.disconnected", "message": str(error)})
            finally:
                self.fail_waiters()
                if read_task:
                    read_task.cancel()
                    await asyncio.gather(read_task, return_exceptions=True)
                if self.serial:
                    self.serial.close()
                    self.serial = None
            await asyncio.sleep(2)
