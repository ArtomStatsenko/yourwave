import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import tomllib
import urllib.request
from pathlib import Path

from . import config

MAX_CHARS = 4000  # VoiceStudio limit is 4096 per request


class VoiceStudio:
    def __init__(self, base_url: str, voice_id: str, language: str, speed: float = 1.0, instruct: str = "",
                 seed: int | None = None):
        self.base_url, self.voice_id, self.language = base_url.rstrip("/"), voice_id, language
        self.speed, self.instruct, self.seed = speed, instruct, seed

    def _get(self, path: str, timeout=3):
        with urllib.request.urlopen(self.base_url + path, timeout=timeout) as r:
            return json.load(r)

    def health(self) -> bool:
        try:
            return self._get("/health").get("status") == "ok"
        except Exception:
            return False

    def voices(self) -> list[dict]:
        return self._get("/v1/audio/voices")["voices"]

    def synthesize(self, text: str, out: Path, seed: int | None = None) -> None:
        """seed: this take only (a retry with a neighbouring seed); the voice's own seed otherwise."""
        if len(text) > MAX_CHARS:
            raise ValueError("text too long for one TTS request")
        req_body = {"model": "tts-1", "voice": self.voice_id, "input": text, "language": self.language,
                    "response_format": "wav", "speed": self.speed}
        if self.instruct:
            req_body["instruct"] = self.instruct  # voice design: the built-in voice is described, not cloned
        if (seed := self.seed if seed is None else seed) is not None:
            req_body["seed"] = seed  # same seed -> same designed voice in every clip
        body = json.dumps(req_body).encode()
        req = urllib.request.Request(self.base_url + "/v1/audio/speech", body, {"Content-Type": "application/json"})
        tmp = out.with_suffix(".tmp.wav")
        with urllib.request.urlopen(req, timeout=600) as r:
            tmp.write_bytes(r.read())
        os.replace(tmp, out)

    def transcribe(self, path: Path) -> str:
        return transcribe(path, self.language)

    def cache_key(self, text: str) -> str:
        key = f"voicestudio|{self.voice_id}|{self.language}|{self.speed}|{self.instruct}|{self.seed}|{text}"
        return hashlib.sha256(key.encode()).hexdigest()[:32]


class MlxClone:
    """Qwen3-TTS (or another mlx-audio model) cloning a reference clip, through a local mlx_audio.server.
    The voice is the reference: ref + ".wav" and its transcript ref + ".txt" (picked by A/B test, docs/tts-ab-test.md).
    The server takes no seed: a retry differs by sampling."""
    seed = None

    def __init__(self, base_url: str, model: str, ref: Path, speed: float = 1.0, language: str = "ru"):
        self.base_url, self.model, self.speed, self.language = base_url.rstrip("/"), model, speed, language
        self.ref_audio, self.ref_text = str(ref.with_suffix(".wav")), ref.with_suffix(".txt").read_text(encoding="utf-8").strip()
        self._ref_id = hashlib.sha256(Path(self.ref_audio).read_bytes()).hexdigest()[:12]

    def health(self) -> bool:
        try:
            with urllib.request.urlopen(self.base_url + "/", timeout=3) as r:
                return r.status == 200
        except OSError:
            return False

    def synthesize(self, text: str, out: Path, seed: int | None = None) -> None:
        if len(text) > MAX_CHARS:
            raise ValueError("text too long for one TTS request")
        body = json.dumps({"model": self.model, "input": text, "ref_audio": self.ref_audio, "ref_text": self.ref_text,
                           "lang_code": "russian", "speed": self.speed, "response_format": "wav"}).encode()
        req = urllib.request.Request(self.base_url + "/v1/audio/speech", body, {"Content-Type": "application/json"})
        raw = out.with_suffix(".raw.wav")
        try:
            with urllib.request.urlopen(req, timeout=600) as r, open(raw, "wb") as f:
                shutil.copyfileobj(r, f)
            # mlx-audio leaves silence at the edges; the noise check reads a long quiet tail as noise (A/B test)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-af",
                            "silenceremove=start_periods=1:start_threshold=-45dB:stop_periods=-1:stop_threshold=-45dB"
                            ":stop_duration=0.2", str(out)], check=True, capture_output=True)
        finally:
            raw.unlink(missing_ok=True)

    def transcribe(self, path: Path) -> str:
        return transcribe(path, self.language)

    def cache_key(self, text: str) -> str:
        key = f"mlx|{self.model}|{self._ref_id}|{self.ref_text}|{self.speed}|{text}"
        return hashlib.sha256(key.encode()).hexdigest()[:32]


