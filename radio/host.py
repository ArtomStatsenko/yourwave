"""Host links of a news break: back-sell, an opener with a time check, and the set opener after the news. Fixed templates, no LLM.
Each phrase is a separate TTS clip, so the closers (and openers within a minute) come from the cache."""
import random
import re
from datetime import datetime

from . import audio

_ONES = ["ноль", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять", "десять",
         "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", "пятнадцать", "шестнадцать", "семнадцать",
         "восемнадцать", "девятнадцать"]
_TENS = {2: "двадцать", 3: "тридцать", 4: "сорок", 5: "пятьдесят"}


def _num(n: int) -> str:
    if n < 20:
        return _ONES[n]
    t, u = divmod(n, 10)
    return _TENS[t] + (f" {_ONES[u]}" if u else "")


def time_words(dt: datetime) -> str:
    """23:05 -> "двадцать три ноль пять", 9:00 -> "девять ноль-ноль", 14:30 -> "четырнадцать тридцать"."""
    m = dt.minute
    minutes = "ноль-ноль" if m == 0 else (f"ноль {_ONES[m]}" if m < 10 else _num(m))
    return f"{_num(dt.hour)} {minutes}"


# Sections of a news break by package id prefix: (jingles key of its intro, the short phrase said when the section
# follows another one). The intro jingle opens the break instead of the phrase.
SECTIONS = {"github": ("github_intro", "GitHub."), "horizon": ("horizon_intro", "Новости технологий.")}

# Lines as a person says them (docs/host-phrases.md): no run-up before the point, no slogans, no announcer words.
OPENERS = [
    "{time}, {station}.",
    "На часах {time}. Это {station}.",
    "{station}, время {time}.",
    "{time}. Немного новостей, и снова музыка.",
    "Уже {time}. Прервёмся ненадолго.",
]
STARTUPS = [
    "Привет, это {station}. Первая песня почти загрузилась.",
    "{station} в эфире. Секунду, первый трек догружается.",
    "Это {station}. Пока грузится первая песня, просто здороваюсь.",
]


def _rotate(templates: list[str], role: str, recent: dict, seed: str) -> str:
    # Same no-repeat rotation as the jingles; templates stand in for files.
    return audio.choose_jingles({role: templates}, seed, recent)[role]


def _quoted(station: str) -> str:
    # The voice reads a Latin name in «quotes» as an English name; without them it sounds like spelling.
    return f"«{station}»" if station.isascii() else station


def startup(station: str) -> str:
    """Said at the start while YouTube Music loads the first song (pre-rendered: the voice may still be starting)."""
    # one a day: the opener is rendered once and named by its text, so every call must give the same one
    return STARTUPS[datetime.now().toordinal() % len(STARTUPS)].format(station=_quoted(station))


def opener(air_time: datetime, station: str, recent: dict, seed: str) -> str:
    text = _rotate(OPENERS, "host_opener", recent, seed).format(time=time_words(air_time), station=_quoted(station))
    return text[0].upper() + text[1:]


# ---- music sets: back-sell what just played, forward-sell the set that starts after the break ----
# Live, not a template read every time: each break picks one shape (stable per break), from saying nothing
# to naming the last song.
# Several artists are a list ("играли X, Y и Z" sounded like one song by both, so commas only).
# No verb agrees with an artist or a title (their gender is unknown) and names stay in the nominative.
BACK_SELLS = ["У нас звучали {names}.", "{names}: вот кто был в эфире последние минуты.", "Перед новостями играли {names}."]
BACK_SELLS_LAST = ["Чуть раньше играли {names}, а только что «{title}».", "Вы слушали {names}. А закончили мы на «{title}»."]
LAST_SONG = ["Это {artist}, «{title}».", "Вы слушали «{title}», {artist}.", "Это {artist} с песней «{title}».",
             "{artist}, «{title}» — вот что сейчас играло.", "Только что звучала песня «{title}». Это {artist}."]
SET_OPENERS = [
    "И снова музыка.",
    "Всё, возвращаемся к музыке.",
    "На этом всё, дальше музыка.",
    "Новостей пока хватит.",
    "[laughter] Ну всё, хватит разговоров.",  # VoiceStudio tag: a short, natural chuckle
]
FIRST_SONG = [" Дальше {artist}, «{title}».", " Сейчас будет {artist}.", " Следующий трек «{title}», {artist}.",
              " А сейчас {artist} с песней «{title}»."]
NEW_FIRST_SONG = [" Дальше кое-что новое: {artist}, «{title}».", " Этого трека в эфире ещё не было: «{title}», {artist}.",
                  " Свежее в фонотеке: {artist}."]

