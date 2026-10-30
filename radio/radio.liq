# Yourwave audio engine. Started by radio/engine.py; all paths come from env.
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

# Replace a queued request without skipping the currently playing source.
# hold(f): run f while the playlist hands out no next file (its prefetch would slip one in among a new queue).
def replace_music(~hold=fun (f) -> f(), get_queue, set_queue, arg) =
  let json.parse (params : {expected: string, uri: string, rid: int, head: bool}) = arg
  def matches(r) =
    m = request.metadata(r)
    if params.rid < 0 then m["ref"] == params.expected else
      request.id(r) == params.rid and (m["ref"] == "" or m["ref"] == params.expected)
    end
  end
  q = get_queue()
  if not list.exists(matches, q) then "STALE" else
    replacement = request.create(params.uri)
    if not request.resolve(timeout=25., replacement) then
      request.destroy(replacement)
      "UNAVAILABLE"
    else
      q = get_queue()
      found = list.filter(matches, q)
      if list.is_empty(found) or (params.head and not matches(list.hd(q))) then
        request.destroy(replacement)
        "STALE"
      else
        old = list.hd(found)
        # playlist.set_queue destroys old requests; resolve copies before clearing so prefetch cannot jump ahead.
        # shortcut: reroll downloads prefetched remote tracks again; reuse their files if this becomes too slow.
        prepared = list.map(fun (r) -> if request.id(r) == request.id(old) then replacement
          elsif params.rid < 0 then request.create("annotate:plan_clone=\"1\":#{request.uri(r)}") else r end, q)
        resolved = if params.rid < 0 then list.for_all(fun (r) -> request.resolve(timeout=25., r), prepared) else true end
        unchanged = list.map(request.id, q) == list.map(request.id, get_queue())
        if not resolved or not unchanged then
          list.iter(fun (r) -> begin
            if params.rid < 0 or request.id(r) == request.id(replacement) then request.destroy(r) end
          end, prepared)
          if resolved then "STALE" else "UNAVAILABLE" end
        else
          # set_queue returns before it has added the requests one by one: wait for them, holding the playlist's next
          # file back until then, or its prefetch lands among them.
          ids = list.map(request.id, prepared)
          def rec settle(n) =
            if n > 0 and not list.for_all(fun (i) -> list.mem(i, list.map(request.id, get_queue())), ids) then
              thread.pause(.02)
              settle(n - 1)
            end
          end
          hold(fun () -> begin set_queue(prepared); settle(100) end)
          request.destroy(old)
          "#{request.id(replacement)}"
        end
      end
    end
  end
end

# The next songs in air order: the queued requests, then the rest of this shuffle round (refs, one per line).
def upcoming_music_refs(playlist_source) =
  def ref_of(r) =
    m = request.metadata(r)
    m["ref"]
  end
  def ref_in(uri) =
    m = r/ref="([^"]*)"/.exec(uri)
    m[1]
  end
  refs = list.append(list.map(ref_of, playlist_source.queue()), list.map(ref_in, playlist_source.remaining_files()))
  string.concat(separator="\n", list.prefix(12, list.filter(fun (x) -> x != "", refs)))
end

def queued_music_ref(playlist_source) =
  q = playlist_source.queue()
  if list.is_empty(q) or request.status(list.hd(q)) != "ready" then "" else
    m = request.metadata(list.hd(q))
    m["ref"]
  end
end

