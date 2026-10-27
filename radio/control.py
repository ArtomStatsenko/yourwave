"""Shared validated operations for the browser API and the native radio CLI."""
import time
from . import catchup, chatgpt, config, editor, hooks, openrouter, plugins, public_news, reddit, settings, store, telegram, tts, ytmusic
from .timeline import timeline

VOICE_SAMPLE = "Добрый вечер! Вы слушаете Персональное радио. Сейчас — коротко о новостях, а потом снова музыка."
COMMANDS = {"pause", "resume", "next", "skip-news", "stop"}
# name -> fields accepted by the existing API; daemon validation owns planning semantics.
OPERATIONS = {
    'catchup': [],
    'play': [], 'pause': [], 'resume': [], 'stop': [], 'next': [], 'skip-news': [],
    'plugin': ['name', 'on'], 'setting': ['key', 'value'], 'settings_reset': ['key'],
    'audio_preset': ['name'], 'reddit_community': ['action', 'value'], 'telegram_channel': ['action', 'value'],
    'ytmusic_connect': ['cookie', 'playlist', 'browser'], 'voice_sample': [], 'llm_key': ['provider', 'key'],
    'chatgpt_login': [], 'chatgpt_logout': [], 'openrouter_login': [], 'openrouter_logout': [],
    'hook_review': ['id', 'approved'], 'reroll-next': ['ref'],
    'replay': ['kind', 'ref'], 'unqueue': ['kind', 'ref'],
    'direction': ['action', 'kind', 'ref', 'text', 'at', 'id', 'token', 'changes', 'version', 'remember_undo'],
}
READS = ('status', 'snapshot', 'settings', 'plugins', 'experiments', 'timeline', 'queue', 'hooks', 'ytmusic', 'audio', 'capabilities')


def capabilities():
    return {'api_version': 1, 'ok': True, 'reads': list(READS),
            'operations': {name: {'fields': fields} for name, fields in OPERATIONS.items()},
            'client_only': ['appearance', 'monitor volume', 'browser listening'],
            'client_note': 'These preferences belong to a browser, not the station. probe is always silent.'}


def read(name, *, sender=None, hours=12, limit=120):
    from .__main__ import send
    sender = sender or send
    if name not in READS:
        raise ValueError('Unknown read operation')
    if name == 'capabilities':
        return capabilities()
    cfg = config.load()
    if name == 'settings':
        return {'ok': True, 'settings': settings.listing(cfg)}
    if name in ('plugins', 'experiments'):
        rows = plugins.listing(cfg)
        return {'ok': True, name: [r for r in rows if name != 'experiments' or r['section'] == 'experiments']}
    if name == 'timeline':
        if not 1 <= hours <= 168 or not 1 <= limit <= 1000:
            raise ValueError('Use hours 1..168 and limit 1..1000')
        return {'ok': True, 'timeline': timeline(hours=hours, limit=limit)}
    if name == 'ytmusic':
        return {'ok': True, 'ytmusic': ytmusic.status()}
    if name == 'audio':
        return {'ok': True, 'audio_preset': config.audio_presets()}
    if name == 'hooks':
        db = store.connect()
        try:
            return {'ok': True, 'hooks': hooks.listing(db)}
        finally:
            db.close()
    result = sender('status', timeout=3)
    if result is not None and not result.get('ok'):
        return result
    status = result['status'] if result else {'state': 'STOPPED'}
    if not result:
        db=store.connect()
        try:
            off=[s for s,p in plugins.SOURCE_PLUGIN.items() if not plugins.on(cfg,p)]
            status['catchup']={'state':'idle','available':len(catchup.candidates(db,store.now(),off)),'error':'','hours':48}
        finally:
            db.close()
    if name == 'status':
        return {'ok': True, 'status': status}
    if name == 'queue':
        return {'ok': True, 'state': status['state'], 'upcoming': status.get('upcoming', []),
                'upcoming_tracks': status.get('upcoming_tracks', []), 'replay_queue': status.get('replay_queue', []),
                'next_reroll': status.get('next_reroll'), 'broadcast_plan': status.get('broadcast_plan')}
    return {'ok': True, 'status': status, 'timeline': timeline(), 'plugins': plugins.listing(cfg),
            'settings': settings.listing(cfg), 'ytmusic': ytmusic.status(),
            'audio_preset': config.audio_presets(), 'music_source_choice': settings.music_source(cfg),
            'hooks': read('hooks')['hooks'] if plugins.on(cfg, 'track_previews') else None}


