import json
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from . import config


def send(cmd: str, timeout=10, **args) -> dict | None:
    try:
        with socket.socket(socket.AF_UNIX) as s:
            s.settimeout(timeout)
            s.connect(str(config.SOCK))
            s.sendall((json.dumps({"cmd": cmd, **args}) + "\n").encode())
            return json.loads(s.makefile().readline())
    except (FileNotFoundError, ConnectionRefusedError):
        return None
    except (TimeoutError, ValueError, ConnectionResetError):  # busy or stopping, not stopped: no second radio offered
        return {"ok": False, "error": "The radio did not answer in time. Try again."}


def print_status(resp: dict | None) -> None:
    if resp is None:
        print("STOPPED")
        return
    if not resp.get("ok"):
        print("Ошибка:", resp.get("error"))
        return
    s = resp["status"]
    print(f"{s['state']}  {s.get('current') or ''}")
    if "news_ready" in s:
        print(f"новости: готово {s['news_ready']}, готовится {s['news_preparing']}  |  "
              f"речь за 60 мин: {s['spoken_last_hour_sec']}/{s['speech_limit_sec']} c  |  "
              f"VoiceStudio: {s['voicestudio']}  |  LLM-вызовов: {s['llm_calls']}")
        for name, state in s.get("sources", {}).items():
            print(f"источник {name}: {state}")
        if s["last_error"]:
            print("последняя ошибка:", s["last_error"])


