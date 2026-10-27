"""FFmpeg helpers: measuring, validating and rendering news packages."""
import functools
import hashlib
import json
import os
import re
import subprocess
import tomllib
from pathlib import Path

from .config import ASSETS_DIR

LEAD_SEC = 0.4   # bed alone after the stinger, before the voice
TAIL_SEC = 0.6   # bed alone after the voice
XF_IN, XF_OUT = 0.3, 0.5  # overlaps: body starts this long before the logo's loud part ends; outro before body end
RAMP_SEC = 0.5   # duck/unduck ramp
BED_OPEN_DB = -6.0
FMT = "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo"


def _ffmpeg(*args, timeout=300) -> str:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", *args], capture_output=True, text=True, errors="replace", timeout=timeout)
    if r.returncode:
        raise RuntimeError(f"ffmpeg failed: {r.stderr[-600:]}")
    return r.stderr


def join_packages(paths: list[Path], out: Path) -> Path:
    """Reuse ready audio for an immediate transition without generating host links."""
    if len(paths) == 1:
        return paths[0]
    inputs = [arg for path in paths for arg in ('-i', str(path))]
    formats = ''.join(f'[{i}:a]{FMT}[p{i}];' for i in range(len(paths)))
    streams = ''.join(f'[p{i}]' for i in range(len(paths)))
    _ffmpeg('-y', *inputs, '-filter_complex', f'{formats}{streams}concat=n={len(paths)}:v=0:a=1[a]',
            '-map', '[a]', '-c:a', 'pcm_s16le', str(out))
    return out


def tags(path: Path) -> tuple[str, str]:
    """(artist, title) from the file's tags; "" where a tag is missing."""
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags=artist,title:stream_tags=artist,title",
                        "-of", "json", str(path)], capture_output=True, text=True, errors="replace", timeout=30)
    try:
        d = json.loads(r.stdout or "{}")
    except ValueError:
        return "", ""
    # The file's own tags win: an embedded cover picture is a stream with its own title ("Cover").
    found = {k.lower(): v for t in [s.get("tags", {}) for s in d.get("streams", [])] + [d.get("format", {}).get("tags", {})]
             for k, v in t.items() if v.strip()}
    return found.get("artist", "").strip(), found.get("title", "").strip()


def duration(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True, errors="replace", timeout=30)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def lufs(path: Path) -> float | None:
    """Integrated loudness; None if the file does not decode."""
    try:
        err = _ffmpeg("-i", str(path), "-af", "ebur128=framelog=quiet", "-f", "null", "-", timeout=600)
    except (RuntimeError, subprocess.TimeoutExpired):
        return None
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", err)
    return float(m[-1]) if m else None


def validate(path: Path, min_sec=0.5, max_sec=600.0) -> str | None:
    """Returns an error string, or None if the file is playable audio of sane length."""
    if not path.exists() or path.stat().st_size < 1000:
        return "missing or empty file"
    d = duration(path)
    if not min_sec <= d <= max_sec:
        return f"bad duration {d:.1f}s"
    err = _ffmpeg("-i", str(path), "-af", "volumedetect", "-f", "null", "-")
    m = re.search(r"mean_volume: (-?[\d.]+) dB", err)
    if not m or float(m.group(1)) < -45:
        return "silence"
    return None


def speech_problem(path: Path) -> str | None:
    """TTS sometimes returns noise or a hum instead of speech; neither may go on air.
    Calibrated on this machine: speech has spectral centroid ~2-3 kHz and flatness ~0.25-0.3;
    broken clips measured 5.7-7.2 kHz / 0.5 (noise) and 1 kHz / 0.1 (hum)."""
    out = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-i", str(path), "-af",
                          "aspectralstats=measure=centroid+flatness,ametadata=print:file=-", "-f", "null", "-"],
                         capture_output=True, text=True, errors="replace", timeout=120).stdout
    cent = [float(x) for x in re.findall(r"\.1\.centroid=(\S+)", out)]
    flat = [float(x) for x in re.findall(r"\.1\.flatness=(\S+)", out)]
    if not cent:
        return "no spectrum"
    c, f = sum(cent) / len(cent), sum(flat) / len(flat)
    if not 1400 <= c <= 4500 or f > 0.42:
        return f"not speech (centroid {c:.0f} Hz, flatness {f:.2f})"
    # A clip that is mostly silence with a short burst: speech has pauses, not 80 % of silence. Measured on
    # cached clips: broken 0.19 voiced, good ones 0.78-1.0 (a 1.3 s clip 0.42, so short clips are exempt).
    d = duration(path)
    if d >= 2.0:
        quiet = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-i", str(path), "-af", "silencedetect=noise=-35dB:d=0.25",
                                "-f", "null", "-"], capture_output=True, text=True, errors="replace", timeout=120).stderr
        voiced = 1 - sum(float(x) for x in re.findall(r"silence_duration: ([\d.]+)", quiet)) / d
        if voiced < 0.5:
            return f"mostly silence ({voiced:.0%} voiced)"
    return None


