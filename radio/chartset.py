"""The chart set on air: the week's top songs by thumbs up, counted down from the last place, introduced by the host.
Its settings live in DATA_DIR/chartset.json; a set airs as a plan block (kind 'chart', Air direction)."""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import charts, config, music

SIZES = (3, 5, 10)
DAYS = ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')
DEFAULTS = {'size': 5, 'weekly': False, 'day': 'fri', 'time': '18:00'}
WEEKDAYS_RU = ('понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота', 'воскресенье')
WEEKDAYS_EN = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')


def _path():
    return config.DATA_DIR / 'chartset.json'


def load() -> dict:
    try:
        saved = json.loads(_path().read_text(encoding='utf-8'))
        return validate({**DEFAULTS, **(saved if isinstance(saved, dict) else {})})
    except (FileNotFoundError, ValueError, TypeError):
        return dict(DEFAULTS)


def validate(value: dict) -> dict:
    size, day, time = value.get('size'), value.get('day'), value.get('time')
    if size not in SIZES:
        raise ValueError('Choose Top 3, 5 or 10')
    if day not in DAYS:
        raise ValueError('Choose a day of the week')
    if not isinstance(time, str) or len(time) != 5 or time[2] != ':' or not (time[:2] + time[3:]).isdigit() \
            or int(time[:2]) > 23 or int(time[3:]) > 59:
        raise ValueError('Choose a time as HH:MM')
    if type(value.get('weekly')) is not bool:
        raise ValueError('Every week is on or off')
    return {'size': size, 'weekly': value['weekly'], 'day': day, 'time': time}


def save(value: dict) -> dict:
    checked = validate({**load(), **value})
    config.write_json(_path(), checked)
    return checked


def next_weekly(settings: dict, at: datetime, timezone: str) -> datetime | None:
    """The next weekly set at or after `at` (in the station's time zone), or None when Every week is off."""
    if not settings['weekly']:
        return None
    local = at.astimezone(ZoneInfo(timezone))
    hour, minute = int(settings['time'][:2]), int(settings['time'][3:])
    day = (DAYS.index(settings['day']) - local.weekday()) % 7
    when = (local + timedelta(days=day)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return when if when >= local else when + timedelta(days=7)


def countdown(db, size: int, at: datetime) -> list[dict]:
    """The week's top `size` songs from the last place to the first: [{place, ref, title}]."""
    top = charts.ranking(db, 7, at)[:size]
    return [{'place': r['place'], 'ref': r['ref'], 'title': music.label(db, r['ref'])} for r in reversed(top)]


def title(size: int) -> str:
    return f'Top {size} of the week'


def template(station: str, size: int, weekday: int, songs: list[dict], language: str) -> dict:
    """The host's words when the script provider is off or answers badly: an introduction and a line per song."""
    if language.startswith('en'):
        intro = f"It's {WEEKDAYS_EN[weekday]}, time for the {station} Top {size} of the week, from your thumbs up."
        return {'intro': intro, 'lines': [f"Number {s['place']}: {s['title']}." for s in songs]}
    intro = f"{WEEKDAYS_RU[weekday].capitalize()}, а значит хит-парад {station}. Топ {size} недели по вашим отметкам."
    return {'intro': intro, 'lines': [f"Номер {s['place']}: {s['title']}." for s in songs]}
