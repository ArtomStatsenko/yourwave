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

OPENERS = [
    "{time}. Вы слушаете {station}.",
    "На часах {time}, это {station}.",
    "{station}, {time}. Несколько слов между треками.",
    "{time}, с вами {station}. Коротко о главном.",
]


def _rotate(templates: list[str], role: str, recent: dict, seed: str) -> str:
    # Same no-repeat rotation as the jingles; templates stand in for files.
    return audio.choose_jingles({role: templates}, seed, recent)[role]


def _quoted(station: str) -> str:
    # The voice reads a Latin name in «quotes» as an English name; without them it sounds like spelling.
    return f"«{station}»" if station.isascii() else station


def startup(station: str) -> str:
    """Said at the start while YouTube Music loads the first song (pre-rendered: the voice may still be starting)."""
    return f"В эфире {_quoted(station)}. Первая песня уже на подходе — YouTube Music её догружает, а мы пока здороваемся."


def opener(air_time: datetime, station: str, recent: dict, seed: str) -> str:
    text = _rotate(OPENERS, "host_opener", recent, seed).format(time=time_words(air_time), station=_quoted(station))
    return text[0].upper() + text[1:]


# ---- music sets: back-sell what just played, forward-sell the set that starts after the break ----
# Live, not a template read every time: each break picks one shape (stable per break), from saying nothing
# to naming the last song.
# A set is named as a set: "Вы слушали X и Y" sounded like one song by both artists.
BACK_SELLS = ["Это был музыкальный блок: {names}.", "Вы слушали подборку: {names}.", "В этом блоке звучали песни {names}."]
LAST_SONG = ["Вы слушали {artist} — «{title}».", "Это был трек «{title}», исполнитель — {artist}.",
             "Прозвучала песня «{title}» в исполнении {artist}."]  # no verb that agrees with the artist's gender
SET_OPENERS = [
    "Дальше — {minutes} музыки без перерыва.",
    "А теперь — {minutes} музыки нон-стоп.",
    "Впереди {minutes} музыки без перерыва, оставайтесь с нами.",
    "Возвращаемся к музыке.",
    "А теперь снова музыка.",
    "Отлично! Впереди {minutes} музыки без перерыва.",
    "[laughter] Ну что, возвращаемся к музыке.",  # VoiceStudio tag: a short, natural chuckle
]
NEW_SET_OPENERS = [
    "А теперь — новинки нашей библиотеки: {minutes} музыки, которой ещё не было в эфире.",
    "Новинки! Впереди {minutes} свежей музыки без перерыва.",
    "А теперь только новое: {minutes} музыки, которую вы ещё не слышали.",
]
FIRST_SONG = [" Начинаем с {artist}, «{title}».", " Первой прозвучит «{title}», {artist}.", " Открывает сет {artist}."]

# Short, as a DJ says it between songs: no "playlist" or "selection" talk.
# [laughter] / [surprise-ah] are VoiceStudio tags (a chuckle, an "А,"); [sigh] turns the clip into noise: never use it.
MUSIC_LINKS = ["А мы продолжаем.", "Продолжаем.", "Дальше — снова музыка.", "Не переключайтесь.",
               "[laughter] А мы продолжаем!"]
FACT_LEADS = ["", "Кстати, ", "[surprise-ah] Кстати, "]
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
    text = _rotate(BACK_SELLS, "host_back", recent, seed).format(names=", ".join(_quoted(n) for n in seen))
    return text + (f" Последней прозвучала «{_title(songs[-1][1])}»." if shape == 2 else "")


def set_opener(minutes: int | None, first: tuple[str, str] | None, recent: dict, seed: str, novelty: bool = False) -> str:
    """The set announcement, sometimes with the first song (not always: then it sounds read off a card)."""
    if minutes is None:
        choices = (["А теперь — новинки нашей библиотеки.", "Дальше — новая музыка."] if novelty
                   else [line for line in SET_OPENERS if '{minutes}' not in line])
        text = _rotate(choices, 'host_new' if novelty else 'host_set', recent, seed)
    else:
        text = (_rotate(NEW_SET_OPENERS, "host_new", recent, seed) if novelty
                else _rotate(SET_OPENERS, "host_set", recent, seed)).format(minutes=minutes_words(minutes))
    if first and _shape(seed, "first", [1, 2]):  # 2 of 3 breaks name it
        text += _rotate(FIRST_SONG, "host_first", recent, seed).format(artist=_quoted(first[0]), title=_title(first[1]))
    return text
