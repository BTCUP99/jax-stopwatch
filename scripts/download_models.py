"""Fetch the local speech models into models/ (under JAX_HOME if set), pinned and checksum-verified."""
import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path

from huggingface_hub import hf_hub_download

ROOT = Path(os.environ.get("JAX_HOME") or Path(__file__).resolve().parents[1]).expanduser()
WHISPER = {"repo": "mlx-community/whisper-small-mlx", "revision": "45f3915923c7a79a5a5b5a7d909d39aeb0e5630e",
           "files": {"weights.npz": "55b6674c9b339702d486e2b1573839a66f8ec8f821ed2886993ef717a86b09f5",
                     "config.json": "e8f58e638208af66d5d5d67801259dc7a12d199e971967a9f9d33a8e3635668e"}}
SILERO_COMMIT = "066409f3d9db45a30d21dfcbf9147826ffdd7052"
SILERO = {"silero_vad.jit": ("src/silero_vad/data/silero_vad.jit",
                             "e1122837f4154c511485fe0b9c64455f7b929c96fbb8d79fbdb336383ebd3720"),
          "LICENSE": ("LICENSE", "2e63e9a38b6e8fc0c7bc37ce174caca1862870856c6daf5697cfb785e925520b")}


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def ensure(path, expected, fetch):
    if path.exists() and sha256(path) == expected:
        print(f"ok        {path.relative_to(ROOT)}")
        return
    print(f"download  {path.relative_to(ROOT)}", flush=True)
    fetch(path)
    if sha256(path) != expected:
        path.unlink()
        sys.exit(f"checksum mismatch: {path}")


def main():
    whisper = ROOT / "models/whisper-small-mlx"
    whisper.mkdir(parents=True, exist_ok=True)
    for name, digest in WHISPER["files"].items():
        def fetch(path, name=name):
            cached = hf_hub_download(WHISPER["repo"], name, revision=WHISPER["revision"])
            path.write_bytes(Path(cached).read_bytes())
        ensure(whisper / name, digest, fetch)

    silero = ROOT / "models/silero-vad"
    silero.mkdir(parents=True, exist_ok=True)
    for name, (source, digest) in SILERO.items():
        url = f"https://raw.githubusercontent.com/snakers4/silero-vad/{SILERO_COMMIT}/{source}"
        ensure(silero / name, digest, lambda path, url=url: urllib.request.urlretrieve(url, path))
    (silero / "manifest.json").write_text(json.dumps({"repository": "https://github.com/snakers4/silero-vad",
                                                      "commit": SILERO_COMMIT}, indent=2) + "\n")


if __name__ == "__main__":
    main()
