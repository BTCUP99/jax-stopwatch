"""SCU1 stream framing, including recovery from fragmented/noisy USB input."""
import json
import struct
import zlib

MAGIC = b"SCU1"
MAX_PAYLOAD = 8192


def frame(kind, payload, sequence=0):
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("SCU1 payload exceeds 8192 bytes")
    body = struct.pack("<BBBBII", 1, kind, 0, 0, sequence & 0xFFFFFFFF, len(payload)) + payload
    return MAGIC + body + struct.pack("<I", zlib.crc32(body))


def json_frame(value, sequence=0):
    return frame(1, json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(), sequence)


class Decoder:
    def __init__(self):
        self.buffer = bytearray()
        self.bad_crc = 0
        self.discarded = 0

    def feed(self, data):
        self.buffer.extend(data)
        out = []
        while True:
            pos = self.buffer.find(MAGIC)
            if pos < 0:
                # Retain a possible partial magic at the boundary.
                drop = max(0, len(self.buffer) - 3)
                self.discarded += drop
                del self.buffer[:drop]
                break
            if pos:
                self.discarded += pos
                del self.buffer[:pos]
            if len(self.buffer) < 16:
                break
            version, kind, flags, reserved, seq, length = struct.unpack_from("<BBBBII", self.buffer, 4)
            if version != 1 or reserved != 0 or length > MAX_PAYLOAD:
                del self.buffer[0]
                continue
            total = 20 + length
            if len(self.buffer) < total:
                break
            crc = struct.unpack_from("<I", self.buffer, 16 + length)[0]
            if zlib.crc32(self.buffer[4:16 + length]) != crc:
                self.bad_crc += 1
                del self.buffer[0]
                continue
            out.append((kind, bytes(self.buffer[16:16 + length]), seq))
            del self.buffer[:total]
        return out


def mic_adpcm(packet):
    """MIC1 header followed by one ADPCM block (network link)."""
    from adpcm import decode
    if len(packet) < 20 or packet[:4] != b"MIC1":
        raise ValueError("Invalid MIC1 header")
    seq, timestamp, samples, flags = struct.unpack_from("<IIHH", packet, 4)
    pcm = decode(packet[16:])
    if len(pcm) < samples * 2:
        raise ValueError("Short MIC1 ADPCM block")
    return pcm[:samples * 2], seq, flags


def mic_pcm(packet):
    if len(packet) < 16 or packet[:4] != b"MIC1":
        raise ValueError("Invalid MIC1 header")
    seq, timestamp, samples, flags = struct.unpack_from("<IIHH", packet, 4)
    if len(packet) != 16 + samples * 2:
        raise ValueError("Invalid MIC1 sample count")
    return packet[16:], seq, flags