def make_voice(cfg: dict, role: str = "host"):
    """The voice for a role ("host", "cohost") with the configured engine: VoiceStudio (OmniVoice, a voice by
    description) or "qwen3" (Qwen3-TTS cloning the role's reference clip in DATA_DIR/voices)."""
    v = cfg["voice"]
    if v["engine"] == "qwen3":
        return MlxClone(v["mlx_audio_url"], v["qwen_model"], config.DATA_DIR / "voices" / role, v["speed"])
    instruct, seed = (v["cohost_instruct"], v["cohost_seed"]) if role == "cohost" else (v["instruct"], v["seed"])
    return VoiceStudio(v["base_url"], v["voice_id"], v["language"], v["speed"], instruct, seed)


def transcribe(path: Path, language: str = "ru") -> str:
    # Reuse VoiceStudio's installed Whisper and cached weights; never download models for a weather check.
    python = Path(config.load()["voice"]["server_dir"]).expanduser() / ".venv/bin/python"
    script = ("import json,sys,mlx_whisper; "
              "r=mlx_whisper.transcribe(sys.argv[1],path_or_hf_repo='mlx-community/whisper-large-v3-turbo',"
              "language=sys.argv[2],condition_on_previous_text=False); print(json.dumps(r['text']))")
    result = subprocess.run([str(python), "-c", script, str(path), language],
                            env=dict(os.environ, HF_HUB_OFFLINE="1"), capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise RuntimeError("Local speech verification unavailable; weather withheld")
    text = json.loads(result.stdout.strip().splitlines()[-1])
    if not isinstance(text, str):
        raise ValueError("Invalid speech transcription")
    return text


VOCAL_SCRIPT = """import json, sys, mlx_whisper
BAD = ("продолжение следует", "субтитры", "thank you for watching", "спасибо за просмотр", "amara.org")
r = mlx_whisper.transcribe(sys.argv[1], path_or_hf_repo="mlx-community/whisper-large-v3-turbo",
                           condition_on_previous_text=False, word_timestamps=True)
segs = [s for s in r["segments"] if s.get("no_speech_prob", 1) < 0.5 and s.get("avg_logprob", -9) > -1.0
        and s.get("compression_ratio", 9) < 2.4 and not any(b in s["text"].lower() for b in BAD)]
words = [w for s in segs for w in s.get("words", []) if w.get("probability", 0) > 0.3]
print(json.dumps([words[0]["start"], words[-1]["end"]] if len(segs) >= 3 and words else None))
"""


def vocal_span(path: Path) -> tuple[float, float] | None:
    """First and last sung word of a song (seconds), by the local Whisper with word times; None if it heard none.
    Runs at background priority (taskpolicy -b, nice 20): about 30 s a song, the radio and the Mac stay responsive."""
    python = Path(config.load()["voice"]["server_dir"]).expanduser() / ".venv/bin/python"
    result = subprocess.run(["taskpolicy", "-b", "nice", "-n", "20", str(python), "-c", VOCAL_SCRIPT, str(path)],
                            env=dict(os.environ, HF_HUB_OFFLINE="1"), capture_output=True, text=True, timeout=600)
    if result.returncode:
        raise RuntimeError("Local Whisper unavailable")
    span = json.loads(result.stdout.strip().splitlines()[-1])
    return tuple(span) if span else None


def start_backend(project: str, port: int, log) -> subprocess.Popen:
    """Starts the VoiceStudio voice server by itself, as the app would, but without the app: no window, no Dock
    icon. Same server, same voice: a clip made this way is identical to one made through the app (measured:
    same length and loudness). The app's own analytics stay off (it is off by default, this also kills it)."""
    root = Path(project).expanduser()
    python = root / ".venv/bin/python"
    if not python.exists() or not (root / "backend/main.py").exists():
        raise FileNotFoundError(f"no VoiceStudio server in {root}")
    env = dict(os.environ, OMNIVOICE_PORT=str(port), OMNIVOICE_ANALYTICS_DISABLED="1",
               PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1")
    for name, tool in (("FFMPEG_PATH", "ffmpeg"), ("FFPROBE_PATH", "ffprobe"), ("OMNIVOICE_FFPROBE_PATH", "ffprobe")):
        if found := shutil.which(tool):
            env[name] = found
    return subprocess.Popen([str(python), "-m", "uvicorn", "main:app", "--app-dir", str(root / "backend"),
                             "--host", "127.0.0.1", "--port", str(port)], cwd="/", env=env, stdout=log,
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)


def start_mlx_server(folder: str, port: int, log) -> subprocess.Popen:
    """mlx-audio's OpenAI-compatible server from its own venv (folder/.venv), for the "qwen3" voice engine."""
    root = Path(folder).expanduser()
    server = (root if root.is_absolute() else config.DATA_DIR / root) / ".venv/bin/mlx_audio.server"
    if not server.exists():
        raise FileNotFoundError(f"no mlx-audio server in {folder} (uv pip install 'mlx-audio[server]')")
    # cwd: the server writes its logs/ next to where it runs (the root of the disk is read-only)
    return subprocess.Popen([str(server), "--host", "127.0.0.1", "--port", str(port)], cwd=str(server.parents[2]),
                            env=dict(os.environ, PYTHONUNBUFFERED="1"), stdout=log, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, start_new_session=True)


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


class MockTTS:
    """Test stand-in: a tone as long as the text would take to speak, with a speech-like spectrum
    so it passes audio.speech_problem."""
    voice_id = "mock"

    def __init__(self, chars_per_sec=14.0):
        self.cps = chars_per_sec

    def health(self) -> bool:
        return True

    def voices(self) -> list[dict]:
        return [{"voice_id": "mock"}]

    def synthesize(self, text: str, out: Path, seed: int | None = None) -> None:
        d = max(1.0, len(text) / self.cps)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        f"aevalsrc=0.1*sin(2*PI*1200*t)+0.1*sin(2*PI*2400*t)+0.1*sin(2*PI*3300*t):s=24000:d={d}", "-ar", "24000", "-ac", "1", str(out)], check=True)

    def cache_key(self, text: str) -> str:
        return hashlib.sha256(f"mock|{text}".encode()).hexdigest()[:32]


