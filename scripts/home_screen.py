"""Redraw the firmware's home screen (FaceController::drawHomeStatusTarget) for the README.

Coordinates, colours and icon shapes follow the firmware; fonts are close stand-ins
(Helvetica for FreeSans / DejaVu). Rendered at 3x and scaled down for smooth edges.
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/images"
S = 3  # supersampling
W = H = 466
HELV = "/System/Library/Fonts/Helvetica.ttc"
CJK = "/System/Library/Fonts/Hiragino Sans GB.ttc"


def font(size, bold=False):
    return ImageFont.truetype(HELV, int(size * S), index=1 if bold else 0)


class Screen:
    def __init__(self, avatar="nuo-courier"):
        face = Image.open(ROOT / f"avatars/{avatar}/base_m0_e0.png").convert("RGB")
        self.img = Image.new("RGB", (W * S, H * S), "black")
        self.img.paste(face.resize((386 * S, 386 * S), Image.LANCZOS), (40 * S, 40 * S))
        self.d = ImageDraw.Draw(self.img)

    def p(self, *v):
        return [x * S for x in v]

    def rrect(self, x, y, w, h, r, fill=None, outline=None, width=1):
        self.d.rounded_rectangle(self.p(x, y, x + w - 1, y + h - 1), r * S, fill=fill, outline=outline, width=width * S)

    def rect(self, x, y, w, h, fill):
        self.d.rectangle(self.p(x, y, x + w - 1, y + h - 1), fill=fill)

    def circle(self, x, y, r, fill=None, outline=None, width=1):
        self.d.ellipse(self.p(x - r, y - r, x + r, y + r), fill=fill, outline=outline, width=width * S)

    def arc(self, x, y, r_out, r_in, a0, a1, fill):
        self.d.arc(self.p(x - r_out, y - r_out, x + r_out, y + r_out), a0, a1, fill=fill, width=(r_out - r_in + 1) * S)

    def text(self, xy, s, f, fill, anchor):
        self.d.text((xy[0] * S, xy[1] * S), s, font=f, fill=fill, anchor=anchor)

    def tri(self, pts, fill):
        self.d.polygon([(x * S, y * S) for x, y in pts], fill=fill)

    def line(self, a, b, fill, width=1):
        self.d.line(self.p(*a, *b), fill=fill, width=width * S)

    # --- firmware pieces -------------------------------------------------------------
    def clock(self, text="09:41"):
        cx = W // 2
        self.rrect(cx - 64, 12, 128, 42, 21, fill="black")
        self.text((cx, 34), text, font(25, bold=True), (242, 244, 243), "mm")

    def link_icon(self, x, y, state):
        if state == 1:
            c = (159, 196, 232)
            self.rect(x - 13, y - 1, 5, 3, c)
            self.rrect(x - 8, y - 5, 10, 11, 2, fill=c)
            self.rect(x + 2, y - 4, 6, 2, c)
            self.rect(x + 2, y + 3, 6, 2, c)
            return
        grey = (120, 128, 132)
        c = (76, 217, 138) if state == 2 else (90, 169, 255) if state == 3 else grey
        by0 = y + 6
        self.circle(x, by0, 2, fill=c)
        self.arc(x, by0, 8, 6, 225, 315, c)
        self.arc(x, by0, 13, 11, 225, 315, c)
        bx, by = x + 9, y + 3
        if state == 3:
            self.circle(bx, by, 5, fill="black")
            self.circle(bx, by, 4, outline=c)
            self.line((bx - 4, by), (bx + 4, by), c)
            self.d.ellipse(self.p(bx - 2, by - 4, bx + 2, by + 4), outline=c, width=S)
        elif state == 5:
            red = (255, 92, 80)
            self.circle(bx, by, 6, fill="black")
            self.line((bx - 4, by - 4), (bx + 4, by + 4), red, 2)
            self.line((bx - 4, by + 4), (bx + 4, by - 4), red, 2)
        elif state == 6:
            orange = (255, 181, 71)
            self.circle(bx, by, 6, fill="black")
            self.rect(bx - 1, by - 5, 3, 7, orange)
            self.rect(bx - 1, by + 3, 3, 2, orange)

    def status_row(self, agent="Codex", link=2, battery=82, charging=False):
        cx = W // 2
        pill_w, pill_h, pill_y = 180, 26, H - 46
        row = pill_y + pill_h // 2
        self.rrect(cx - pill_w // 2, pill_y, pill_w, pill_h, 13, fill="black")
        border = (201, 209, 206)
        percent = f"{battery}%"
        agent_font, pct_font = font(12), font(13)
        agent_w = self.d.textlength(agent, font=agent_font) / S if agent else 0
        pct_w = self.d.textlength(percent, font=pct_font) / S
        link_w = 26 if link else 0
        total = agent_w + (10 if agent_w else 0) + link_w + (8 if link_w else 0) + 28 + 3 + 6 + pct_w
        left = cx - total / 2
        if agent_w:
            color = (214, 218, 216) if agent.lower() == "grok" else (110, 222, 178)
            self.text((left, row), agent, agent_font, color, "lm")
            left += agent_w + 10
        if link:
            self.link_icon(left + 13, row, link)
            left += link_w + 8
        x, y, w, h = left, row - 7, 28, 14
        fill = (255, 92, 80) if battery <= 20 else (82, 220, 128)
        self.rrect(x, y, w, h, 3, outline=border)
        self.rect(x + w, y + 4, 3, h - 8, border)
        self.rect(x + 2, y + 2, round((w - 4) * battery / 100), h - 4, fill)
        if charging:
            bolt = (255, 230, 90)
            self.tri([(x + 15, y + 1), (x + 9, y + 8), (x + 14, y + 8)], bolt)
            self.tri([(x + 13, y + 6), (x + 18, y + 6), (x + 12, y + h - 1)], bolt)
        self.text((x + w + 9, row), percent, pct_font, border, "lm")

    def page_dots(self, index, count):
        """Avatar page dots just above the status row, as FaceController draws them."""
        cx, gap, y = W // 2, 11, H - 46 - 9
        width = (count - 1) * gap
        self.rrect(cx - width // 2 - 7, y - 5, width + 14, 11, 5, fill="black")
        for i in range(count):
            on = i == index
            self.circle(cx - width // 2 + i * gap, y, 3 if on else 2, fill=(236, 238, 237) if on else (92, 98, 100))

    def badge(self, x, y, kind, count=1):
        bg = (22, 25, 28)
        self.circle(x, y, 20, fill=bg, outline=(42, 47, 51))
        if kind == "follow":
            c = (255, 92, 150)
            self.circle(x - 5, y - 3, 6, fill=c)
            self.circle(x + 5, y - 3, 6, fill=c)
            self.tri([(x - 11, y - 1), (x + 11, y - 1), (x, y + 11)], c)
        elif kind == "reply":
            c = (90, 169, 255)
            self.rrect(x - 11, y - 10, 22, 16, 4, fill=c)
            self.rrect(x - 8, y - 7, 16, 10, 2, fill=bg)
            self.tri([(x - 8, y + 5), (x - 2, y + 5), (x - 9, y + 11)], c)
        elif kind == "mention":
            self.text((x, y), "@", font(19, bold=True), (255, 181, 71), "mm")
        elif kind == "repost":
            c = (76, 217, 138)
            self.rect(x - 9, y - 7, 13, 3, c)
            self.rect(x - 9, y - 7, 3, 7, c)
            self.tri([(x + 3, y - 11), (x + 3, y), (x + 9, y - 6)], c)
            self.rect(x - 3, y + 4, 13, 3, c)
            self.rect(x + 7, y, 3, 7, c)
            self.tri([(x - 3, y), (x - 3, y + 11), (x - 9, y + 5)], c)
        else:  # quote
            c = (180, 140, 255)
            for dx in (-6, 6):
                self.circle(x + dx, y + 3, 4, fill=c)
                self.tri([(x + dx - 4, y + 2), (x + dx, y + 2), (x + dx + 2, y - 8)], c)
        if count >= 2:
            text = "9+" if count > 9 else str(count)
            w = 24 if len(text) > 1 else 18
            pill = (242, 244, 243)
            self.rrect(x + 13 - w // 2, y - 23, w, 18, 9, fill=pill)
            self.text((x + 13, y - 14), text, font(11, bold=True), "black", "mm")

    def badges(self, items):
        cx = cy = W // 2
        for i, (kind, count) in enumerate(items):
            a = math.radians(-30 + 15 * i)
            self.badge(int(cx + 192 * math.cos(a)), int(cy + 192 * math.sin(a)), kind, count)

    def done(self, size=W):
        mask = Image.new("L", self.img.size, 0)
        ImageDraw.Draw(mask).ellipse((0, 0, self.img.width - 1, self.img.height - 1), fill=255)
        out = Image.new("RGBA", self.img.size, (0, 0, 0, 0))
        out.paste(self.img, (0, 0), mask)
        return out.resize((size, size), Image.LANCZOS)


def bezel(screen, pad=22):
    size = screen.width + pad * 2
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(out)
    d.ellipse((0, 0, size - 1, size - 1), fill=(52, 57, 66))
    d.ellipse((6, 6, size - 7, size - 7), fill=(16, 17, 21))
    out.paste(screen, (pad, pad), screen)
    return out


def annotated():
    s = Screen()
    s.clock()
    s.badges([("follow", 3), ("mention", 1), ("reply", 2), ("repost", 1), ("quote", 1)])
    s.status_row("Codex", 2, 82)
    s.page_dots(2, 4)
    screen_px = 540
    watch = bezel(s.done(screen_px))
    width, height = 1280, 700
    img = Image.new("RGB", (width, height), (13, 17, 23))
    ox, oy = 70, (height - watch.height) // 2
    img.paste(watch, (ox, oy), watch)
    d = ImageDraw.Draw(img)
    big, small = ImageFont.truetype(CJK, 24), ImageFont.truetype(HELV, 17)
    accent, fg, sub = (110, 168, 255), (236, 240, 246), (140, 150, 168)
    k, z = 22, screen_px / W  # bezel pad, screen scale
    to = lambda x, y: (ox + k + x * z, oy + k + y * z)
    callouts = [
        (to(233 + 64, 33), 110, "时间", "Clock — synced from the Mac"),
        (to(233 + 192 * math.cos(math.radians(-30)) + 20, 233 + 192 * math.sin(math.radians(-30))), 210,
         "X 提醒图标", "Follow · @ · reply · repost · quote, 30 min each"),
        (to(370, 260), 330, "当前形象", "16 mouth × eye frames, swappable"),
        (to(233 + 90, 433), 470, "形象页码 · 状态栏", "Avatar dots (swipe) · assistant · link · battery"),
    ]
    tx = 760
    # Zoomed status row under its callout.
    row = s.img.crop((S * 133, S * 400, S * 333, S * 448))
    row = row.resize((400, 96), Image.LANCZOS)
    inset = Image.new("RGB", (row.width + 4, row.height + 4), (48, 54, 64))
    inset.paste(row, (2, 2))
    img.paste(inset, (tx - 8, 470 + 70))
    for (px, py), ty, title, desc in callouts:
        d.line([(px, py), (tx - 30, ty + 14)], fill=accent, width=2)
        d.ellipse((px - 5, py - 5, px + 5, py + 5), fill=accent)
        d.ellipse((tx - 36, ty + 8, tx - 24, ty + 20), outline=accent, width=2)
        d.text((tx - 8, ty), title, font=big, fill=fg)
        d.text((tx - 8, ty + 34), desc, font=small, fill=sub)
    img.save(OUT / "home-screen.png", optimize=True)


def legend():
    """Every link state and badge kind, cut from the same renderer."""
    tiles = []
    def crop(screen, box):
        x0, y0, x1, y1 = box
        return screen.img.crop((x0 * S, y0 * S, x1 * S, y1 * S)).resize(((x1 - x0) * 2, (y1 - y0) * 2), Image.LANCZOS)
    for state, label in [(1, "USB"), (2, "局域网 LAN"), (3, "公网 Funnel"), (4, "连接中"), (5, "无 Wi-Fi"), (6, "连不上 Mac")]:
        s = Screen()
        s.img.paste((0, 0, 0), (0, 0, s.img.width, s.img.height))
        s.link_icon(30, 26, state)
        tiles.append((crop(s, (5, 2, 55, 54)), label))
    for kind, label in [("follow", "新粉丝"), ("reply", "回复"), ("mention", "@ 提及"), ("repost", "转发"), ("quote", "引用")]:
        s = Screen()
        s.img.paste((0, 0, 0), (0, 0, s.img.width, s.img.height))
        s.badge(30, 30, kind, 2 if kind == "follow" else 1)
        tiles.append((crop(s, (5, 2, 55, 54)), label))
    cell_w, height = 104, 150
    img = Image.new("RGB", (cell_w * len(tiles) + 40, height), (0, 0, 0))
    d = ImageDraw.Draw(img)
    f = ImageFont.truetype(CJK, 15)
    for i, (tile, label) in enumerate(tiles):
        x = 20 + i * cell_w
        img.paste(tile, (x + (cell_w - tile.width) // 2, 70 - tile.height // 2))
        d.text((x + cell_w // 2, 128), label, font=f, fill=(176, 184, 198), anchor="mm")
    d.line([(20 + 6 * cell_w, 20), (20 + 6 * cell_w, height - 20)], fill=(48, 54, 64), width=2)
    img.save(OUT / "status-icons.png", optimize=True)


if __name__ == "__main__":
    annotated()
    legend()
    for name in ("home-screen.png", "status-icons.png"):
        print(name, (OUT / name).stat().st_size // 1024, "KB")
