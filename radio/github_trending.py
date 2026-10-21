"""GitHub Trending/day adapter: top-1 repository of https://github.com/trending?since=daily.
GitHub has no official Trending API, so this parses HTML; on markup changes it raises ParseError
and the daemon marks the source degraded while the music keeps playing."""
import hashlib
import html
import re
import urllib.error
import urllib.parse
import urllib.request

URL = "https://github.com/trending?since=daily"
USER_AGENT = "PersonalRadio/0.1 (personal use; one request per hour)"


class ParseError(Exception):
    pass


class RateLimited(Exception):
    def __init__(self, retry_after: float):
        super().__init__(f"rate limited, retry after {retry_after:.0f}s")
        self.retry_after = retry_after


def fetch_html(timeout=20, language: str = "") -> str:
    """Today's trending page, overall or for one language (github.com/trending/<language>)."""
    url = URL if not language else f"https://github.com/trending/{urllib.parse.quote(language)}?since=daily"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(3_000_000).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise RateLimited(float(e.headers.get("Retry-After") or 3600)) from e
        raise


def _text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def _parse_entry(art: str) -> dict | None:
    h2 = re.search(r"<h2[^>]*>.*?href=\"/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)\"", art, re.S)
    if not h2:
        return None
    desc = re.search(r"<p class=\"[^\"]*color-fg-muted[^\"]*\">(.*?)</p>", art, re.S)
    lang = re.search(r'itemprop="programmingLanguage">(.*?)</span>', art, re.S)
    stars = re.search(r"([\d,]+)\s+stars? today", art)
    total = re.search(r'/stargazers"[^>]*>.*?([\d,]+)\s*</a>', art, re.S)
    num = lambda m: int(m.group(1).replace(",", "")) if m else None
    return {
        "owner": h2.group(1), "repo": h2.group(2),
        "description": _text(desc.group(1)) if desc else "",
        "language": _text(lang.group(1)) if lang else "",
        "stars_total": num(total), "stars_today": num(stars),
    }