def start_daemon(record: str | None = None) -> dict | None:
    """Starts the radio process if it is not running. Returns its status, or None if it did not come up."""
    if (resp := send("play")) is not None:
        return resp
    config.load()
    log = open(config.DATA_DIR / "logs/radio.log", "a")
    env = dict(os.environ)
    if record:
        env["RADIO_RECORD"] = record
    subprocess.Popen([sys.executable, "-m", "radio", "_daemon"], cwd=config.PKG_DIR.parent, env=env,
                     stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    for _ in range(100):
        time.sleep(0.1)
        if (resp := send("status")) is not None:
            return resp
    return None


def cmd_play(args):
    if send("status") is not None:
        print_status(send("play"))
        if args.record:
            print("Радио уже играет: запись включается только при новом запуске (radio stop, затем play --record).")
        return
    record = str(Path(args.record).expanduser().resolve()) if args.record else None
    if record:
        print("Запись эфира в", record)
    if (resp := start_daemon(record)) is not None:
        print_status(resp)
        if config.load()["output"]["mode"] == "browser":
            print("Звук идёт в браузер: откройте `radio ui` и нажмите ▶ listen (без слушателя эфир встанет на паузу).")
        return
    print("Радио не запустилось. Последние строки журнала:")
    print("".join(open(config.DATA_DIR / "logs/radio.log").readlines()[-10:]))
    sys.exit(1)


def cmd_simple(args):
    resp = send(args.command)
    print_status(resp)
    if resp is None or not resp.get("ok"):
        sys.exit(1)


def cmd_history(args):
    from . import store
    db = store.connect()
    rows = db.execute("""SELECT p.played_at, p.status, p.part, p.parts, p.speech_sec, i.topic, i.source, i.url, i.file
                         FROM packages p JOIN items i ON i.id=p.item_id WHERE p.played_at IS NOT NULL
                         ORDER BY p.played_at DESC LIMIT ?""", (args.last,)).fetchall()
    if not rows:
        print("Пока ничего не звучало.")
    for r in rows:
        t = datetime.fromisoformat(r["played_at"]).astimezone().strftime("%d.%m %H:%M")
        print(f"{t}  {r['status']:7}  {r['topic']} ({r['part']}/{r['parts']}, {r['speech_sec']:.0f}s)  "
              f"[{r['source']}]  {r['url'] or r['file']}")


def write_inbox(stem: str, item_id: str, body: str, topic: str, published, source: str, url: str) -> None:
    from . import content
    cfg = config.load()
    dest = config.DATA_DIR / "inbox" / f"{published.strftime('%Y%m%dT%H%M%S')}-{stem}.md"
    front = {"id": item_id, "topic": topic, "published_at": published.isoformat(), "source": source, "url": url}
    tmp = dest.with_suffix(".tmp")
    tmp.write_text("---\n" + "".join(f"{k}: {v}\n" for k, v in front.items()) + "---\n" + body + "\n",
                   encoding="utf-8")
    tmp.rename(dest)  # atomic: the daemon never sees a half-written file
    print("Добавлено в inbox:", dest.name)
    if not content.is_fresh(published, cfg["sources"]["max_age_hours"]):
        print("Внимание: сводка старше 24 ч, в эфир не попадёт.")


def cmd_import(args):
    from . import content
    src = Path(args.path).expanduser()
    item = content.parse_inbox_file(src)
    published = content.parse_time(args.published_at) if args.published_at else item["published_at"]
    if published is None:
        sys.exit("Нужна дата выпуска с часовым поясом: --published-at 2026-10-18T12:00:00+07:00")
    write_inbox(src.stem, item["id"].removeprefix("digest:"), item["body"], args.topic or item["topic"], published,
                args.source or item["source"], args.url or item["url"])


def cmd_paste(args):
    """A digest copied from ChatGPT: clipboard text goes into the inbox verbatim."""
    import hashlib
    from . import content, store
    body = subprocess.run(["pbpaste"], capture_output=True, text=True).stdout.strip()
    if len(body) < 20:
        sys.exit("В буфере обмена нет текста сводки.")
    # Default date = now: the digest was just received. Pass --published-at for an older one.
    published = content.parse_time(args.published_at) if args.published_at else store.now()
    if published is None:
        sys.exit("Дата должна быть с часовым поясом: --published-at 2026-10-18T12:00:00+07:00")
    item_id = hashlib.sha256(body.encode()).hexdigest()[:16]  # the same text pasted twice is one item
    write_inbox(f"paste-{item_id[:6]}", item_id, body, args.topic, published, args.source, "")
    print(f"{len(body)} символов, тема «{args.topic}», дата {published.astimezone():%d.%m %H:%M}")


def cmd_source(_):
    resp = send("status")
    if resp and resp.get("ok"):
        for name, state in resp["status"].get("sources", {}).items():
            print(f"{name}: {state}")
        print("ready_inbox: on (inbox + radio import / radio paste)")
        return
    cfg = config.load()
    print(f"github_trending: {'enabled' if cfg['sources']['github_trending']['enabled'] else 'disabled'} "
          f"(every {cfg['sources']['github_trending']['refresh_minutes']} min), radio is stopped")
    print("ready_inbox: on (inbox + radio import / radio paste)")


def cmd_doctor(_):
    from . import music, settings, tts as tts_mod
    cfg = config.load()
    settings.apply(cfg, settings.base(cfg))  # the voice chosen on the Settings tab, as the radio uses it
    ok = True

    def check(name, good, detail=""):
        nonlocal ok
        ok &= bool(good)
        print(f"{'✓' if good else '✗'} {name} {detail}")

    for tool in ("ffmpeg", "ffprobe", "liquidsoap"):
        check(tool, shutil.which(tool))
    devs = subprocess.run(["ffmpeg", "-hide_banner", "-devices"], capture_output=True, text=True).stdout
    check("macOS audio output (ffmpeg audiotoolbox)", "audiotoolbox" in devs)
    files = music.scan(cfg["music"]["path"]) if cfg["music"]["path"].exists() else []
    check("music", files, f"{len(files)} files in {cfg['music']['path']}")
    v = cfg["voice"]
    tts = tts_mod.make_voice(cfg)
    healthy = tts.health()
    qwen = v["engine"] == "qwen3"
    check("Qwen3-TTS health" if qwen else "VoiceStudio health", healthy, v["mlx_audio_url"] if qwen else v["base_url"])
    if healthy:
        if qwen:
            check("voice", True, f"clone of {config.DATA_DIR / 'voices/host.wav'}")
        elif v["voice_id"] == "default":
            check("voice", True, f"built-in, designed: {v['instruct'] or 'engine default'} (seed {v['seed']})")
        else:
            voice = next((x for x in tts.voices() if x["voice_id"] == v["voice_id"]), None)
            check("voice profile", voice, f"{v['voice_id']} {voice['name'] if voice else 'NOT FOUND'}")
        out = config.DATA_DIR / "cache/tts/doctor.wav"
        t0 = time.time()
        try:
            tts.synthesize("Проверка связи. Радио в эфире.", out)
            from . import audio
            err = audio.validate(out) or audio.speech_problem(out)
            check("Russian test phrase", not err,
                  err or f"{audio.duration(out):.1f}s audio in {time.time() - t0:.1f}s")
        except Exception as e:
            check("Russian test phrase", False, str(e))
    print("config:", config.DATA_DIR / "config.toml")
    sys.exit(0 if ok else 1)


def build_parser():
    from . import agent_cli
    p = agent_cli.Parser(prog="radio", description="Personal AI Radio; use --json for machine-readable output")
    p.add_argument("--json", action="store_true", help="machine-readable JSON result and exit codes")
    sub = p.add_subparsers(dest="command", required=True)
    pl = sub.add_parser("play")
    pl.add_argument("--record", metavar="FILE.wav", help="также записывать эфир в файл")
    pl.set_defaults(fn=cmd_play)
    for name in ("pause", "resume", "stop", "next", "skip-news", "status"):
        sub.add_parser(name).set_defaults(fn=cmd_simple)
    h = sub.add_parser("history")
    h.add_argument("--last", type=int, default=20)
    h.set_defaults(fn=cmd_history)
    i = sub.add_parser("import", help="добавить готовую сводку (.md/.json) в inbox")
    i.add_argument("path")
    i.add_argument("--topic")
    i.add_argument("--published-at")
    i.add_argument("--source")
    i.add_argument("--url")
    i.set_defaults(fn=cmd_import)
    pa = sub.add_parser("paste", help="сводка из буфера обмена (например, из ChatGPT) в inbox")
    pa.add_argument("--topic", default="Сводка новостей")
    pa.add_argument("--published-at")
    pa.add_argument("--source", default="chatgpt")
    pa.set_defaults(fn=cmd_paste)
    src = sub.add_parser("source")
    src.add_argument("action", choices=["list"])
    src.set_defaults(fn=cmd_source)
    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    u = sub.add_parser("ui", help="интерфейс в браузере")
    u.add_argument("--port", type=int, default=8765)
    u.set_defaults(fn=lambda a: __import__("radio.ui").ui.serve(a.port))
    d = sub.add_parser("_daemon")
    d.add_argument("--output", default="device")
    d.set_defaults(fn=lambda a: __import__("radio.daemon").daemon.main(a.output))
    agent_cli.register(sub)
    return p


def main(argv=None):
    from . import agent_cli, control
    argv = list(sys.argv[1:] if argv is None else argv)
    options_end = argv.index('--') if '--' in argv else len(argv)
    json_mode = '--json' in argv[:options_end]
    argv = [arg for index, arg in enumerate(argv) if arg != '--json' or index >= options_end]
    agent_cli.Parser.json_errors = json_mode
    p = build_parser()
    args = p.parse_args(argv)
    args.json = json_mode
    if args.fn == agent_cli.run:
        raise SystemExit(args.fn(args))
    if json_mode:
        try:
            if args.command in ('status', 'pause', 'resume', 'stop', 'next', 'skip-news'):
                result = control.read('status') if args.command == 'status' else control.execute({'cmd': args.command})[0]
            elif args.command == 'play':
                record = str(Path(args.record).expanduser().resolve()) if args.record else None
                if record and send('status') is not None:
                    result = {'ok': False, 'error': 'Recording requires a stopped station; existing playback was left unchanged.'}
                else:
                    result = start_daemon(record) or {'ok': False, 'error': 'Radio startup was not confirmed'}
            else:
                import contextlib
                import io
                output = io.StringIO()
                code = 0
                with contextlib.redirect_stdout(output):
                    try:
                        args.fn(args)
                    except SystemExit as error:
                        code = error.code or 0
                result = {'ok': code == 0, 'output': output.getvalue()}
            raise SystemExit(agent_cli.emit(result, True))
        except (ValueError, OSError):
            raise SystemExit(agent_cli.emit({'ok': False, 'error': 'Operation failed; check arguments and local permissions.'}, True))
    if args.command == "status" and send("status") is None:
        print("STOPPED")
        return
    args.fn(args)


if __name__ == "__main__":
    main()