def _bed_gain_expr(v0: float, v1: float, open_db: float, duck_db: float) -> str:
    o, d, r = 10 ** (open_db / 20), 10 ** (duck_db / 20), RAMP_SEC
    a = v0 - r
    return (f"if(lt(t,{a}),{o},if(lt(t,{v0}),{o}+({d}-{o})*(t-{a})/{r},"
            f"if(lt(t,{v1}),{d},if(lt(t,{v1 + r}),{d}+({o}-{d})*(t-{v1})/{r},{o}))))")


@functools.lru_cache(maxsize=256)
def active_span(path: Path, floor_lufs: float = -35.0) -> tuple[float, float]:
    """(start, end) of the audible part: skips leading silence and a long quiet tail,
    so a logo with a 3 s decay does not leave a gap before the voice."""
    out = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-i", str(path), "-af",
                          "ebur128=metadata=1,ametadata=print:key=lavfi.r128.M:file=-", "-f", "null", "-"],
                         capture_output=True, text=True, errors="replace", timeout=60).stdout
    t, loud = 0.0, []
    for line in out.splitlines():
        if "pts_time:" in line:
            t = float(line.split("pts_time:")[1].split()[0])
        elif "r128.M=" in line and float(line.split("=")[1]) >= floor_lufs:
            loud.append(t)
    d = duration(path)
    if not loud:
        return 0.0, d
    # The momentary meter looks 0.4 s back: shift the start back, keep 0.3 s of tail for the fade.
    return max(0.0, loud[0] - 0.4), min(d, loud[-1] + 0.3)


def _by_ear(path: Path) -> float:
    """Hand correction from assets/jingles/gains.toml, re-read each render so edits apply without a restart."""
    f = ASSETS_DIR / "jingles/gains.toml"
    try:
        return float(tomllib.loads(f.read_text()).get(path.name, 0.0)) if f.exists() else 0.0
    except (tomllib.TOMLDecodeError, ValueError):
        return 0.0


def pick(choices: list[Path], seed: str) -> Path:
    """Stable choice per package: re-rendering the same package gives the same jingles."""
    return choices[int(hashlib.sha256(seed.encode()).hexdigest(), 16) % len(choices)]


def _prepared(idx: int, path: Path, max_len: float | None = None) -> tuple[str, float, float]:
    """Leading silence is cut; the quiet tail is kept to ring under what comes next (overlap, not cut).
    Only a sound longer than max_len (e.g. a 10 s loop used as outro) is cut, with a fade.
    Returns (filter chain, audible length, total length) in seconds from the first sound."""
    a, b = active_span(path)
    end = duration(path)
    chain = f"[{idx}]atrim=start={a:.3f},asetpts=PTS-STARTPTS,{FMT}"
    if max_len and end - a > max_len:
        end, fade = a + max_len, min(1.0, max_len / 3)
        b = min(b, end)
        chain += f",atrim=0:{max_len:.3f},afade=t=out:st={max_len - fade:.3f}:d={fade:.3f}"
    return chain, b - a, end - a


# Concrete files (one per role) for render_package; the daemon picks them from the configured folders.
PLACEHOLDER_JINGLES = {"stinger_in": ASSETS_DIR / "stinger_in.wav", "bed": ASSETS_DIR / "bed.wav",
                       "stinger_out": ASSETS_DIR / "stinger_out.wav", "stinger_out_max_sec": 4.0,
                       "separator": ASSETS_DIR / "separator.wav"}


