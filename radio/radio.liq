# Personal Radio audio engine. Started by radio/engine.py; all paths come from env.
settings.log.stdout := false
settings.log.file := true
settings.log.file.path := environment.get("RADIO_LIQ_LOG")
settings.server.socket := true
settings.server.socket.path := environment.get("RADIO_LIQ_SOCK")

xfade = float_of_string(environment.get(default="3.0", "RADIO_XFADE"))
yt_failed = ref(0)
yt_offline = ref(false)
yt_count = int_of_string(environment.get(default="0", "RADIO_YTMUSIC_COUNT"))

# Download each song when requested; Liquidsoap removes its temporary file after playback.
def ytmusic(~rlog, ~maxtime, arg) =
  cmd = environment.get("RADIO_YTMUSIC_CMD")
  timeout = min(25., maxtime - time())
  if timeout <= 0. or cmd == "" then null else
    uri = string.trim(process.read(timeout=timeout, log_errors=false, "#{cmd} #{process.quote(arg)}"))
    if uri == "!unavailable" then
      ref.incr(yt_failed)
      if yt_failed() >= max(1, yt_count) then yt_offline := true end
      rlog("Skipping unavailable YouTube Music song")
      null
    elsif uri == "" or uri == "!offline" then
      yt_offline := true
      rlog("YouTube Music unavailable")
      null
    else
      yt_failed := 0
      yt_offline := false
      uri
    end
  end
end
protocol.add(temporary=true, "ytmusic", ytmusic)

# randomize = new Fisher-Yates permutation on every start and on every loop of the library.
music = playlist(id="music", prefetch=(if yt_count > 0 then 3 else 1 end), mode="randomize", reload_mode="never", environment.get("RADIO_M3U"))
local_playlist = environment.get(default="", "RADIO_FALLBACK_M3U")
music = if local_playlist != "" then
  local_music = playlist(id="local_music", mode="randomize", local_playlist)
  fallback(track_sensitive=true, [music, switch([({yt_offline()}, local_music)])])
else music end
news = request.queue(id="news")
# "New tracks" set: queued by the daemon after a news break. Below the news queue, so the next break (or a call
# sign) still comes at the next song boundary, above the shuffle.
fresh = request.queue(id="fresh")

# Events go to a file the daemon tails: stdout is block-buffered when piped.
events_path = environment.get("RADIO_EVENTS")
def emit_track(m) =
  file.write(append=true, data=json.stringify(compact=true, {kind=m["kind"], ref=m["ref"], filename=m["filename"], replay_id=m["replay_id"]}) ^ "\n", events_path)
end


# track_sensitive: a ready news package only starts at a song boundary, never mid-song.
radio = fallback(id="switch", track_sensitive=true, [news, fresh, music])
# Track events are taken before crossfade, which drops metadata on its transition tracks.
radio.on_track(synchronous=false, emit_track)
radio = amplify(1., radio)  # per-track gain from the liq_amplify annotation (measured LUFS)
# Into a news break the host talks over the song's ending: the break starts liq_talk_up s into the song's end
# window (the playlist sets it TALK_UP_SEC before the audible end; unmeasured songs: 1.5 s before the end).
def spoken(m) =
  m["kind"] == "news" or m["kind"] == "reminder" or m["kind"] == "weather"
end
def transition(a, b) =
  da = source.duration(a.source)
  d = da - source.duration(b.source)
  if spoken(b.metadata) then
    t = if a.metadata["liq_talk_up"] != "" then float_of_string(a.metadata["liq_talk_up"]) else max(0., da - 1.5) end
    in_md = list.add(("liq_fade_in_delay", "#{t}"), list.assoc.remove("liq_fade_in_delay", b.metadata))
    out_md = list.add(("liq_fade_out_delay", "#{t}"), list.assoc.remove("liq_fade_out_delay", a.metadata))
    cross.simple(fade_in=xfade, fade_out=xfade, initial_fade_in_metadata=in_md, initial_fade_out_metadata=out_md,
                 a.source, b.source)
  else
    # The next track starts where it always did, xfade + 1 s before the end, whatever both windows are: a long
    # start window (a song the host can talk over) would otherwise start it earlier. After a talkover break the
    # song rises under the voice for the whole overlap.
    start = max(0., da - (xfade + 1.))
    lead_in = if spoken(a.metadata) then 0. else max(0., start - max(0., d)) end
    in_md = list.add(("liq_fade_in_delay", "#{lead_in}"), list.assoc.remove("liq_fade_in_delay", b.metadata))
    in_md = if spoken(a.metadata) and da > xfade then
      list.add(("liq_fade_in", "#{da}"), list.assoc.remove("liq_fade_in", in_md)) else in_md end
    out_md = if start > 0. and a.metadata["liq_fade_out_delay"] == "" then
      list.add(("liq_fade_out_delay", "#{start}"), a.metadata) else a.metadata end
    cross.simple(fade_in=xfade, fade_out=xfade, initial_fade_in_metadata=in_md, initial_fade_out_metadata=out_md,
                 a.source, b.source)
  end
end
radio = cross(duration=xfade + 1., transition, radio)
radio = limit(threshold=-1.5, radio)  # gentle; the ffmpeg sink's alimiter is the hard ceiling

# Pause = the output keeps running on silence; the radio source is not pulled meanwhile, so it stays in place.
# (Stopping the output would end the ffmpeg sink process and restart the recording file.)
paused = ref(false)
program = radio
radio = switch(track_sensitive=false, [({paused()}, blank()), ({true}, radio)])
server.register("pause", fun (_) -> begin paused := true; "OK" end)
server.register("resume", fun (_) -> begin paused := false; "OK" end)

out_mode = environment.get(default="device", "RADIO_OUTPUT")
if out_mode == "device" then
  # Raw PCM is piped to an ffmpeg sink (built by engine.py): brick-wall alimiter, then the macOS audio device,
  # plus the recording file for `radio play --record`, so the recording is exactly what was heard.
  out = output.external(id="out", fallible=true,
    %ffmpeg(format="f32le", %audio(codec="pcm_f32le", samplerate=44100, channels=2)),
    environment.get("RADIO_SINK_CMD"), radio)
else
  # Test mode: render the broadcast to a wav file. Fast by default (unit tests); RADIO_FILE_SYNC=realtime for
  # long-run checks, since the unsynced clock skips through tracks over long runs.
  if environment.get(default="fast", "RADIO_FILE_SYNC") != "realtime" then
    clock.assign_new(sync="none", [radio])
  end
  out = output.file(id="out", fallible=true, %wav, out_mode, radio)
end

server.register("skip", fun (_) -> begin program.skip(); "OK" end)
