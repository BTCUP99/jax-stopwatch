import base64
import io
import json
import re
import zlib
from pathlib import Path

from PIL import Image

SIZE = 386
MAX_JPEG = 40_000
CHUNK = 4096  # raw bytes per avatar.put; base64 keeps the frame under the 8 KB SCU1 limit
ID = re.compile(r"[a-z0-9-]{1,20}")
BASE = [f"base_m{m}_e{e}" for m in range(4) for e in range(4)]
TOUCH = ["giggle_1", "giggle_2", "laugh_1", "laugh_2"]  # tap / triple-tap reactions, optional


def jpeg(path):
    """PNG -> baseline JPEG (quality 85, 4:2:0) as the firmware decoder expects."""
    with Image.open(path) as image:
        if image.size != (SIZE, SIZE):
            raise ValueError(f"{path.name} 尺寸是 {image.size[0]}×{image.size[1]}，应为 {SIZE}×{SIZE}")
        out = io.BytesIO()
        image.convert("RGB").save(out, "JPEG", quality=85, subsampling=2, optimize=True)
    data = out.getvalue()
    if len(data) > MAX_JPEG:
        raise ValueError(f"{path.name} 转换后 {len(data) // 1000}KB，超过 40KB")
    return data


class Library:
    """Avatar sets under avatars/<id>/ (see docs/avatars.md)."""

    def __init__(self, root):
        self.root = Path(root)

    def folder(self, avatar_id):
        if not ID.fullmatch(avatar_id or ""):
            raise ValueError("形象 ID 只能是 1–20 位小写字母、数字或连字符")
        folder = self.root / avatar_id
        if not (folder / "avatar.json").is_file():
            raise ValueError(f"没有找到形象 {avatar_id}")
        return folder

    def listing(self):
        sets = []
        for meta_file in sorted(self.root.glob("*/avatar.json")):
            folder = meta_file.parent
            if not ID.fullmatch(folder.name):
                continue
            try:
                meta = json.loads(meta_file.read_text())
            except ValueError:
                continue
            missing = [name for name in BASE if not (folder / f"{name}.png").is_file()]
            sets.append({"id": folder.name, "name": str(meta.get("name") or folder.name),
                         "description": str(meta.get("description") or ""), "version": meta.get("version"),
                         "happy": (folder / "happy.png").is_file(),
                         "touch": all((folder / f"{name}.png").is_file() for name in TOUCH), "complete": not missing})
        return sets

    def files(self, avatar_id):
        """Every device file of a set, converted: [(name, bytes)]."""
        folder = self.folder(avatar_id)
        meta = json.loads((folder / "avatar.json").read_text())
        names = BASE + [name for name in ["happy", *TOUCH] if (folder / f"{name}.png").is_file()]
        files = [(f"{name}.jpg", jpeg(folder / f"{name}.png")) for name in names]
        info = {"id": avatar_id, "name": str(meta.get("name") or avatar_id), "version": meta.get("version")}
        files.append(("meta.json", json.dumps(info, ensure_ascii=False).encode()))
        return files


def chunks(avatar_id, name, data):
    """avatar.put requests for one file; the last carries the CRC the device verifies."""
    crc = zlib.crc32(data)
    for offset in range(0, len(data), CHUNK):
        part = data[offset:offset + CHUNK]
        yield {"id": avatar_id, "file": name, "offset": offset, "size": len(data),
               "data": base64.b64encode(part).decode(), "final": offset + CHUNK >= len(data), "crc": crc}
