"""IMA ADPCM (4:1) for audio over the network link; mirrors the firmware's Adpcm.h.

Block: int16 predictor, uint8 step index, uint8 reserved, then one nibble per sample,
low nibble first. Each block decodes on its own, so a lost frame cannot corrupt the next.
"""
import struct

STEPS = (7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66, 73, 80,
         88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544, 598,
         658, 724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327,
         3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899, 15289,
         16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767)
INDEX = (-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8)


def _step(code, predictor, index):
    step = STEPS[index]
    diff = step >> 3
    if code & 4:
        diff += step
    if code & 2:
        diff += step >> 1
    if code & 1:
        diff += step >> 2
    predictor = max(-32768, min(32767, predictor - diff if code & 8 else predictor + diff))
    return predictor, max(0, min(88, index + INDEX[code]))


def encode(pcm):
    samples = struct.unpack(f"<{len(pcm) // 2}h", pcm[:len(pcm) // 2 * 2])
    predictor, index = (samples[0] if samples else 0), 0
    out = bytearray(struct.pack("<hBB", predictor, 0, 0))
    out.extend(bytes((len(samples) + 1) // 2))
    for i, sample in enumerate(samples):
        step = STEPS[index]
        diff = sample - predictor
        code = 0
        if diff < 0:
            code, diff = 8, -diff
        if diff >= step:
            code |= 4
            diff -= step
        if diff >= step >> 1:
            code |= 2
            diff -= step >> 1
        if diff >= step >> 2:
            code |= 1
        predictor, index = _step(code, predictor, index)
        out[4 + i // 2] |= code << 4 if i & 1 else code
    return bytes(out)


def decode(block):
    if len(block) < 4:
        return b""
    predictor, index, _ = struct.unpack_from("<hBB", block)
    index = min(index, 88)
    out = []
    for byte in block[4:]:
        for code in (byte & 0x0F, byte >> 4):
            predictor, index = _step(code, predictor, index)
            out.append(predictor)
    return struct.pack(f"<{len(out)}h", *out)
