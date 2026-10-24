"""Install the pinned, attributed Base pack: python3 tools/install_base_audio.py [archive.zip]."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import urllib.request
import wave
import zipfile

ASSETS = Path(__file__).resolve().parents[1] / "assets"
MANIFEST = ASSETS / "base-audio.json"


def install(archive=None):
    spec = json.loads(MANIFEST.read_text())
    source = Path(archive).open("rb") if archive else urllib.request.urlopen(spec["url"], timeout=30)
    with source:
        data = source.read(spec["bytes"] + 1)
    if len(data) != spec["bytes"] or hashlib.sha256(data).hexdigest() != spec["sha256"]:
        raise ValueError("Base archive size/SHA-256 mismatch; existing audio was not changed")
    expected = set(spec["files"]) | {"RIGHTS.md", "LICENSE-CC-BY-4.0.txt"}
    with tempfile.TemporaryDirectory(prefix=".base-install-", dir=ASSETS) as tmp:
        stage = Path(tmp)
        with zipfile.ZipFile(io.BytesIO(data)) as pack:
            members = pack.infolist()
            if len(members) != len(expected) or {m.filename for m in members} != expected:
                raise ValueError("Unexpected Base archive contents")
            for member in members:
                limit = spec["files"].get(member.filename, {}).get("bytes", 100_000)
                if member.is_dir() or member.file_size > limit:
                    raise ValueError(f"Invalid Base entry: {member.filename}")
            for name, info in spec["files"].items():
                content = pack.read(name)
                if len(content) != info["bytes"] or hashlib.sha256(content).hexdigest() != info["sha256"]:
                    raise ValueError(f"Invalid Base file: {name}")
                with wave.open(io.BytesIO(content)) as wav:
                    if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (2, 2, 44100):
                        raise ValueError(f"Invalid Base WAV format: {name}")
                (stage / name).write_bytes(content)
        # Refuse to replace a custom local sound; Personal presets are never touched.
        for name, info in spec["files"].items():
            dest = ASSETS / name
            if dest.is_symlink() or (dest.exists() and
                    hashlib.sha256(dest.read_bytes()).hexdigest() != info["sha256"]):
                raise ValueError(f"Custom sound at {dest}; move it to a Personal preset before installing Base")
        installed = []
        try:
            for name in spec["files"]:
                dest = ASSETS / name
                if not dest.exists():
                    os.replace(stage / name, dest)
                    installed.append(dest)
        except OSError:
            for dest in installed:
                dest.unlink()
            raise
    return spec["version"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", nargs="?", type=Path, help="downloaded ZIP (also works offline/private repo)")
    args = parser.parse_args()
    try:
        print(f"Installed {install(args.archive)}")
    except (OSError, ValueError, zipfile.BadZipFile, wave.Error) as exc:
        parser.exit(1, f"Base audio: {exc}\nSee assets/RIGHTS.md for download/rebuild instructions.\n")
