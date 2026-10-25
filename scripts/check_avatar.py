"""Check avatar sets against the round-screen outline rules (docs/avatars.md, sections 1–2).

    .venv/bin/python scripts/check_avatar.py avatars/nuo-courier [more dirs...] [--preview out.png]

Looks at every delivered frame for: 386x386 opaque RGB, nothing lit outside the
canvas circle, and straight cut lines in the outline (a crop by the canvas, a
rectangle or the source photo's frame), which show on the round screen as a hard
edge. Edges that reach the circle itself (bleed) are fine. --preview draws each
set's m0_e0 as the watch shows it, for a look by eye. Exit code 1 on any failure.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

SIZE = 386
LIT = 24          # channel value above which a pixel counts as drawn (JPEG noise stays below)
WARN_RUN = 40     # straight edge this long: check by eye that it's a designed flat (feet, base)
FAIL_RUN = 80     # straight edge this long, on the sides or bottom, without a drawn contour: a crop line
BLEED_R = 186     # edges this far from the centre end on the circle, not on a straight line
FRAMES = [f"base_m{m}_e{e}.png" for m in range(4) for e in range(4)]
OPTIONAL = ["happy.png", "giggle_1.png", "giggle_2.png", "laugh_1.png", "laugh_2.png"]

yy, xx = np.mgrid[0:SIZE, 0:SIZE]
RADIUS = np.hypot(xx - (SIZE - 1) / 2, yy - (SIZE - 1) / 2)


def straight_edges(gray):
    """Per side, the longest run of neighbouring lines whose outermost drawn pixel sits on one line.

    Returns {side: (length, position, outlined)}; outlined means the edge is darker than
    just inside it, i.e. a drawn contour (a flat foot or base) rather than a cut through the fill.
    """
    found = {}
    for side, g in {"下": gray, "上": gray[::-1], "右": gray.T, "左": gray.T[::-1]}.items():
        a = g > LIT
        edge = np.where(a.any(0), SIZE - 1 - np.argmax(a[::-1], 0), -1)  # outermost drawn index per line
        best, start = (0, 0), 0
        for i in range(1, SIZE + 1):
            if i == SIZE or abs(edge[i] - edge[start]) > 1 or edge[start] < 0:  # ±1px: antialiased crops
                if edge[start] >= 0 and i - start > best[0]:
                    best = (i - start, start)
                start = i
        run, first = best
        if not run:
            continue
        lines = np.arange(first, first + run)
        pos = int(edge[first + run // 2])
        if np.hypot(pos - (SIZE - 1) / 2, first + run // 2 - (SIZE - 1) / 2) >= BLEED_R:
            continue  # the edge reaches the circle: bleed, not a crop
        inner = g[np.maximum(edge[lines] - 6, 0), lines].mean()
        outlined = g[edge[lines], lines].mean() < 0.5 * inner
        found[side] = (run, pos if side in "下右" else SIZE - 1 - pos, outlined)
    return found


def check(folder):
    problems, warnings = [], []
    names = [n for n in FRAMES + OPTIONAL if (folder / n).exists()]
    missing = [n for n in FRAMES if n not in names]
    if missing:
        problems.append("缺少基础帧：" + "、".join(missing))
    for name in names:
        image = Image.open(folder / name)
        if image.size != (SIZE, SIZE) or image.mode != "RGB":
            problems.append(f"{name}：应为 {SIZE}×{SIZE} 不透明 RGB，实际 {image.size[0]}×{image.size[1]} {image.mode}")
            continue
        gray = np.asarray(image).max(2)
        lit = gray > LIT
        outside = int((lit & (RADIUS > SIZE / 2)).sum())
        if outside:
            problems.append(f"{name}：画布圆外有 {outside} 个非黑像素")
        if name != "base_m0_e0.png":
            continue  # other frames share the outline (pixel-identical outside eyes and mouth)
        for side, (run, pos, outlined) in straight_edges(gray).items():
            where = f"{side}缘在{'y' if side in '上下' else 'x'}={pos} 处有 {run}px 平直边"
            if run >= FAIL_RUN and side != "上" and not outlined:
                problems.append(f"{name}：{where}，改为完整轮廓或延伸到圆周出血（第 2.1 节）")
            elif run >= WARN_RUN:
                warnings.append(f"{name}：{where}，在圆屏预览上确认是造型本身的平边（脚、底座、头顶）而非裁切")
    return problems, warnings


def screen(folder):
    """The m0_e0 frame as the round watch face shows it."""
    face = Image.new("RGB", (466, 466), "black")
    face.paste(Image.open(folder / "base_m0_e0.png").convert("RGB"), (40, 40))
    out = Image.new("RGB", (466, 466), (70, 70, 70))
    mask = Image.new("L", (466, 466), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, 465, 465), fill=255)
    out.paste(face, (0, 0), mask)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folders", nargs="+", type=Path)
    parser.add_argument("--preview", type=Path, help="write a round-screen contact sheet here")
    args = parser.parse_args()
    failed = False
    args.folders = [f for f in args.folders if any((f / n).exists() for n in FRAMES)] or args.folders
    for folder in args.folders:
        problems, warnings = check(folder)
        print(f"{'✗' if problems else '!' if warnings else '✓'} {folder.name}")
        for line in problems:
            print("  ✗ " + line)
        for line in warnings:
            print("  ! " + line)
        failed |= bool(problems)
    if args.preview:
        shown = [f for f in args.folders if (f / "base_m0_e0.png").exists()]
        cell, cols = 240, min(5, len(shown)) or 1
        sheet = Image.new("RGB", (cols * cell, -(-len(shown) // cols) * (cell + 20)), (70, 70, 70))
        draw = ImageDraw.Draw(sheet)
        for i, folder in enumerate(shown):
            x, y = i % cols * cell, i // cols * (cell + 20)
            sheet.paste(screen(folder).resize((cell - 8, cell - 8), Image.LANCZOS), (x + 4, y + 4))
            draw.text((x + 6, y + cell - 2), folder.name, fill="white")
        sheet.save(args.preview)
        print(f"圆屏预览：{args.preview}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
