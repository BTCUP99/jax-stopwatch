"""Render the README banner and the talking-avatar GIFs from avatars/ (macOS fonts)."""
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/images"
AVATARS = ["nuo-courier", "orbit-warden", "little-host"]
LATIN = "/System/Library/Fonts/SFNSRounded.ttf"
CJK = "/System/Library/Fonts/Hiragino Sans GB.ttc"


def frame(avatar, mouth, eye):
    return Image.open(ROOT / f"avatars/{avatar}/base_m{mouth}_e{eye}.png").convert("RGB")


def watch(face, size):
    """A face on a round AMOLED screen inside a thin bezel."""
    face = face.resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    pad = size // 14
    out = Image.new("RGBA", (size + pad * 2, size + pad * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(out)
    draw.ellipse((0, 0, size + pad * 2 - 1, size + pad * 2 - 1), fill=(46, 50, 58, 255))
    draw.ellipse((pad // 3, pad // 3, size + pad * 2 - pad // 3, size + pad * 2 - pad // 3), fill=(14, 15, 18, 255))
    screen = Image.new("RGBA", (size, size), (0, 0, 0, 255))
    screen.paste(face, (0, 0), mask)
    out.paste(screen, (pad, pad), mask)
    return out


def banner():
    width, height = 1600, 620
    image = Image.new("RGB", (width, height), (9, 11, 16))
    glow = Image.new("RGB", (width, height), (0, 0, 0))
    draw = ImageDraw.Draw(glow)
    for x, color in ((980, (60, 80, 200)), (1330, (200, 130, 60))):
        draw.ellipse((x - 260, 60, x + 260, 580), fill=color)
    image = Image.blend(image, glow.filter(ImageFilter.GaussianBlur(140)), .35)
    draw = ImageDraw.Draw(image)
    draw.text((90, 165), "Jax StopWatch", font=ImageFont.truetype(LATIN, 92), fill=(244, 246, 250))
    draw.text((94, 290), "手腕上的 AI 助手 · 按住说话，Mac 帮你办", font=ImageFont.truetype(CJK, 38), fill=(196, 204, 218))
    draw.text((94, 350), "Push-to-talk to Codex & webhook agents from an M5Stack StopWatch",
              font=ImageFont.truetype(LATIN, 27), fill=(130, 140, 158))
    for i, label in enumerate(["Local Whisper", "Wi-Fi anywhere", "X alerts", "Custom avatars"]):
        font = ImageFont.truetype(LATIN, 22)
        x = 94 + sum(draw.textlength(t, font=font) + 50 for t in ["Local Whisper", "Wi-Fi anywhere", "X alerts"][:i])
        draw.rounded_rectangle((x, 430, x + draw.textlength(label, font=font) + 30, 472), 21, outline=(70, 78, 94), width=2)
        draw.text((x + 15, 439), label, font=font, fill=(176, 184, 198))
    faces = [("nuo-courier", 0, 0, 300, 940, 150), ("orbit-warden", 0, 0, 230, 1290, 60), ("little-host", 0, 0, 230, 1300, 330)]
    for avatar, mouth, eye, size, x, y in faces:
        piece = watch(frame(avatar, mouth, eye), size)
        image.paste(piece, (x, y), piece)
    OUT.mkdir(parents=True, exist_ok=True)
    image.save(OUT / "banner.png", optimize=True)


def talking(avatar, size=240):
    """Lip sync plus a blink, the way the firmware drives the 16 frames, then the happy face."""
    rng = random.Random(7)
    mouths = [0, 1, 2, 3, 2, 1, 2, 3, 3, 2, 1, 0, 1, 2, 1, 0, 0, 1, 3, 2, 1, 0, 0, 0]
    eyes = [0] * len(mouths)
    for i, e in zip(range(14, 21), [1, 2, 3, 3, 2, 1, 0]):
        eyes[i] = e
    frames = [watch(frame(avatar, m, e), size) for m, e in zip(mouths, eyes)]
    durations = [90 + rng.randint(0, 40) for _ in frames]
    happy = ROOT / f"avatars/{avatar}/happy.png"
    if happy.exists():
        frames.append(watch(Image.open(happy).convert("RGB"), size))
        durations.append(1400)
    frames.append(watch(frame(avatar, 0, 0), size))
    durations.append(900)
    flat = []
    for piece in frames:
        canvas = Image.new("RGB", piece.size, (13, 17, 23))  # GitHub dark page colour
        canvas.paste(piece, (0, 0), piece)
        flat.append(canvas.convert("P", palette=Image.ADAPTIVE, colors=128))
    flat[0].save(OUT / f"{avatar}.gif", save_all=True, append_images=flat[1:], duration=durations, loop=0,
                 optimize=True, disposal=1)


if __name__ == "__main__":
    banner()
    for name in AVATARS:
        talking(name)
    for path in sorted(OUT.iterdir()):
        print(f"{path.relative_to(ROOT)}  {path.stat().st_size // 1024} KB")