def choose_jingles(cfg_jingles: dict, seed: str, recent: dict[str, list[Path]] | None = None) -> dict:
    """One file per role. With `recent` (updated in place), a sound is not reused until every other
    file of its folder has played: no two packages in a row with the same logo, bed or outro."""
    out = {}
    for k, v in cfg_jingles.items():
        if not isinstance(v, list):
            out[k] = v
            continue
        if recent is None:
            out[k] = pick(v, seed + k)
            continue
        used = recent.setdefault(k, [])
        fresh = [f for f in v if f not in used[-(len(v) - 1):]] if len(v) > 1 else v
        out[k] = pick(fresh, seed + k)
        used.append(out[k])
    return out


# Around a separator: (start relative to the end of the phrase, next phrase relative to the end of the
# separator's audible part). Negative = overlap: the sound starts on the last word, the next phrase on its tail.
SEP_PAD = (-0.1, -0.2)
PAUSE_SEC = 0.3         # plain pause between segments without a sound (topic -> text)
LEAD_PAUSE_SEC = -0.3   # the stinger comes in under the lead phrase's (back-sell's) last syllable
MIN_GAP = 0.15          # never closer than this between two phrases


@functools.lru_cache(maxsize=512)
def speech_span(path: Path) -> tuple[float, float]:
    """(start, end) of speech in a TTS clip: drops the silence the engine adds at both edges."""
    err = _ffmpeg("-i", str(path), "-af", "silencedetect=n=-45dB:d=0.05", "-f", "null", "-")
    d = duration(path)
    starts = [float(x) for x in re.findall(r"silence_start: (\S+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end: (\S+)", err)]
    a = ends[0] if starts and starts[0] < 0.01 and ends else 0.0
    b = starts[-1] if starts and (len(ends) < len(starts) or ends[-1] >= d - 0.02) else d
    return (a, b) if b - a > 0.3 else (0.0, d)


TALKOVER_MAX_SEC = 2.0  # how much of the last phrase the next song may start under
TALKOVER_END_SEC = 0.4  # silence after the last word: the only part the crossfade's short fade ever touches


def render_notice(voice: Path, chime: Path, out_wav: Path) -> None:
    """A short insert: a chime, then the voice starting over its last part; -16 LUFS."""
    ca, cb = active_span(chime)
    cb = min(cb, ca + 1.6)
    va, vb = speech_span(voice)
    gain = lambda p, extra=0.0: f"volume={-16 - (lufs(p) or -16) + extra:.2f}dB"
    delay = max(0.0, cb - ca - 0.4)
    graph = (f"[0]atrim={ca:.3f}:{cb:.3f},asetpts=PTS-STARTPTS,{FMT},{gain(chime, -3)},afade=t=out:st={max(0.0, cb - ca - 0.5):.3f}:d=0.5[c];"
             f"[1]atrim={va:.3f}:{vb:.3f},asetpts=PTS-STARTPTS,{FMT},{gain(voice)},adelay={int(delay * 1000)}:all=1[v];"
             f"[c][v]amix=inputs=2:duration=longest:normalize=0,alimiter=limit=0.89,{FMT}[out]")  # static gains: loudnorm misreads 4 s
    tmp = out_wav.with_suffix(".tmp.wav")
    _ffmpeg("-y", "-i", str(chime), "-i", str(voice), "-filter_complex", graph, "-map", "[out]", "-c:a", "pcm_s16le", str(tmp))
    if err := validate(tmp, min_sec=vb - va):
        raise RuntimeError(f"rendered notice invalid: {err}")
    os.replace(tmp, out_wav)


JINGLE_AFTER_SEC = 0.15  # a host line between songs: the jingle comes in just after the last word