def execute(body, *, sender=None, starter=None, port=8765):
    from .__main__ import send as native_send, start_daemon
    send, start_daemon = sender or native_send, starter or start_daemon
    cfg = config.load()
    def reply(data, code=200):
        return data, code
    if not isinstance(body, dict) or not isinstance(body.get('cmd'), str) or body['cmd'] not in OPERATIONS:
        return reply({'ok': False, 'error': 'unknown command'}, 400)
    cmd = body['cmd']
    if cmd == 'plugin' and type(body.get('on')) is not bool:
        return reply({'ok': False, 'error': 'Plugin on must be a boolean'}, 400)
    if cmd == "ytmusic_connect":
        try:
            with config.file_lock(config.DATA_DIR / 'ytmusic-connect.json'):
                result = ytmusic.connect(str(body.get("cookie", "")),
                                         str(body.get("playlist") or ytmusic.DEFAULT_PLAYLIST),
                                         browser=str(body.get("browser", "")))
        except ValueError as e:  # our own messages only: never yt-dlp output (cookies, account details)
            return reply({"ok": False, "error": str(e)}, 400)
        except OSError:
            return reply({"ok": False, "error": "Could not connect YouTube Music. Check the playlist; YouTube may be rate-limiting requests."}, 400)
        resp = {"ok": True, "ytmusic": result}
    elif cmd == "openrouter_login":
        resp = {"ok": True, "url": openrouter.login_url(f"http://127.0.0.1:{port}/openrouter/callback")}
    elif cmd == "chatgpt_login":  # OpenAI accepts only a 127.0.0.1 callback; the port may vary
        resp = {"ok": True, "url": chatgpt.login_url(f"http://127.0.0.1:{port}/auth/callback")}
    elif cmd == "voice_sample":  # one host line in the selected voice, played by the page
        live = config.load()
        settings.apply(live, settings.base(live))
        try:
            voice = tts.make_voice(live)
            if not voice.health():
                raise OSError
            voice.synthesize(VOICE_SAMPLE, config.RUN_DIR / "voice-sample.wav")
        except OSError:
            return reply({"ok": False, "error": "the voice engine did not answer"}, 400)
        except (ValueError, RuntimeError) as e:
            return reply({"ok": False, "error": str(e)}, 400)
        resp = {"ok": True, "url": f"/voice-sample.wav?{time.time_ns()}"}
    elif cmd == "llm_key":  # the key is stored, never echoed back
        try:
            editor.set_key(str(body.get("provider", "")), str(body.get("key", "")))
        except ValueError as e:
            return reply({"ok": False, "error": str(e)}, 400)
        resp = {"ok": True, "settings": settings.listing(cfg)}
    elif cmd == "chatgpt_logout":
        chatgpt.sign_out()
        resp = {"ok": True, "settings": settings.listing(cfg)}
    elif cmd == "openrouter_logout":
        openrouter.sign_out()
        resp = {"ok": True, "settings": settings.listing(cfg)}
    elif cmd == "play":
        resp = start_daemon()
    elif cmd == "plugin":  # a file switch; a running radio also takes back what the plugin already queued
        name, on = str(body.get("name", "")), bool(body.get("on"))
        if name not in plugins.PLUGINS:
            return reply({"ok": False, "error": f"unknown plugin {name}"}, 400)
        resp = send("plugin", timeout=10, name=name, on=on)
        if resp is None:  # the radio is off: the file alone
            plugins.set_plugin(name, on)
            resp = {"ok": True}
    elif cmd == "audio_preset":
        try:
            config.select_audio_preset(str(body.get("name", "")))
            cfg = config.load()
        except (ValueError, OSError) as e:
            return reply({"ok": False, "error": str(e)}, 400)
        resp = {"ok": True, "audio_preset": config.audio_presets()}
    elif cmd in ("reddit_community", "telegram_channel"):
        try:
            with config.file_lock(config.DATA_DIR / 'source-edits.json'):
                source, column, key, noun = (("reddit", "communities", "reddit_communities", "subreddit")
                    if cmd == "reddit_community" else ("telegram", "channels", "telegram_channels", "public channel"))
                names = public_news.references(body.get("value"), source, "names")
                if len(names) != 1 or body.get("action") not in ("add", "remove"):
                    raise ValueError(f"Add or remove one {noun} at a time.")
                name = names[0]
                live = config.load()
                settings.apply(live, settings.base(live))
                current = list(live["sources"][source][column])
                if body["action"] == "add" and name not in current:
                    if len(current) >= 20:
                        raise ValueError("Use up to 20 entries. Remove one before adding another.")
                    if source == "reddit":
                        reddit.validate([name], "names")
                    else:
                        try:
                            telegram.fetch(name)
                        except OSError:
                            raise ValueError("Could not read Telegram's public preview. Nothing was added; try again later.") from None
                    current.append(name)
                elif body["action"] == "remove" and name in current:
                    current.remove(name)
                settings.set_setting(key, current)
        except ValueError as e:
            return reply({"ok": False, "error": str(e)}, 400)
        resp = {"ok": True, "settings": settings.listing(cfg)}
    elif cmd in ("setting", "settings_reset"):  # a file too: the daemon reads it before every decision
        try:
            if cmd == "setting":
                key, value = str(body.get("key", "")), body.get("value")
                if key in ("reddit_communities", "reddit_posts"):
                    value = reddit.validate(value, settings.SOURCE_LISTS[key][1])
                settings.set_setting(key, value)
            else:
                defaults = settings.base(cfg)
                for key in ([str(body["key"])] if body.get("key") else ("reddit_communities", "reddit_posts")):
                    if key in ("reddit_communities", "reddit_posts"):
                        reddit.validate(defaults[key], settings.SOURCE_LISTS[key][1])
                settings.reset(str(body["key"]) if body.get("key") else None)
        except ValueError as e:
            return reply({"ok": False, "error": str(e)}, 400)
        resp = {"ok": True, "settings": settings.listing(cfg)}
    elif cmd == 'hook_review':
        db = store.connect()
        try:
            if type(body.get('approved')) is not bool:
                raise ValueError('Choose approve or reject')
            hooks.review(db,body.get('id'),body['approved'])
            resp = {'ok':True,'hooks':hooks.listing(db)}
        except (OSError,ValueError,TypeError) as error:
            return reply({'ok':False,'error':str(error)},400)
        finally:
            db.close()
    elif cmd == "reroll-next":
        resp = send(cmd, timeout=45, ref=str(body.get("ref", "")))
    elif cmd == "direction":
        resp = send(cmd, timeout=10, **{k: v for k, v in body.items() if k != "cmd"})
    elif cmd in ("replay", "unqueue"):
        resp = send(cmd, timeout=10, kind=str(body.get("kind", "")), ref=str(body.get("ref", "")))
    elif cmd == 'catchup':
        resp = send(cmd,timeout=10)
    elif cmd in COMMANDS:
        resp = send(cmd, timeout=45 if cmd == 'next' else 10)
    else:
        return reply({"error": "unknown command"}, 400)
    return reply(resp or {"ok": False, "error": "the radio is not answering"})
