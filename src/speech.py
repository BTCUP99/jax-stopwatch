import hashlib
import json
import re
import subprocess
import threading
from pathlib import Path
import numpy as np


class Speech:
    def __init__(self, root):
        self.root = Path(root)
        self.model = self.root / "models/whisper-small-mlx"
        self.cache = self.root / "runtime/tts"
        self.cache.mkdir(mode=0o700, exist_ok=True)
        self.lock = threading.Lock()
        self.last_no_speech = None
        self.voice_file = self.root / "runtime/voice.json"
        self.voices = chinese_voices()
        saved = json.loads(self.voice_file.read_text()) if self.voice_file.exists() else {}
        self.voice = next((v for v in self.voices if v.startswith("Tingting")), next(iter(self.voices)))
        self.rate = 195
        try:
            self.set_voice(saved.get("voice", self.voice), saved.get("rate", self.rate))
        except ValueError:
            pass  # An uninstalled saved voice falls back to Tingting.

    def set_voice(self, voice, rate):
        if voice not in self.voices:
            raise ValueError("这台 Mac 没有安装该中文声音")
        if not isinstance(rate, int) or not 140 <= rate <= 260:
            raise ValueError("语速应为 140–260")
        self.voice, self.rate = voice, rate

    def save_voice(self):
        saved = self.voice_file.with_suffix(".tmp")
        saved.write_text(json.dumps({"voice": self.voice, "rate": self.rate}, ensure_ascii=False))
        saved.chmod(0o600)
        saved.replace(self.voice_file)

    def transcribe(self, pcm, name=None):
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        if len(samples) < 6400:
            raise ValueError("录音太短，请按住 A 键说完一句话再松开")
        samples = samples - np.mean(samples)
        if float(np.sqrt(np.mean(samples * samples))) < 0.002:
            raise ValueError("没有听清声音，请靠近设备麦克风再试")
        import mlx_whisper
        hint = prompt(name)
        with self.lock:
            result = mlx_whisper.transcribe(samples, path_or_hf_repo=str(self.model), language="zh",
                temperature=0, condition_on_previous_text=False, initial_prompt=hint, no_speech_threshold=0.6,
                hallucination_silence_threshold=1.0, word_timestamps=True, verbose=None)
        text = result.get("text", "").strip()
        segments = result.get("segments", [])
        self.last_no_speech = round(max((s.get("no_speech_prob", 0) for s in segments), default=0), 3)
        if not text or hallucinated(text, segments, name):
            raise ValueError("未识别到清晰语音，请再试一次")
        return text

    def synthesize(self, text):
        self.cache.mkdir(mode=0o700, exist_ok=True)  # a cleared cache must not break playback
        digest = hashlib.sha256(f"{self.voice}|{self.rate}|{text}".encode()).hexdigest()
        raw = self.cache / (digest + ".pcm")
        if raw.exists():
            return raw.read_bytes()
        aiff = self.cache / (digest + ".aiff")
        subprocess.run(["/usr/bin/say", "-v", self.voice, "-r", str(self.rate), "-o", str(aiff), text], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=90)
        subprocess.run(["/opt/homebrew/bin/ffmpeg", "-y", "-v", "error", "-i", str(aiff),
                        "-ac", "1", "-ar", "16000", "-f", "s16le", str(raw)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30)
        aiff.unlink(missing_ok=True)
        raw.chmod(0o600)
        return raw.read_bytes()


def chinese_voices():
    """Installed Mandarin voices for `say`, by the exact name it accepts."""
    try:
        listing = subprocess.run(["/usr/bin/say", "-v", "?"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        listing = ""
    voices = {}
    for line in listing.splitlines():
        m = re.match(r"^(.+?)\s+(zh_CN|zh_TW)\s+#", line)
        if m and (m.group(2) == "zh_CN" or m.group(1) == "Meijia"):
            name = m.group(1).strip()
            voices[name] = re.sub(r"\s*\(.*\)$", "", name) + ("（台湾腔）" if m.group(2) == "zh_TW" else "")
    return voices or {"Tingting": "Tingting"}


# Whisper's stock outputs for silence/noise, learned from video subtitles.
STOCK_PHRASES = re.compile(r"字幕\s*(?:by|由|製作|制作)|中文字幕|感谢观看|感謝觀看|请不吝|請不吝|点赞\s*订阅|點贊\s*訂閱|amara\.org", re.I)


# Biases spelling of the pet name and assistant names; Whisper may echo it on noise.
PROMPT_TERMS = ("简体中文", "Codex", "Grok Bot", "编程助手")


def prompt(name=None):
    return "。".join(PROMPT_TERMS[:1] + ((name,) if name else ()) + PROMPT_TERMS[1:]) + "。"


def sentences(text):
    return [s.strip() for s in re.split(r"[。！？!?，,\s]+", text.lower()) if s.strip()]


def hallucinated(text, segments, name=None):
    """Reject noise transcriptions: low speech confidence, stock phrases, or an echoed prompt."""
    if segments and max(s.get("no_speech_prob", 0) for s in segments) >= .5:
        return True
    if STOCK_PHRASES.search(text):
        return True
    said = sentences(text)
    terms = set(sentences("。".join(PROMPT_TERMS)))
    others = [s for s in said if s != (name or "").lower()]
    # An echoed prompt repeats its terms or lists several of them with nothing else said;
    # "贾克斯，Codex" stays valid because the name itself is spoken input.
    return bool(others) and set(others) <= terms and (len(said) != len(set(said)) or len(others) >= 2
                                                      or "简体中文" in others)


def spoken_segments(text, maximum=360):
    text = re.sub(r"```[\s\S]*?```", "代码请在 Mac 上查看。", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[*#`]+", "", text)
    if len(text) > maximum:
        text = text[:maximum] + "。完整结果请在 Mac 上查看。"
    # Device subtitle area is three short lines. Split longer sentences accordingly.
    parts = re.split(r"(?<=[。！？；\n])", text)
    out = []
    for part in parts:
        while len(part) > 44:
            out.append(part[:44]); part = part[44:]
        if part.strip():
            out.append(part.strip())
    return out
