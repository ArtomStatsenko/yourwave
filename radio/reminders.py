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

USER_AGENT = "PersonalRadio/0.1 (personal reminders, read only)"
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


def parse_events(ics: str, local=None) -> list[dict]:
    """VEVENTs, including all-day dates; cancelled overrides remove occurrences of their series."""
    local = local or datetime.now().astimezone().tzinfo
    events, cur = [], None
    for line in _unfold(ics):
        if line == "BEGIN:VEVENT":
            cur = {"exdates": [], "rrule": None, "recurrence_id": None, "title": "", "uid": "", "cancelled": False,
                   "all_day": False, "description": ""}
        elif line == "END:VEVENT" and cur is not None:
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
            elif name == "DESCRIPTION":
                cur["description"] = _unescape(value)
            elif name == "SUMMARY":
                cur["title"] = _unescape(value)
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


def calendar_due(events: list[dict], now: datetime, lead: timedelta) -> list[dict]:
    """Events starting within `lead` from now: {key, title, start, kind}. A moved occurrence replaces the
    original one; an unsupported rule skips that event."""
    moved = {(e["uid"], e["recurrence_id"]) for e in events if e["recurrence_id"]}
    out = []
    for ev in events:
        if ev["cancelled"] or ev.get("all_day"):
            continue
        try:
            for occ in occurrences(ev, now, now + lead + timedelta(seconds=1)):  # exactly `lead` away counts
                if ev["recurrence_id"] or (ev["uid"], occ.replace(tzinfo=None)) not in moved:
                    out.append({"key": f"cal:{ev['uid']}:{occ.astimezone(timezone.utc):%Y%m%dT%H%M}",
                                "title": ev["title"], "start": occ, "kind": "event"})
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


def clean_title(title: str) -> str:
    title = re.sub(r"https?://\S+", "", title)
    title = re.sub(r"[←-⯿\U0001f000-\U0001faff️]", "", title)
    return " ".join(title.split())[:90].rstrip(" .,;:")


# ---------------------------------------------------------------------------------------------------- Todoist
def fetch_todoist(token: str) -> list[dict]:
    """Active tasks (all pages). Auth: the personal API token."""
    tasks, cursor = [], None
    for _ in range(10):
        q = {"limit": 200, **({"cursor": cursor} if cursor else {})}
        data = json.loads(_get(f"{TODOIST_URL}?{urllib.parse.urlencode(q)}", {"Authorization": f"Bearer {token}"}))
        tasks += data["results"] if isinstance(data, dict) else data
        cursor = data.get("next_cursor") if isinstance(data, dict) else None
        if not cursor:
            break
    return tasks


def todoist_due(tasks: list[dict], now: datetime, lead: timedelta, local=None) -> list[dict]:
    """Tasks with a time that fall within `lead` from now. due.date is YYYY-MM-DD (all day: no reminder),
    YYYY-MM-DDTHH:MM:SS (floating: local time) or ...Z (UTC)."""
    local = local or datetime.now().astimezone().tzinfo
    out = []
    for t in tasks:
        d = ((t.get("due") or {}).get("date") or "")
        if "T" not in d:
            continue
        try:
            at = datetime.fromisoformat(d.replace("Z", "+00:00"))
            if at.tzinfo is None:
                at = at.replace(tzinfo=ZoneInfo(t["due"]["timezone"]) if t["due"].get("timezone") else local)
        except (ValueError, ZoneInfoNotFoundError):
            continue
        if now <= at <= now + lead:
            out.append({"key": f"todoist:{t['id']}:{at.astimezone(timezone.utc):%Y%m%dT%H%M}",
                        "title": t.get("content", ""), "start": at, "kind": "task",
                        "description": t.get("description") or ""})
    return sorted(out, key=lambda r: r["start"])


# ---------------------------------------------------------------------------------------------------- spoken text
def say(r: dict, at: datetime, minutes_words) -> str | None:
    """The reminder as the host says it when it airs at `at`; None if it has already started."""
    minutes = round((r["start"] - at).total_seconds() / 60)
    title = clean_title(r["title"])
    if minutes < 0 or not title:
        return None
    when = "Сейчас" if minutes == 0 else f"Через {minutes_words(minutes)}"
    # A dash, not a colon: the voice turns "минут: Название" into noise for some titles (measured).
    return f"Напоминание. {when}{' задача' if r['kind'] == 'task' else ''} — {title}."