def render_link(voices: list[Path], out_wav: Path, jingle: Path, jingle_max_sec: float = 4.0) -> None:
    """A host line between songs (a back-sell, a music fact): the voice first, with no jingle or bed before it, so it
    starts right over the song's ending (radio.liq talks up into a spoken insert); the jingle only after the last
    word. Normalized like a package. Written to a temp file and renamed only after validation."""
    spans = [speech_span(v) for v in voices]
    durs = [b - a for a, b in spans]
    starts = [sum(durs[:i]) + PAUSE_SEC * i for i in range(len(voices))]
    t_jingle = starts[-1] + durs[-1] + JINGLE_AFTER_SEC
    gain = lambda p: f"volume={-16 - (lufs(p) or -16) + _by_ear(p):.2f}dB"
    ja, jb = active_span(jingle)
    j_chain, _, j_len = _prepared(len(voices), jingle, min(jingle_max_sec, jb - ja + 1.0))
    chain = "".join(f"[{i}]atrim={a:.3f}:{b:.3f},asetpts=PTS-STARTPTS,{FMT},{gain(v)},adelay={int(starts[i] * 1000)}:all=1[v{i}];"
                    for i, (v, (a, b)) in enumerate(zip(voices, spans)))
    n = len(voices)
    graph = (chain + f"{j_chain},{gain(jingle)},adelay={int(t_jingle * 1000)}:all=1[j];"
             + "".join(f"[v{i}]" for i in range(n)) + f"[j]amix=inputs={n + 1}:duration=longest:normalize=0,"
             f"loudnorm=I=-16:TP=-1.5:LRA=11,{FMT}[out]")
    tmp = out_wav.with_suffix(".tmp.wav")
    try:
        _ffmpeg("-y", *[a for v in voices + [jingle] for a in ("-i", str(v))], "-filter_complex", graph,
                "-map", "[out]", "-c:a", "pcm_s16le", str(tmp))
        if err := validate(tmp, min_sec=sum(durs), max_sec=t_jingle + j_len + 1):
            raise RuntimeError(f"rendered host line invalid: {err}")
        os.replace(tmp, out_wav)
    finally:
        tmp.unlink(missing_ok=True)


