import base64
import json
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from avatars import BASE, Library, chunks


def make_set(root, avatar_id, size=386, happy=True):
    folder = root / avatar_id
    folder.mkdir()
    (folder / "avatar.json").write_text(json.dumps({"id": avatar_id, "name": "测试", "version": 1}))
    for name in BASE + (["happy"] if happy else []):
        Image.new("RGB", (size, size), (40, 80, 120)).save(folder / f"{name}.png")
    return folder


class AvatarTests(unittest.TestCase):
    def test_chunks_reassemble_and_carry_crc(self):
        data = bytes(range(256)) * 40  # 10240 bytes -> 3 chunks
        parts = list(chunks("demo", "base_m0_e0.jpg", data))
        self.assertEqual([p["offset"] for p in parts], [0, 4096, 8192])
        self.assertEqual([p["final"] for p in parts], [False, False, True])
        self.assertEqual(b"".join(base64.b64decode(p["data"]) for p in parts), data)
        self.assertTrue(all(p["crc"] == zlib.crc32(data) and p["size"] == len(data) for p in parts))

    def test_library_converts_complete_sets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_set(root, "good")
            make_set(root, "partial", happy=False)
            (root / "partial/base_m3_e3.png").unlink()
            library = Library(root)
            listing = {x["id"]: x for x in library.listing()}
            self.assertTrue(listing["good"]["complete"] and listing["good"]["happy"])
            self.assertFalse(listing["partial"]["complete"])
            names = [name for name, _ in library.files("good")]
            self.assertEqual(names, [f"{n}.jpg" for n in BASE] + ["happy.jpg", "meta.json"])
            jpeg = dict(library.files("good"))["base_m0_e0.jpg"]
            self.assertEqual(jpeg[:2], b"\xff\xd8")
            self.assertIn(b"\xff\xc0", jpeg)  # baseline, which the firmware decoder needs

    def test_rejects_bad_ids_and_sizes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_set(root, "small", size=200)
            library = Library(root)
            for bad in ("../x", "Upper", "a" * 21, ""):
                with self.assertRaises(ValueError):
                    library.folder(bad)
            with self.assertRaisesRegex(ValueError, "386"):
                library.files("small")


if __name__ == "__main__":
    unittest.main()
