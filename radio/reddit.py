"""Temporary public Reddit Atom feeds; unavailable feeds are reported, never bypassed."""
import html
import fcntl
import math
import re
import time
import urllib.error
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from . import config, public_news

# shortcut: public RSS ends November 13, 2026; replace only with an approved source when available.
ATOM = {"a": "http://www.w3.org/2005/Atom"}


def _rate_limit(retry_at):
    minutes = max(1, math.ceil((retry_at - time.time()) / 60))
    return ValueError(f"Reddit rate limit reached. Next local retry in about {minutes} min; your saved sources are unchanged.")


def _fetch(url):
    # UI checks and daemon imports share Reddit's cooldown, including across restarts.
    config.RUN_DIR.mkdir(parents=True, exist_ok=True)
    with (config.RUN_DIR / "reddit-rss.lock").open("a+") as state:
        fcntl.flock(state, fcntl.LOCK_EX)
        state.seek(0)
        try:
            retry_at = float(state.read() or 0)
        except ValueError:
            retry_at = 0
        if math.isfinite(retry_at) and retry_at > time.time():
            raise _rate_limit(retry_at)
        try:
            return public_news.fetch(url)
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
            after = e.headers.get("Retry-After", "") if e.headers else ""
            retry_at = time.time() + 1800
            try:
                candidate = time.time() + float(after)
            except ValueError:
                try:
                    candidate = parsedate_to_datetime(after).timestamp()
                except (ValueError, TypeError, OverflowError):
                    candidate = retry_at
            if math.isfinite(candidate):
                retry_at = max(time.time() + 1, candidate)
            e.close()
            state.seek(0)
            state.truncate()
            state.write(str(retry_at))
            state.flush()
            raise _rate_limit(retry_at) from None


def parse(text, community, with_comments=False):
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        raise ValueError("Reddit did not return a readable RSS feed") from None
    if root.tag != '{http://www.w3.org/2005/Atom}feed':
        raise ValueError("Reddit did not return an Atom feed")
    result = []
    for entry in root.findall("a:entry", ATOM)[:25]:
        key = entry.findtext("a:id", "", ATOM)
        link = entry.find("a:link", ATOM)
        url = link.get("href", "") if link is not None else ""
        if not key or not url.startswith("https://www.reddit.com/r/"):
            continue
        title = entry.findtext("a:title", "", ATOM)
        if with_comments and key.startswith("t1_"):
            body = entry.findtext("a:content", "", ATOM)
            # RSS wraps comment text in .md; the rest is navigation, not material for the host.
            comment = re.search(r'<div class="md">(.*?)</div>', body, re.S)
            if not comment:
                continue
            title = f"{title}. {public_news.plain(comment[1])}"
        result.append(public_news.item("reddit", community, key, url,
                      entry.findtext("a:published", "", ATOM) or entry.findtext("a:updated", "", ATOM), html.escape(title)))
    return result


def fetch(community):
    community = public_news.names([community], "reddit")[0]
    return parse(_fetch(f"https://www.reddit.com/r/{community}/new/.rss?limit=25"), community)


def fetch_post(url):
    url = public_news.references([url], "reddit", "posts")[0]
    return parse(_fetch(url + ".rss?sort=new&limit=25"), "Discussion", with_comments=True)


def validate(values, kind):
    values = public_news.references(values, "reddit", kind)
    for value in values:
        try:
            (fetch if kind == "names" else fetch_post)(value)
        except urllib.error.HTTPError as e:
            code = e.code
            e.close()
            if code == 404:
                message = "Source does not exist or its RSS feed is unavailable."
            elif code == 429:
                message = "Reddit rate limit reached. Try again later."
            else:
                message = "Reddit RSS is unavailable or access was denied. Try again later."
            raise ValueError(f"{value}: {message}") from None
        except OSError:
            raise ValueError(f"{value}: Could not reach Reddit RSS. Nothing was saved; try again.") from None
        except ValueError as e:
            raise ValueError(f"{value}: {e}") from None
    return values