def fetch_readme(owner: str, repo: str, limit: int = 1500, timeout: int = 15) -> str:
    """Opening of the repo's README as plain text (no code, HTML, badges or links), or "" if there is none.
    Untrusted text: it only feeds the retelling prompt, as data."""
    from .content import SEP, BREAK, speakable
    req = urllib.request.Request(f"https://api.github.com/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}/readme",
                                 headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github.raw"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            md = r.read(300_000).decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError):
        return ""
    md = re.sub(r"<!--.*?-->|<[^>]+>", " ", md, flags=re.S)
    md = re.sub(r"^\s*\[!\[.*$|^\s*!\[.*$|^\s*\|.*$", "", md, flags=re.M)  # badges, images, tables
    # Hard-wrapped lines back into paragraphs (keeping lists and headers), so no period lands mid-sentence.
    md = re.sub(r"(?<=\S)\n(?=[^\n\s#>|*+-])(?!\d+[.)]\s)", " ", md)
    text = speakable(md).replace(SEP, "").replace(BREAK, "")
    text = " ".join(p for p in text.split("\n\n") if len(p) > 25)  # drop crumbs: lone words, menu items
    return text[:limit].rsplit(" ", 1)[0] if len(text) > limit else text


def parse_list(page: str, n: int = 3) -> list[dict]:
    """The first n repositories in page order, each with its "rank" (1-based)."""
    arts = re.findall(r'<article class="Box-row">(.*?)</article>', page, re.S)
    if not arts:
        raise ParseError("no <article class=\"Box-row\"> on the page")
    first = _parse_entry(arts[0])
    if first is None:
        raise ParseError("no repository link in the first entry")
    out = [dict(first, rank=1)]
    for rank, art in enumerate(arts[1:n], 2):
        if entry := _parse_entry(art):
            out.append(dict(entry, rank=rank))
    return out


def parse_top(page: str) -> dict:
    return parse_list(page, 1)[0]


def repo_id(top: dict) -> str:
    return f"github:{top['owner']}/{top['repo']}".lower()


def airable(t: dict, blocked_words: tuple[str, ...] = ()) -> bool:
    """Has a description to retell and none of the owner's blocked words ([sources] blocked_words) in its
    name or description. No other filtering."""
    text = f"{t['owner']}/{t['repo']} {t.get('description') or ''}".lower()
    return bool(t.get("description")) and not any(w.lower() in text for w in blocked_words if w)


def select_mix(tops: list[dict], seen: set[str], top_picks: int, indie_picks: int,
               indie_max_total_stars: int, blocked_words: tuple[str, ...] = ()) -> list[tuple[dict, str]]:
    """GitHub's own ranking, no taste of ours: the best-ranked new repos as "top", then small ones that made
    today's trending list ("indie": under indie_max_total_stars in total), in page order."""
    fresh = [t for t in tops if repo_id(t) not in seen and airable(t, blocked_words)]
    picked = [(t, "top") for t in fresh[:top_picks]]
    taken = {repo_id(t) for t, _ in picked}
    indie = [t for t in fresh if repo_id(t) not in taken and (t["stars_total"] or 0) < indie_max_total_stars]
    return picked + [(t, "indie") for t in indie[:indie_picks]]


_UNITS = ["", "одного", "двух", "трёх", "четырёх", "пяти", "шести", "семи", "восьми", "девяти"]
_TEENS = ["десяти", "одиннадцати", "двенадцати", "тринадцати", "четырнадцати", "пятнадцати", "шестнадцати",
          "семнадцати", "восемнадцати", "девятнадцати"]
_TENS = ["", "", "двадцати", "тридцати", "сорока", "пятидесяти", "шестидесяти", "семидесяти", "восьмидесяти",
         "девяноста"]
_HUNDREDS = ["", "ста", "двухсот", "трёхсот", "четырёхсот", "пятисот", "шестисот", "семисот", "восьмисот",
             "девятисот"]


def _genitive(n: int, feminine=False) -> str:
    """1..999 in genitive case as words: 42 -> "сорока двух". TTS cannot inflect digits itself."""
    h, rest = divmod(n, 100)
    words = [_HUNDREDS[h]]
    if 10 <= rest < 20:
        words.append(_TEENS[rest - 10])
    else:
        t, u = divmod(rest, 10)
        words += [_TENS[t], "одной" if u == 1 and feminine else _UNITS[u]]
    return " ".join(w for w in words if w)


def approx(n: int) -> str:
    """Rounded down, for the ear: 42218 -> "более сорока двух тысяч", 1187 -> "более тысячи", 560 -> "более пятисот"."""
    if n >= 1000:
        k = min(n // 1000, 999)
        if k == 1:
            return "более тысячи"
        return f"более {_genitive(k, feminine=True)} {'тысячи' if k % 10 == 1 and k % 100 != 11 else 'тысяч'}"
    if n >= 100:
        return f"более {_genitive(n // 100 * 100)}"
    if n >= 20:
        return f"более {_genitive(n // 10 * 10)}"
    return str(n)


def _stars_word(n: int) -> str:
    """Nominative after a number: 1 звезда, 3 звезды, 5 звёзд."""
    if n % 10 == 1 and n % 100 != 11:
        return "звезда"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "звезды"
    return "звёзд"


def _stars_phrase(n: int) -> str:
    """Rounded down, in words, for the ear: 51254 -> "более пятидесяти одной тысячи звёзд", 7 -> "7 звёзд"."""
    if n >= 20:
        return f"{approx(n)} звёзд"
    return f"{n} {_stars_word(n)}"


def _say_name(name: str) -> str:
    return re.sub(r"[-_.]+", " ", name).strip()


# Lead-in before the retelling (the topic itself is not spoken when the section intro plays).
# (with the name, without it): the second is used when the retelling already starts with the name.
LEADS = {
    "top": [("В трендах GitHub сегодня — {name}.", "В трендах GitHub сегодня."),
            ("Сегодня на первых строчках GitHub — {name}.", "Сегодня на первых строчках GitHub."),
            ("Главный тренд дня на GitHub — {name}.", "Главный тренд дня на GitHub.")],
    "indie": [("А вот проект поменьше — {name}.", "А вот проект поменьше."),
              ("Небольшая находка с GitHub — {name}.", "Небольшая находка с GitHub."),
              ("Из маленьких проектов дня — {name}.", "Из маленьких проектов дня.")],
    "new": [("Свежий проект этой недели — {name}.", "Свежий проект этой недели."),
            ("Совсем новый проект на GitHub — {name}.", "Совсем новый проект на GitHub."),
            ("Новинка недели — {name}.", "Новинка недели.")],
}


def lead_in(role: str, repo: str, summary: str) -> str:
    variants = LEADS[role]
    with_name, without = variants[int(hashlib.sha256(repo.encode()).hexdigest(), 16) % len(variants)]
    norm = lambda x: re.sub(r"[^a-zа-яё0-9]", "", x.lower())
    names_it = norm(repo)[:max(4, len(norm(repo)) // 2)] in norm(summary)
    return without if names_it else with_name.format(name=_say_name(repo))


TOPICS = {"top": "GitHub, тренд дня", "indie": "GitHub, находка дня", "new": "GitHub, новинка недели"}


def to_item(t: dict, summary: str | None, role: str, daily_min: int = 1000,
            source: str = "github_trending") -> dict | None:
    """One short bulletin per repository: name, a one-sentence Russian retelling (editor.py), numbers from GitHub.
    None without a retelling: an English description or a bare name is not aired. Topics are fixed per role,
    so their TTS clip is generated once."""
    if not summary:
        return None
    total, today = t.get("stars_total") or 0, t.get("stars_today") or 0
    if role == "new":
        stars = f" Проекту меньше недели, а у него уже {_stars_phrase(total)}." if total else ""
    elif role == "indie":
        stars = f" Пока у него {_stars_phrase(total)}, но он уже в трендах дня." if total else ""
    elif today >= daily_min:
        stars = f" Уже {_stars_phrase(total)}, и {approx(today)} новых за сутки." if total else ""
    else:
        stars = f" Уже {_stars_phrase(total)}." if total else ""
    url = f"https://github.com/{t['owner']}/{t['repo']}"
    return {
        "id": repo_id(t),
        "topic": TOPICS[role],
        "source": source,
        "url": url,
        "body": f"{lead_in(role, t['repo'], summary)} {summary}{stars}",
        # Source details for the UI: what was said came from these.
        "origin": [{"rank": t.get("rank"), "role": role, "repo": f"{t['owner']}/{t['repo']}", "url": url,
                    "description": t.get("description", ""), "stars_total": t.get("stars_total"),
                    "stars_today": t.get("stars_today")}],
    }