def render_package(voices: list[Path], out_wav: Path, bed_gain_db: float, jingles: dict | None = None,
                   separators: list[bool] | None = None, sep_pad: tuple[float, float] = SEP_PAD,
                   talkover: bool = False, lead: Path | None = None, talkover_max: float | None = None,
                   speaker_changes: list[bool] | None = None) -> float:
    """[lead] -> stinger_in -> bed with ducked voice on top -> stinger_out, normalized to -16 LUFS / -1.5 dBTP.
    lead: a dry phrase before the stinger, as a host back-sells the songs before the news sounder.
    talkover_max: how much of the last phrase the next song may start under (default TALKOVER_MAX_SEC); a set
    opener over the next song's intro may take the whole phrase.
    separators[i] says whether the gap before voices[i+1] gets the separator sound (default) or a plain pause.
    speaker_changes[i]: the next voice enters immediately, without a pause, separator or fade.
    talkover: no outro; the file ends on the last word and the bed is gone by then, so the next song can start
    under the last phrase. Returns that overlap in seconds (0 without talkover).
    Written to a temp file and renamed only after validation."""
    j = jingles or PLACEHOLDER_JINGLES
    si, bed, so, sep = j["stinger_in"], j["bed"], j["stinger_out"], j["separator"]
    si_chain, si_loud, _ = _prepared(0, si)
    # Nothing plays under the outro's tail, so a long decay would be dead air before the next song: keep 1 s of it.
    so_a, so_b = active_span(so)
    so_chain, _, so_len = _prepared(2, so, min(j["stinger_out_max_sec"], so_b - so_a + 1.0))
    sep_chain, sep_len, _ = _prepared(3, sep, 2.5)
    n = len(voices)
    separators = [True] * (n - 1) if separators is None else separators
    changes = [False] * (n - 1) if speaker_changes is None else speaker_changes
    if len(changes) != n - 1 or len(separators) != n - 1:
        raise ValueError("one transition is required between each pair of voices")
    separators = [sound and not change for sound, change in zip(separators, changes)]
    gaps = [max(MIN_GAP, sep_pad[0] + sep_len + sep_pad[1]) if s else PAUSE_SEC for s in separators]
    gaps = [0.0 if change else gap for gap, change in zip(gaps, changes)]
    spans = [speech_span(v) for v in voices]
    durs = [b - a for a, b in spans]
    vdur = sum(durs) + sum(gaps)
    body = LEAD_SEC + vdur + TAIL_SEC
    v1 = LEAD_SEC + vdur
    # Static gains from measured LUFS put every input at -16 LUFS, so bed_gain_db really is relative to the voice.
    # (loudnorm here would break the time-based ducking envelope: it rewrites frame timestamps.)
    gain = lambda p: f"volume={-16 - (lufs(p) or -16) + _by_ear(p):.2f}dB"
    t_body = max(0.0, si_loud - XF_IN)
    t_out = t_body + body - XF_OUT
    tail = 0.0
    la, lb = speech_span(lead) if lead else (0.0, 0.0)
    L = (lb - la + LEAD_PAUSE_SEC) if lead else 0.0  # everything after the lead phrase moves by this
    if talkover:
        t_out = t_body + v1 + TALKOVER_END_SEC  # the package ends just after the last word
        tail = min(talkover_max or TALKOVER_MAX_SEC, durs[-1])
    # The bed is its own layer and plays only between the jingles: it rises as the logo's loud part ends and is
    # gone when the outro starts (on top of the jingles it only muddied them). Envelope times are bed-relative.
    bed_start = max(0.0, si_loud)
    bed_len = max(1.0, t_out - bed_start)
    shift = t_body - bed_start
    fade_out = min(max(0.6, tail), bed_len / 3)  # with talkover the bed leaves during the last phrase
    starts = [LEAD_SEC + sum(durs[:i]) + sum(gaps[:i]) for i in range(n)]
    chain = []
    for i, v in enumerate(voices):
        a, b = spans[i]
        chain.append(f"[{4 + i}]atrim={a:.3f}:{b:.3f},asetpts=PTS-STARTPTS,{FMT},{gain(v)},"
                     f"adelay={int(starts[i] * 1000)}:all=1[v{i}];")
    # Each phrase is placed at its own time, so a separator may overlap the edges of phrases.
    chain.append("".join(f"[v{i}]" for i in range(n)) + f"amix=inputs={n}:normalize=0,apad=whole_dur={body}[v];")
    mix = ["[v]"]
    sounds = [i for i, s in enumerate(separators) if s]
    if sounds:
        # Separator sits in the gap, a little under the voice level so it reads as punctuation.
        chain.append(f"{sep_chain},{gain(sep)},volume=-2dB,asplit={len(sounds)}" + "".join(f"[s{i}]" for i in sounds) + ";")
        for i in sounds:
            t = max(0.0, starts[i] + durs[i] + sep_pad[0])
            chain.append(f"[s{i}]adelay={int(t * 1000)}:all=1[sd{i}];")
            mix.append(f"[sd{i}]")
    if lead:
        chain.append(f"[{4 + n}]atrim={la:.3f}:{lb:.3f},asetpts=PTS-STARTPTS,{FMT},{gain(lead)}[ld];")
    graph = "".join(chain) + (
        f"[1]atrim=0:{bed_len:.3f},asetpts=N/SR/TB,{FMT},{gain(bed)},"
        f"volume='{_bed_gain_expr(shift + LEAD_SEC, shift + v1, BED_OPEN_DB, bed_gain_db)}':eval=frame,"
        f"afade=t=in:d=0.3:curve=qsin,"
        f"afade=t=out:st={bed_len - fade_out:.3f}:d={fade_out:.3f}:curve=qsin,"
        f"adelay={int((bed_start + L) * 1000)}:all=1[b];"
        + (f"{''.join(mix)}amix=inputs={len(mix)}:duration=first:normalize=0[body];" if len(mix) > 1
           else f"{mix[0]}anull[body];") +
        # Layers instead of cuts: the body starts as the logo's loud part ends (its tail rings on under the bed),
        # the outro starts XF_OUT before the body's end.
        f"{si_chain},{gain(si)},adelay={int(L * 1000)}:all=1[si];"
        + ("" if talkover else f"{so_chain},{gain(so)},adelay={int((t_out + L) * 1000)}:all=1[so];") +
        f"[body]adelay={int((t_body + L) * 1000)}:all=1[bd];"
        + (f"[si][bd][b]{'[ld]' if lead else ''}amix=inputs={3 + bool(lead)}:duration=longest:normalize=0,"
           f"atrim=0:{t_out + L:.3f},"
           if talkover else f"[si][bd][b][so]{'[ld]' if lead else ''}amix=inputs={4 + bool(lead)}:duration=longest:normalize=0,") +
        f"loudnorm=I=-16:TP=-1.5:LRA=11,{FMT}[out]"
    )
    tmp = out_wav.with_suffix(".tmp.wav")
    try:
        inputs = ["-i", str(si), "-stream_loop", "-1", "-i", str(bed), "-i", str(so), "-i", str(sep)]
        for v in voices + ([lead] if lead else []):
            inputs += ["-i", str(v)]
        _ffmpeg("-y", *inputs, "-filter_complex", graph, "-map", "[out]", "-c:a", "pcm_s16le", str(tmp))
        if err := validate(tmp, min_sec=vdur + 1, max_sec=t_out + L + so_len + 1):
            raise RuntimeError(f"rendered package invalid: {err}")
        os.replace(tmp, out_wav)
    finally:
        tmp.unlink(missing_ok=True)
    return tail
