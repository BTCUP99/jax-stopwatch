import math
import struct
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from adpcm import decode, encode
from protocol import Decoder, frame, mic_adpcm, mic_pcm


class StreamTests(unittest.TestCase):
    def test_noise_fragmentation_and_back_to_back_frames(self):
        source = b"Boot log\r\n" + frame(1, b'{"type":"pong"}', 9) + frame(3, b"data", 10)
        parser = Decoder()
        result = []
        for i in range(0, len(source), 3):
            result += parser.feed(source[i:i+3])
        self.assertEqual(result, [(1, b'{"type":"pong"}', 9), (3, b"data", 10)])

    def test_crc_corruption_recovers(self):
        bad = bytearray(frame(3, b"bad")); bad[-1] ^= 1
        parser = Decoder()
        result = parser.feed(bad + frame(1, b"good", 1))
        self.assertEqual(result, [(1, b"good", 1)])
        self.assertEqual(parser.bad_crc, 1)

    def test_rejects_unbounded_length(self):
        bad = b"SCU1" + struct.pack("<BBBBII", 1, 1, 0, 0, 0, 0xFFFFFFFF)
        parser = Decoder()
        self.assertEqual(parser.feed(bad + frame(1, b"ok")), [(1, b"ok", 0)])

    def test_rejects_truncated_mic(self):
        data = b"MIC1" + struct.pack("<IIHH", 1, 20, 2, 1) + b"\x00\x00"
        with self.assertRaises(ValueError):
            mic_pcm(data)


if __name__ == "__main__":
    unittest.main()


class AdpcmTests(unittest.TestCase):
    def test_round_trip_tracks_speech_like_signal(self):
        samples = [int(8000 * math.sin(i / 7) + 3000 * math.sin(i / 2.3)) for i in range(2048)]
        pcm = struct.pack("<2048h", *samples)
        block = encode(pcm)
        self.assertEqual(len(block), 4 + 1024)
        out = struct.unpack("<2048h", decode(block))
        error = sum(abs(a - b) for a, b in zip(samples, out)) / len(samples)
        self.assertLess(error, 400)

    def test_mic_adpcm_packet_keeps_header_fields(self):
        pcm = struct.pack("<640h", *range(640))
        packet = b"MIC1" + struct.pack("<IIHH", 7, 0, 640, 1) + encode(pcm)
        out, seq, flags = mic_adpcm(packet)
        self.assertEqual((len(out), seq, flags), (1280, 7, 1))