# Short, as a DJ says it between songs: no "playlist" or "selection" talk.
# [laughter] / [surprise-ah] are VoiceStudio tags (a chuckle, an "А,"); [sigh] turns the clip into noise: never use it.
MUSIC_LINKS = ["Едем дальше.", "Слушаем дальше.", "Дальше ещё музыка.", "И следующая.",
               "[laughter] Так, что там у нас дальше."]
FACT_LEADS = ["", "Кстати, ", "[surprise-ah] Кстати, ", "Между прочим, "]
_COMMON_STARTS = {"этот", "эта", "это", "эту", "этой", "песня", "песню", "трек", "альбом", "клип", "впервые", "в", "на",
                  "по", "за", "изначально", "когда", "у"}


def music_link(song: tuple[str, str] | None, facts: str, recent: dict, seed: str) -> str:
    """A back-sell of the song that just played (the same phrases as before a break), an optional fact, a closer."""
    lead = ""
    if song:
        artist, title = song
        lead = _rotate(LAST_SONG, "host_last", recent, seed).format(artist=_quoted(artist), title=_title(title)) + " "
    if facts:
        pre = _rotate(FACT_LEADS, "fact_lead", recent, seed)
        first = facts.split(" ", 1)[0]
        if pre and first.lower() in _COMMON_STARTS:  # "Кстати, этот трек…", but never lower a name
            facts = facts[:1].lower() + facts[1:]
        lead += (pre + facts if pre and first.lower() in _COMMON_STARTS else facts) + " "
    return lead + _rotate(MUSIC_LINKS, "music_link", recent, seed)


def minutes_words(n: int) -> str:
    """20 -> "двадцать минут", 21 -> "двадцать одну минуту", 23 -> "двадцать три минуты" (accusative)."""
    word = "минуту" if n % 10 == 1 and n % 100 != 11 else "минуты" if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else "минут"
    t, u = divmod(n, 10)
    last = {1: "одну", 2: "две"}.get(u, _ONES[u]) if not 10 <= n % 100 < 20 else ""
    num = _num(n) if 10 <= n % 100 < 20 or not u else (f"{_TENS[t]} {last}" if t >= 2 else last)
    return f"{num} {word}"


def span_words(n: int) -> str:
    """Like minutes_words, also past an hour: 60 -> "час", 125 -> "два часа пять минут" (up to a day)."""
    hours, rest = divmod(n, 60)
    if not hours:
        return minutes_words(n)
    word = "час" if hours % 10 == 1 and hours != 11 else "часа" if hours % 10 in (2, 3, 4) and hours not in (12, 13, 14) else "часов"
    head = word if hours == 1 else f"{_num(hours)} {word}"
    return f"{head} {minutes_words(rest)}" if rest else head


def _title(title: str) -> str:
    """As said on air: without "(feat. X)", "[Official Video]" and the like."""
    return re.sub(r"\s*[(\[](feat|ft|prod|official|lyric|audio|video)[^)\]]*[)\]]", "", title, flags=re.I).strip()


def _shape(seed: str, role: str, weights: list[int]) -> int:
    return random.Random(f"{role}|{seed}").choices(range(len(weights)), weights)[0]


def back_sell(songs: list[tuple[str, str]], recent: dict, seed: str) -> str | None:
    """No back-sell, the last song with its title, or the last three artists (the last one with its title)."""
    shape = _shape(seed, "back", [1, 2, 2, 1]) if songs else 0
    if shape == 0:
        return None
    if shape == 1:
        a, t = songs[-1]
        return _rotate(LAST_SONG, "host_last", recent, seed).format(artist=_quoted(a), title=_title(t))
    seen = []
    for a, _ in reversed(songs):
        if a not in seen:
            seen.insert(0, a)
        if len(seen) == 3:
            break
    if len(seen) < 2:  # one artist all set long: name the last song, not "a block of X"
        a, t = songs[-1]
        return _rotate(LAST_SONG, "host_last", recent, seed).format(artist=_quoted(a), title=_title(t))
    names = ", ".join(_quoted(n) for n in seen)
    if shape == 2:
        return _rotate(BACK_SELLS_LAST, "host_back_last", recent, seed).format(names=names, title=_title(songs[-1][1]))
    return _rotate(BACK_SELLS, "host_back", recent, seed).format(names=names)


def set_opener(first: tuple[str, str] | None, recent: dict, seed: str, novelty: bool = False) -> str:
    """Back to the music, sometimes with the next song (not always: then it sounds read off a card); a song new to
    the station is said to be new."""
    text = _rotate(SET_OPENERS, "host_set", recent, seed)
    if first and (novelty or _shape(seed, "first", [1, 2])):  # 2 of 3 breaks name it; a new one always
        lines, role = (NEW_FIRST_SONG, "host_new") if novelty else (FIRST_SONG, "host_first")
        text += _rotate(lines, role, recent, seed).format(artist=_quoted(first[0]), title=_title(first[1]))
    elif novelty:
        text += " Дальше кое-что новое."
    return text
