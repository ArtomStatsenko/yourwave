"""Horizon (github.com/Thysrael/Horizon) as a news source: runs its pipeline in its own folder and reads the
English Markdown briefing it writes. Horizon does the picking and ranking; the radio retells and voices."""
import hashlib
import html
import os
import re
import signal
import subprocess
from pathlib import Path

from . import config, ollama_proxy

PROXY_PORT = 11435  # Horizon's data/config.json points ai.base_url here (see ollama_proxy)
_proxy = None
_running: subprocess.Popen | None = None


def stop() -> None:
    """Ends a run in flight with uv's children (own process group), so a stopping radio exits at once
    instead of waiting up to the run's timeout and holding PROXY_PORT."""
    if (proc := _running) and proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def folder(cfg: dict) -> Path:
    return config.DATA_DIR / cfg["sources"]["horizon"]["dir"]


def run(cfg: dict, timeout: int = 1800) -> Path:
    """One Horizon run. Returns the briefing file it wrote. Raises on failure."""
    global _proxy
    if _proxy is None:
        _proxy = ollama_proxy.start(PROXY_PORT, cfg["editor"]["base_url"])
    h = folder(cfg)
    if not (h / "data/config.json").exists():
        raise RuntimeError(f"Horizon is not set up in {h}")
    global _running
    proc = _running = subprocess.Popen(["uv", "run", "horizon", "--hours", str(cfg["sources"]["horizon"]["hours"])],
                                       cwd=h, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                       start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        stop()
        proc.communicate()
        raise
    finally:
        _running = None
    if proc.returncode:
        raise RuntimeError(f"horizon exited {proc.returncode}: {(err or out)[-300:]}")
    lang = cfg["sources"]["horizon"]["language"]  # must match ai.languages in Horizon's data/config.json
    briefings = sorted((h / "data/summaries").glob(f"horizon-*-{lang}.md"), key=lambda p: p.stat().st_mtime)
    if not briefings:
        raise RuntimeError("Horizon wrote no briefing")
    return briefings[-1]


def parse(markdown: str) -> list[dict]:
    """Items of a briefing in Horizon's own order (best first): title, url, summary, score, discussion."""
    items = []
    for block in re.split(r'<a id="item-[^"]+"></a>', markdown)[1:]:
        head = re.search(r"^### \[(.+?)\]\((https?://[^)\s]+)\)\s*(?:⭐️\s*([\d.]+)/10)?", block, re.M)
        if not head:
            continue
        rest = block[head.end():].strip()
        summary = rest.split("\n\n", 1)[0].strip()
        if summary.startswith(("**", "<", "hackernews", "rss")):
            continue  # no plain summary paragraph: nothing safe to retell
        discussion = re.search(r"\[Discussion\]\((https?://[^)\s]+)\)", block)
        items.append({
            "title": html.unescape(head.group(1)).replace("\\", ""),
            "url": head.group(2),
            "score": float(head.group(3)) if head.group(3) else None,
            "summary": html.unescape(re.sub(r"\\(.)", r"\1", summary)),
            "discussion": discussion.group(1) if discussion else "",
        })
    return items


def item_id(it: dict) -> str:
    return "horizon:" + hashlib.sha256(it["url"].encode()).hexdigest()[:16]