# Reorder only the unrequested files; keep currently playing and prefetched requests intact.
shuffle_enabled = ref(environment.get(default="1", "RADIO_SHUFFLE") == "1")
def controlled_playlist(~id, ~prefetch=1, uri) =
  def load(uri) = list.map(fun (item) -> "annotate:plan_queue=\"#{id}\":#{item}", playlist.files(uri)) end
  files = ref(load(uri))
  def ordered(p) = if shuffle_enabled() then list.shuffle(p) else p end end
  loop_reload = ref(fun () -> ())
  loaded = ref(false)
  holding = ref(false)
  s = playlist.list(id=id, prefetch=prefetch, mode="normal",
    on_loop=fun () -> begin f = loop_reload(); f() end, [])
  s.on_wake_up(synchronous=false, memoize(fun () -> begin
    s.reload(ordered(files()))
    loaded := true
  end))
  loop_reload := {if loaded() and not holding() then s.reload(empty_queue=false, ordered(files())) end}
  # f() while the round is empty, so the prefetch finds no next file; then the round as it was
  def hold(f) =
    holding := true
    rest = s.remaining_files()
    s.reload(empty_queue=false, [])
    f()
    s.reload(empty_queue=false, rest)
    holding := false
  end
  def reorder() =
    remaining = s.remaining_files()
    pending = if shuffle_enabled() then list.shuffle(remaining) else
      list.filter(fun (f) -> list.mem(f, remaining), files())
    end
    s.reload(empty_queue=false, pending)
  end
  def reserve() =
    q = s.queue()
    remaining = s.remaining_files()
    if loaded() and list.length(q) < 2 and not list.is_empty(remaining) then
      r = request.create(list.hd(remaining))
      if not string.contains(substring="ytmusic:", request.uri(r)) and request.resolve(timeout=3., r) and
         list.map(request.id, q) == list.map(request.id, s.queue()) and remaining == s.remaining_files() then
        if s.add(r) then s.reload(empty_queue=false, list.tl(remaining)) else request.destroy(r) end
      else request.destroy(r) end
    end
  end
  server.register("#{id}.reload", fun (arg) -> begin
    files := load(if string.trim(arg) == "" then uri else string.trim(arg) end)
    s.reload(ordered(files()))
    "OK"
  end)
  s.{reorder=reorder,reserve=reserve,hold=hold}
end
music = controlled_playlist(id="music", prefetch=(if yt_count > 0 then 3 else 1 end), environment.get("RADIO_M3U"))
local_reorder = ref(fun () -> ())
server.register("music.shuffle", fun (arg) -> begin
  if arg == "1" or arg == "0" then
    shuffle_enabled := arg == "1"
    music.reorder()
    f = local_reorder(); f()
    "OK"
  else "ERROR" end
end)
server.register("music.replace", fun (arg) -> replace_music(hold=music.hold, music.queue, music.set_queue, arg))
primary_music = music
active_playlist = ref(fun () -> primary_music)
local_playlist = environment.get(default="", "RADIO_FALLBACK_M3U")
music = if local_playlist != "" then
  local_music = controlled_playlist(id="local_music", local_playlist)
  local_reorder := local_music.reorder
  server.register("local_music.replace", fun (arg) -> replace_music(hold=local_music.hold, local_music.queue, local_music.set_queue, arg))
  selected = ref(environment.get(default="ytmusic", "RADIO_MUSIC_SOURCE"))
  server.register("music.source", fun (arg) -> begin
    if arg == "local" or arg == "ytmusic" then selected := arg; "OK" else "ERROR" end
  end)
  def selected_music() = if selected() == "local" or yt_offline() then local_music else primary_music end end
  active_playlist := selected_music
  server.register("music.source_ready", fun (_) -> begin
    ready = if selected() == "local" then local_music.is_ready() else primary_music.is_ready() end
    "#{ready}"
  end)
  server.register("music.peek", fun (_) -> queued_music_ref(selected_music()))
  server.register("music.upcoming", fun (_) -> upcoming_music_refs(selected_music()))
  # Keep the current song until its boundary; local music covers remote loading without silence.
  switch(track_sensitive=true, [({selected() == "local"}, local_music),
    ({selected() == "ytmusic"}, fallback(track_sensitive=true, [primary_music,
      switch([({yt_offline() or not primary_music.is_ready()}, local_music)])]))])
else
  server.register("music.peek", fun (_) -> queued_music_ref(primary_music))
  server.register("music.upcoming", fun (_) -> upcoming_music_refs(primary_music))
  music
end
news = request.queue(id="news")
# Unplayed songs of the active music source take priority over shuffle. News and manual replays still come
# first at a song boundary; this queue does not depend on a news break.
fresh = request.queue(id="fresh")
previews_enabled = ref(false)
cancelled_previews = ref([])
aired_requests = ref([])
last_track_kind = ref("")
last_track_ref = ref("")
def future_requests(s) =
  current = s.current()
  # Resolving requests are temporarily outside the source queue, but retain their original queue tag.
  resolving = list.filter(fun (r) -> request.status(r) == "resolving" and
    not string.contains(substring="plan_clone=\"1\"", request.uri(r)) and
    string.contains(substring="plan_queue=\"#{s.id()}\"", request.uri(r)), request.all())
  q = list.append(resolving, s.queue())
  if null.defined(current) then
    r = null.get(current)
    m = request.metadata(r)
    if not list.mem(request.id(r),aired_requests()) and list.mem(request.status(r), ["ready", "playing"]) then [r, ...q] else q end
  else q end
