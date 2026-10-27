"""Read-only calendar and Todoist reminders. Timed events use a lead window; all-day dates become host topics.

shortcut: the iCal reader knows the recurrence rules calendars actually write (DAILY/WEEKLY/MONTHLY/YEARLY with
INTERVAL, COUNT, UNTIL, BYDAY incl. "2TU", BYMONTHDAY, BYMONTH, EXDATE, moved occurrences), not BYSETPOS or
exotic ones: an event with such a rule is reported by unsupported(). Upgrade to the `icalendar` +
`recurring-ical-events` packages if that ever matters."""
import hashlib
import json
import re
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

USER_AGENT = "Yourwave/0.1 (personal reminders, read only)"
TODOIST_URL = "https://api.todoist.com/api/v1/tasks"
WEEKDAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
MAX_OCCURRENCES = 20000


def _get(url: str, headers: dict | None = None, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


# ---------------------------------------------------------------------------------------------------- iCal
def fetch_ics(url: str) -> str:
    url = re.sub(r"^webcal://", "https://", url.strip())
    if not url.startswith("https://"):
        raise ValueError("the calendar address must be an https:// (or webcal://) link")
    return _get(url)


def _unfold(text: str) -> list[str]:
    return re.sub(r"\r?\n[ \t]", "", text).splitlines()


def _prop(line: str) -> tuple[str, dict, str]:
    head, _, value = line.partition(":")
    name, *params = head.split(";")
    return name.upper(), dict(p.split("=", 1) for p in params if "=" in p), value


def _unescape(s: str) -> str:
    return re.sub(r"\\(.)", lambda m: "\n" if m[1] in "nN" else m[1], s)


def _when(value: str, params: dict, local: ZoneInfo | timezone):
    """Naive wall time and timezone; dates use local midnight for recurrence expansion."""
    if params.get("VALUE") == "DATE" or len(value) == 8:
        return datetime.strptime(value, "%Y%m%d"), local
    naive = datetime.strptime(value.rstrip("Z"), "%Y%m%dT%H%M%S")
    if value.endswith("Z"):
        return naive, timezone.utc
    tzid = params.get("TZID")
    if tzid:
        try:
            return naive, ZoneInfo(tzid)
        except (ZoneInfoNotFoundError, ValueError):
            pass  # shortcut: a Windows-style zone name is read as local time
    return naive, local


def _duration(value):
    match = re.fullmatch(r'([+-]?)P(?:(\d+)W|(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?)', value)
    if not match or not any(part is not None for part in match.groups()[1:]):
        return None
    sign, weeks, days, hours, minutes, seconds = match.groups()
    try:
        return timedelta(weeks=int(weeks or 0), days=int(days or 0), hours=int(hours or 0),
                         minutes=int(minutes or 0), seconds=int(seconds or 0)) * (-1 if sign == '-' else 1)
    except (OverflowError, ValueError):
        return None


def parse_events(ics: str, local=None) -> list[dict]:
    """VEVENTs, including all-day dates; cancelled overrides remove occurrences of their series."""
    local = local or datetime.now().astimezone().tzinfo
    events, cur, alarm = [], None, None
    for line in _unfold(ics):
        if line == "BEGIN:VEVENT":
            cur = {"exdates": [], "rrule": None, "recurrence_id": None, "title": "", "uid": "", "cancelled": False,
                   "all_day": False, "description": "", "alarms": []}
        elif line == "BEGIN:VALARM" and cur is not None:
            alarm = {}
        elif line == "END:VALARM" and cur is not None:
            if alarm and 'trigger' in alarm:
                cur['alarms'].append(alarm)
            alarm = None
        elif alarm is not None:
            name, params, value = _prop(line)
            if name == 'TRIGGER':
                try:
                    if params.get('VALUE') == 'DATE-TIME':
                        at, tz = _when(value, params, local)
                        alarm['trigger'] = at.replace(tzinfo=tz)
                    elif (offset := _duration(value)) is not None:
                        alarm.update(trigger=offset, related=params.get('RELATED', 'START'))
                except ValueError:
                    pass
        elif line == "END:VEVENT" and cur is not None:
            if cur.get('duration') is not None and cur.get('start') and not cur.get('end'):
                cur['end'] = cur['start'] + cur['duration']
            if cur.get("start") and (not cur["cancelled"] or cur["recurrence_id"]):
                events.append(cur)  # a cancelled override is kept: it removes that occurrence of its series
            cur = None
        elif cur is not None:
            name, params, value = _prop(line)
            if name == "DTSTART" and (w := _when(value, params, local)):
                cur["start"], cur["tz"] = w
                cur["all_day"] = params.get("VALUE") == "DATE" or len(value) == 8
            elif name == "DTEND" and (w := _when(value, params, local)):
                cur["end"] = w[0]
            elif name == "DURATION":
                cur["duration"] = _duration(value)
            elif name == "DESCRIPTION":
                cur["description"] = _unescape(value)
            elif name == "SUMMARY":
                cur["title"] = _unescape(value)
            elif name == "CATEGORIES":
                cur["categories"] = [_unescape(v).casefold().strip() for v in value.split(",")]
            elif name == "UID":
                cur["uid"] = value
            elif name == "STATUS" and value.upper() == "CANCELLED":
                cur["cancelled"] = True
            elif name == "RRULE":
                cur["rrule"] = dict(p.split("=", 1) for p in value.split(";") if "=" in p)
            elif name == "EXDATE":
                cur["exdates"] += [w[0] for v in value.split(",") if (w := _when(v, params, local))]
            elif name == "RECURRENCE-ID":
                w = _when(value, params, local)
                cur["recurrence_id"] = w[0] if w else None
    return events


def _byday(rule: dict) -> list[tuple[int, int]]:
    out = []
    for d in filter(None, rule.get("BYDAY", "").split(",")):
        m = re.fullmatch(r"([+-]?\d+)?(MO|TU|WE|TH|FR|SA|SU)", d)
        if not m:
            raise ValueError(f"BYDAY={d}")
        out.append((int(m[1] or 0), WEEKDAYS.index(m[2])))
    return out


def _month(y: int, m: int) -> list[date]:
    d, out = date(y, m, 1), []
    while d.month == m:
        out.append(d)
        d += timedelta(days=1)
    return out


def _in_month(y: int, m: int, by_day: list, by_md: list, day: int) -> list[date]:
    """The dates of one month that a MONTHLY/YEARLY rule selects ("2TU", "-1FR", BYMONTHDAY incl. -1)."""
    days = _month(y, m)
    if by_day:
        out = []
        for n, wd in by_day:
            same = [d for d in days if d.weekday() == wd]
            out += ([same[n - 1 if n > 0 else n]] if abs(n) <= len(same) else []) if n else same
        return sorted(out)
    return sorted({days[(d - 1 if d > 0 else d)] for d in (by_md or [day]) if 0 < abs(d) <= len(days)})


def _dates(start: date, rule: dict):
    """Dates of a recurrence rule, in order, from the start date (unbounded: the caller stops)."""
    freq, step = rule.get("FREQ"), int(rule.get("INTERVAL", 1))
    by_day, by_md = _byday(rule), [int(x) for x in rule.get("BYMONTHDAY", "").split(",") if x]
    by_month = [int(x) for x in rule.get("BYMONTH", "").split(",") if x]
    if {"BYSETPOS", "BYWEEKNO", "BYYEARDAY"} & rule.keys() or freq not in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY"):
        raise ValueError("unsupported recurrence rule")
    for k in range(50000):  # a rule that never matches must not loop for ever
        if freq == "DAILY":
            d = start + timedelta(days=k * step)
            if (not by_day or any(d.weekday() == wd for _, wd in by_day)) and (not by_month or d.month in by_month):
                yield d
        elif freq == "WEEKLY":
            monday = start - timedelta(days=start.weekday()) + timedelta(weeks=k * step)
            for wd in sorted({wd for _, wd in by_day} or {start.weekday()}):
                if monday + timedelta(days=wd) >= start:
                    yield monday + timedelta(days=wd)
        elif freq == "MONTHLY":
            y, m = divmod(start.month - 1 + k * step, 12)
            yield from (d for d in _in_month(start.year + y, m + 1, by_day, by_md, start.day) if d >= start)
        else:  # YEARLY
            for m in by_month or [start.month]:
                yield from (d for d in _in_month(start.year + k * step, m, by_day, by_md, start.day) if d >= start)


def _until(rule: dict, tz) -> datetime | None:
    u = rule.get("UNTIL")
    if not u:
        return None
    naive = datetime.strptime(u.rstrip("Z")[:15] if "T" in u else u + "T235959", "%Y%m%dT%H%M%S")
    return naive.replace(tzinfo=timezone.utc) if u.endswith("Z") else naive.replace(tzinfo=tz)


def occurrences(ev: dict, lo: datetime, hi: datetime) -> list[datetime]:
    """Aware start times of an event inside [lo, hi). Raises ValueError for a rule it does not know."""
    tz, start = ev["tz"], ev["start"]
    first = start.replace(tzinfo=tz)
    if not ev["rrule"]:
        return [first] if lo <= first < hi else []
    rule, until, count = ev["rrule"], _until(ev["rrule"], tz), int(ev["rrule"].get("COUNT", 0))
    out, n = [], 0
    for d in _dates(start.date(), rule):
        occ = datetime.combine(d, start.time()).replace(tzinfo=tz)
        n += 1
        if (until and occ > until) or (count and n > count) or occ >= hi or n > MAX_OCCURRENCES:
            break
        if occ >= lo and occ.replace(tzinfo=None) not in ev["exdates"]:
            out.append(occ)
    return out


def unsupported(events: list[dict]) -> list[str]:
    """Titles of events whose recurrence rule the reader cannot expand."""
    bad, now = [], datetime.now(timezone.utc)
    for ev in events:
        try:
            occurrences(ev, now, now + timedelta(minutes=1))
        except ValueError:
            bad.append(ev["title"])
    return bad


def calendar_due(events: list[dict], now: datetime, lead: timedelta, *, horizon=timedelta()) -> list[dict]:
    """Timed events use their earliest alarm, or the same fallback lead as Todoist."""
    moved = {(e["uid"], e["recurrence_id"]) for e in events if e["recurrence_id"]}
    out = []
    for ev in events:
        if ev["cancelled"] or ev.get("all_day"):
            continue
        duration = ev.get('end', ev['start']) - ev['start']
        offsets, absolute = [], []
        for alarm in ev.get('alarms', []):
            trigger = alarm['trigger']
            if isinstance(trigger, timedelta):
                if alarm.get('related') == 'END':
                    if not ev.get('end'):
                        continue
                    trigger += duration
                offsets.append(trigger)
            else:
                absolute.append(trigger)
        advance = max([lead, *[-offset for offset in offsets],
                       *[ev['start'].replace(tzinfo=ev['tz']) - at for at in absolute]])
        try:
            for occ in occurrences(ev, now, now + horizon + advance + timedelta(seconds=1)):
                if not ev["recurrence_id"] and (ev["uid"], occ.replace(tzinfo=None)) in moved:
                    continue
                times = [occ + offset for offset in offsets]
                if occ == ev['start'].replace(tzinfo=ev['tz']):
                    times += absolute
                remind_at = min(times) if times else occ - lead
                if remind_at <= now + horizon:
                    out.append({"key": f"cal:{ev['uid']}:{occ.astimezone(timezone.utc):%Y%m%dT%H%M}",
                                "title": ev["title"], "start": occ, "kind": "event",
                                "end": occ + duration, "remind_at": remind_at,
                                "description": ev.get('description', '')})
        except ValueError:
            continue
    return sorted(out, key=lambda r: r["start"])


def day_due(events: list[dict], at: datetime, history: list) -> dict | None:
    """One topic for today: at most three mentions overall, at least four hours apart.
    History is (key, fired_at) rows from the existing reminders table, for this local date."""
    if len(history) >= 3 or any(at - datetime.fromisoformat(r[1]) < timedelta(hours=4) for r in history):
        return None
    day = at.date()
    midnight = at.replace(hour=0, minute=0, second=0, microsecond=0)
    moved = {(e["uid"], e["recurrence_id"]) for e in events if e["recurrence_id"]}
    candidates = {}
    for ev in events:
        title = clean_title(ev["title"])
        if not ev.get("all_day") or ev["cancelled"] or not title:
            continue
        duration = ev.get("end", ev["start"] + timedelta(days=1)) - ev["start"]
        days = max(1, duration.days)
        try:
            for occ in occurrences({**ev, "tz": at.tzinfo}, midnight - timedelta(days=days - 1), midnight + timedelta(days=1)):
                if not ev["recurrence_id"] and (ev["uid"], occ.replace(tzinfo=None)) in moved:
                    continue
                if not occ.date() <= day < occ.date() + timedelta(days=days):
                    continue
                uid = hashlib.sha256((ev["uid"] or ev["title"]).encode()).hexdigest()[:16]
                prefix = f"day:{day}:{uid}:"
                count = sum(r[0].startswith(prefix) for r in history)
                candidates[prefix] = {"key": prefix + str(count + 1), "title": title,
                                      "description": ev["description"], "count": count, "kind": "day",
                                      "start": midnight}
        except ValueError:
            continue
    return min(candidates.values(), key=lambda r: (r["count"], r["key"]), default=None)


def holiday_due(events: list[dict], at: datetime, history: list) -> dict | None:
    """One preview a day, once 3–5 days ahead and optionally once on the eve of a selected holiday."""
    if any(datetime.fromisoformat(row[1]).astimezone(at.tzinfo).date() == at.date() for row in history):
        return None
    midnight = at.replace(hour=0, minute=0, second=0, microsecond=0)
    moved = {(e['uid'], e['recurrence_id']) for e in events if e['recurrence_id']}
    candidates = []
    selected_uids = {e['uid'] for e in events if (e.get('rrule') or {}).get('FREQ') == 'YEARLY' or
                     {'holiday', 'holidays', 'праздник', 'праздники'} & set(e.get('categories', []))}
    for ev in events:
        if ev['cancelled'] or not ev.get('all_day'):
            continue
        selected = ev['uid'] in selected_uids
        title = clean_title(ev['title'])
        if not selected or not title:
            continue
        try:
            for occ in occurrences({**ev, 'tz': at.tzinfo}, midnight + timedelta(days=1), midnight + timedelta(days=6)):
                if not ev['recurrence_id'] and (ev['uid'], occ.replace(tzinfo=None)) in moved:
                    continue
                days = (occ.date() - at.date()).days
                greeting = bool(re.search(r'день рожд|birthday|день матери|день отца|mother.?s day|father.?s day', title, re.I))
                if days == 2 or days == 1 and not greeting:
                    continue
                phase = 'eve' if days == 1 else 'early'
                uid = hashlib.sha256((ev['uid'] or ev['title']).encode()).hexdigest()[:16]
                key = f'holiday:{uid}:{occ.date()}:{phase}'
                if any(row[0] == key for row in history):
                    continue
                when = {1: 'Завтра', 3: 'Через три дня', 4: 'Через четыре дня', 5: 'Через пять дней'}[days]
                text = f'{when} — {title}.'
                if greeting:
                    text += ' Ещё есть время придумать поздравление.' if phase == 'early' else 'Можно подготовить пару тёплых слов.'
                candidates.append({'key': key, 'title': title, 'description': '', 'count': 0, 'kind': 'day',
                                   'start': midnight, 'holiday_preview': True, 'text': text, 'holiday_date': occ.date()})
        except ValueError:
            continue
    return min(candidates, key=lambda r: (r['holiday_date'], r['key']), default=None)


def clean_title(title: str) -> str:
    title = re.sub(r"https?://\S+", "", title)
    title = re.sub(r"[←-⯿\U0001f000-\U0001faff️]", "", title)
    return " ".join(title.split())[:90].rstrip(" .,;:")


# ---------------------------------------------------------------------------------------------------- Todoist
def fetch_todoist(token: str) -> list[dict]:
    """Active tasks and their reminders (all pages). Auth: the personal API token."""
    def pages(url):
        rows, cursor = [], None
        for _ in range(10):
            q = {"limit": 200, **({"cursor": cursor} if cursor else {})}
            data = json.loads(_get(f"{url}?{urllib.parse.urlencode(q)}", {"Authorization": f"Bearer {token}"}))
            rows += data["results"] if isinstance(data, dict) else data
            cursor = data.get("next_cursor") if isinstance(data, dict) else None
            if not cursor:
                return rows
        raise ValueError("Todoist pagination limit exceeded")

    tasks = pages(TODOIST_URL)
    notices = pages("https://api.todoist.com/api/v1/reminders")
    for task in tasks:
        task["reminders"] = [r for r in notices if str(r.get("item_id")) == str(task["id"]) and not r.get("is_deleted")]
    return tasks


def todoist_due(tasks: list[dict], now: datetime, lead: timedelta, local=None, *, horizon=timedelta()) -> list[dict]:
    """Timed tasks whose reminder is due by now + horizon; lead is the fallback offset."""
    local = local or datetime.now().astimezone().tzinfo
    out = []

    def timestamp(due):
        d = (due or {}).get("date") or ""
        if "T" not in d:
            return None
        try:
            at = datetime.fromisoformat(d.replace("Z", "+00:00"))
            return at if at.tzinfo else at.replace(tzinfo=ZoneInfo(due["timezone"]) if due.get("timezone") else local)
        except (ValueError, ZoneInfoNotFoundError):
            return None

    for t in tasks:
        at = timestamp(t.get("due"))
        if at is None:
            continue
        times = []
        for notice in t.get("reminders", []):
            if notice.get("is_deleted"):
                continue
            if notice.get("type") == "relative":
                offset = notice.get("minute_offset")
                if isinstance(offset, int) and not isinstance(offset, bool) and offset >= 0:
                    times.append(at - timedelta(minutes=offset))
            elif notice.get("type") == "absolute":
                when = timestamp(notice.get("due"))
                if when is not None:
                    times.append(when)
        # shortcut: one announcement per task occurrence; support each reminder if repeated announcements are needed.
        remind_at = min(times) if times else at - lead
        if now <= at and remind_at <= now + horizon:
            out.append({"key": f"todoist:{t['id']}:{at.astimezone(timezone.utc):%Y%m%dT%H%M}",
                        "title": t.get("content", ""), "start": at, "kind": "task", "end": at,
                        "remind_at": remind_at, "description": t.get("description") or ""})
    return sorted(out, key=lambda r: r["start"])


# ---------------------------------------------------------------------------------------------------- spoken text
LATE = timedelta(minutes=15)  # a reminder moved past the start still airs until the end, or this long after a start

BEFORE = {
    "event": ("Кстати, через {when} — {title}. Самое время потихоньку закругляться.",
              "Небольшая сверка с календарём: через {when} у тебя — {title}.",
              "Пока играет музыка, напомню: через {when} — {title}."),
    "task": ("И про дела: через {when} по плану — {title}.",
             "Загляну в список дел: через {when} пора браться — {title}.",
             "Через {when} в планах — {title}. Можно уже готовиться."),
}
NOW = {
    "event": ("А вот и время — {title}. Начинается прямо сейчас.",
              "Часы подсказывают: прямо сейчас — {title}."),
    "task": ("Пора: по плану прямо сейчас — {title}.",
             "Самое время взяться за дело — {title}."),
}
LATER = {
    "event": ("Если ты ещё не там — {title} уже идёт, с начала прошло {when}.",
              "Маленькое напоминание вдогонку: уже {when} как идёт — {title}."),
    "task": ("Время для этого подошло {when} назад, но ещё не поздно — {title}.",
             "Вдогонку из списка дел — {title}. По плану было {when} назад."),
}


def until(r: dict) -> datetime:
    """The last moment a reminder still makes sense: the event's end, at least LATE after the start."""
    return max(r.get("end") or r["start"], r["start"] + LATE)


def say(r: dict, at: datetime, span_words) -> str | None:
    """The reminder as the host says it when it airs at `at`: before, at or after the start (a reminder moved
    later); None once it no longer makes sense. The phrasing rotates by the reminder's key."""
    minutes = round((r["start"] - at).total_seconds() / 60)
    title = clean_title(r["title"])
    if not title or at >= until(r):
        return None
    lines = (BEFORE if minutes > 0 else NOW if minutes == 0 else LATER)[r["kind"] if r["kind"] == "task" else "event"]
    pick = int(hashlib.sha256(str(r.get("key") or title).encode()).hexdigest(), 16) % len(lines)
    # A dash, not a colon, before the title: the voice turns "минут: Название" into noise for some titles (measured).
    return lines[pick].format(when=span_words(abs(minutes)), title=title)


def plain(r: dict, at: datetime, span_words) -> str:
    """The shortest form, for a title the voice cannot read inside a longer phrase."""
    minutes = round((r["start"] - at).total_seconds() / 60)
    when = f"Через {span_words(minutes)}" if minutes > 0 else "Сейчас" if minutes == 0 else "Уже идёт"
    return f"{when} — {clean_title(r['title'])}."