def pronounce(text: str) -> str:
    """Replaces Latin words the Russian voice reads letter by letter ("GitHub" -> "Гит хуб") with their spoken form.
    Longer entries first, so "Claude Code" wins over "Claude"."""
    f = config.ASSETS_DIR / "pronunciation.toml"
    words = tomllib.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    for k in sorted(words, key=len, reverse=True):
        text = re.sub(rf"(?<![\w-]){re.escape(k)}(?![\w-])", words[k], text, flags=re.I)
    return text


def cached_synthesize(tts, text: str, check=None) -> Path:
    """TTS with a cache keyed by the text. `check(path) -> error|None` rejects broken audio: with a fixed seed the
    engine is deterministic, so a text that came out as noise is retried with other punctuation (same words, same
    voice), then with neighbouring seeds. The first good take is cached under the original text."""
    text = pronounce(re.sub(r"https?://\S+", "", text, flags=re.I)).strip()
    # The voice turns "Далат: плюс 16" into noise (measured: weather and reminders); a dash reads the same pause.
    text = re.sub(r"(?<=\w):\s+", " — ", text)
    if not text:
        raise ValueError("No speakable text after removing links")
    out = config.DATA_DIR / "cache/tts" / f"{tts.cache_key(text)}.wav"
    if out.exists() and (check is None or not check(out)):
        out.touch()  # reused clips (topic names) stay out of the 48 h cache cleanup
        return out
    if check is None:
        tts.synthesize(text, out)
        return out
    takes = [(text, None), (text.replace(", ", " — "), None), (text.rstrip(".!?…") + "…", None)]
    if getattr(tts, "seed", None) is not None:
        takes += [(text, tts.seed + d) for d in (2, 5)]
    # A take file of its own: two threads may voice the same phrase at once. The seed goes with the request, never
    # onto the shared voice object: another thread's take would get this retry's seed, i.e. another voice.
    fd, name = tempfile.mkstemp(suffix=".take.wav", dir=out.parent)
    os.close(fd)
    tmp, errors = Path(name), []
    try:
        for variant, seed in dict.fromkeys(takes):
            tts.synthesize(variant, tmp, **({"seed": seed} if seed is not None else {}))
            if not (err := check(tmp)):
                os.replace(tmp, out)
                return out
            errors.append(err)
    finally:
        tmp.unlink(missing_ok=True)
    raise RuntimeError(f"TTS audio invalid after {len(errors)} takes: {errors[-1]}")