end
server.register("music.plan", fun (_) -> begin
  get = active_playlist()
  source = get()
  def rows(s, is_set) =
    list.map(fun (r) -> begin
      m = request.metadata(r)
      {ref=m["ref"], uri=request.uri(r), kind=m["kind"], set=is_set,
       duration=float_of_string(default=0., m["duration"])}
    end, future_requests(s))
  end
  tail = list.map(fun (uri) -> {ref="", uri=uri, kind="music", set=false, duration=0.}, source.remaining_files())
  json.stringify(compact=true, list.append(rows(news,false),
    list.append(rows(fresh,true),list.append(rows(source,false),tail))))
end)
def preview_requests() =
  get = active_playlist()
  list.prefix(2, list.append(future_requests(fresh), future_requests(get())))
end
def preview_refs() =
  q = preview_requests()
  if list.length(q) != 2 or not list.for_all(fun (r) -> list.mem(request.status(r), ["ready", "playing"]) and file.exists(request.filename(r)), q) then [] else
    list.map(fun (r) -> begin m = request.metadata(r); m["ref"] end, q)
  end
end
def preview_files() =
  if list.length(preview_refs()) != 2 then [] else
    list.map(fun (r) -> begin
      m = request.metadata(r)
      path = request.filename(r)
      {ref=m["ref"], path=path, modified=int_of_float(file.mtime(path) * 1000000.), size=file.size(path)}
    end, preview_requests())
  end
end
def preview_snapshot() = json.stringify(compact=true, preview_files()) end
server.register("previews.enable", fun (arg) -> begin previews_enabled := arg == "1"; "OK" end)
server.register("previews.cancel", fun (arg) -> begin
  cancelled_previews := [arg, ...list.prefix(31, cancelled_previews())]
  list.iter(fun (r) -> begin
    m = request.metadata(r)
    if m["kind"] == "teaser" and m["ref"] == arg then news.remove_request_id(request.id(r)) end
  end, news.queue())
  current = news.current()
  if null.defined(current) then
    m = request.metadata(null.get(current))
    if m["kind"] == "teaser" and m["ref"] == arg then news.skip() end
  end
  "OK"
end)
server.register("previews.next", fun (_) -> preview_snapshot())
server.register("previews.prepare", fun (_) -> begin
  list.iter(fun (r) -> begin
    if request.status(r) == "idle" and not string.contains(substring="ytmusic:", request.uri(r)) then ignore(request.resolve(timeout=3., r)) end
  end, list.prefix(2, fresh.queue()))
  if list.length(fresh.queue()) < 2 then get = active_playlist(); get().reserve() end
  preview_snapshot()
end)
server.register("previews.push", fun (arg) -> begin
  let json.parse (p : {refs: [string], files: [{ref:string,path:string,modified:int,size:int}], uri: string}) = arg
  if previews_enabled() and last_track_kind() == "news" and p.refs == preview_refs() and p.files == preview_files() and list.length(p.refs) == 2 and list.is_empty(news.queue()) then
    r = request.create(p.uri)
    resolved = request.resolve(timeout=3., r)
    m = request.metadata(r)
    if resolved and last_track_kind() == "news" and m["preview_after"] == last_track_ref() and
       p.refs == preview_refs() and p.files == preview_files() and not list.mem(m["ref"], cancelled_previews()) then news.push(r); "#{request.id(r)}"
    else request.destroy(r); "STALE" end
  else "STALE" end
end)
server.register("news.replace", fun (arg) -> begin
  result = replace_music(news.queue, news.set_queue, arg)
  news.fetch()
  result
end)
server.register("fresh.replace", fun (arg) -> begin
  result = replace_music(fresh.queue, fresh.set_queue, arg)
  fresh.fetch()
  result
end)
server.register("fresh.ready", fun (_) -> "#{fresh.is_ready()}")
server.register("news.ready", fun (arg) -> begin
  if arg == "" then "#{news.is_ready()}" else
    q = future_requests(news)
    ready = if list.is_empty(q) then false else
      r = list.hd(q)
      request.id(r) == int_of_string(default=-1, arg) and list.mem(request.status(r), ["ready", "playing"])
    end
    "#{news.is_ready() and ready}"
  end
end)
def pending_requests(source) =
  current = source.current()
  q = if null.defined(current) then [null.get(current), ...source.queue()] else source.queue() end
  # Resolving requests are temporarily outside their queue; keep their ids until loading finishes.
  resolving = list.filter(fun (r) -> request.status(r) == "resolving", request.all())
  q = list.append(q, resolving)
  string.concat(separator=" ", list.map(fun (r) -> "#{request.id(r)}", q))
end
server.register("news.pending", fun (_) -> pending_requests(news))
server.register("fresh.pending", fun (_) -> pending_requests(fresh))

# Events go to a file the daemon tails: stdout is block-buffered when piped.
events_path = environment.get("RADIO_EVENTS")
def emit_track(m) =
  last_track_kind := m["kind"]
  last_track_ref := m["ref"]
  rid = int_of_string(default=-1,m["rid"])
  if rid >= 0 then aired_requests := [rid, ...list.prefix(127,aired_requests())] end
  file.write(append=true, data=json.stringify(compact=true, {kind=m["kind"], ref=m["ref"], filename=m["filename"], replay_id=m["replay_id"]}) ^ "\n", events_path)
end


# track_sensitive: a ready news package only starts at a song boundary, never mid-song.
# Reject an expired insert at the handoff even if the Python scheduler is unavailable.
def news_eligible() =
  current = news.current()
  q = if null.defined(current) then [null.get(current)] else news.queue() end
  if list.is_empty(q) then false else
    m = request.metadata(list.hd(q))
    until = float_of_string(default=0., m["air_until"])
    valid = if m["kind"] == "teaser" then
      let json.parse (expected : [string]) = m["preview_refs"]
      let json.parse (files : [{ref:string,path:string,modified:int,size:int}]) = m["preview_files"]
      previews_enabled() and last_track_kind() == "news" and m["preview_after"] == last_track_ref() and
      not list.mem(m["ref"], cancelled_previews()) and expected == preview_refs() and files == preview_files()
    else true end
    valid and (until == 0. or time() < until)
  end
end
eligible_news = switch(track_sensitive=true, [({news_eligible()}, news)])
radio = fallback(id="switch", track_sensitive=true, [eligible_news, fresh, music])
# Track events are taken before crossfade, which drops metadata on its transition tracks.
radio.on_track(synchronous=false, emit_track)
radio = amplify(1., radio)  # per-track gain from the liq_amplify annotation (measured LUFS)
# Into a news break the host talks over the song's ending: the break starts liq_talk_up s into the song's end
# window (the playlist sets it TALK_UP_SEC before the audible end; unmeasured songs: 1.5 s before the end).
def spoken(m) =
  m["kind"] == "news" or m["kind"] == "reminder" or m["kind"] == "weather"
end
manual_skip = ref(false)
def transition(a, b) =
  if manual_skip() then
    manual_skip := false
    ending = max_duration(0.1, a.source)
    cross.simple(fade_in=0.1, fade_out=0.1,
                 initial_fade_in_metadata=[("liq_fade_in", "0.1"), ("liq_fade_in_delay", "0.")],
                 initial_fade_out_metadata=[("liq_fade_out", "0.1"), ("liq_fade_out_delay", "0.")], ending, b.source)
  elsif b.metadata["kind"] == "teaser" then
    # The set preview continues the edition; finish the host's words before its hook.
    t = max(0., source.duration(a.source) - 0.1)
    cross.simple(fade_in=0.03, fade_out=0.1,
      initial_fade_in_metadata=[("liq_fade_in", "0.03"), ("liq_fade_in_delay", "#{t}")],
      initial_fade_out_metadata=[("liq_fade_out", "0.1"), ("liq_fade_out_delay", "#{t}")], a.source, b.source)
  else
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
end
radio = cross(duration=xfade + 1., transition, radio)
# shortcut: conservative silence alarm; calibrate on station recordings before automatic recovery.
radio = blank.detect(threshold=-55., max_blank=20., min_noise=1., track_sensitive=false, radio)
def audio_health(state) =
  file.write(append=true, data=json.stringify(compact=true, {kind="health", ref=state}) ^ "\n", events_path)
end
radio.on_blank(synchronous=false, {audio_health("silence")})
radio.on_noise(synchronous=false, {audio_health("audible")})
radio = limit(threshold=-1.5, radio)  # gentle; the ffmpeg sink's alimiter is the hard ceiling

# Pause = the output keeps running on silence; the radio source is not pulled meanwhile, so it stays in place.
# (Stopping the output would end the ffmpeg sink process and restart the recording file.)
paused = ref(environment.get(default="", "RADIO_START_PAUSED") == "1")  # a resumed song is queued first
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
  clock.assign_new(sync=(if environment.get(default="fast", "RADIO_FILE_SYNC") == "realtime" then "cpu" else "none" end), [radio])
  out = output.file(id="out", fallible=true, %wav, out_mode, radio)
end

server.register("skip", fun (_) -> begin manual_skip := true; program.skip(); "OK" end)
