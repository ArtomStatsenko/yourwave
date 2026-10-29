"""Temporary public Reddit Atom feeds; unavailable feeds are reported, never bypassed."""
import html
import fcntl
import math
import re
import time
import urllib.error
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from . import config, editor, public_news

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
        if key.startswith('t1_') and not with_comments:
            continue
        body = entry.findtext("a:content", "", ATOM)
        prose = re.search(r'<div\b[^>]*class=["\'][^"\']*\bmd\b[^"\']*["\'][^>]*>(.*?)</div>', body, re.S)
        if with_comments and key.startswith("t1_"):
            # RSS wraps comment text in .md; the rest is navigation, not material for the host.
            if not prose:
                continue
        if prose:
            title = public_news.plain(prose[1]) if key.startswith('t1_') else f"{title}. {public_news.plain(prose[1])}"
        elif re.search(r'<(?:img|video)\b|(?:i\.redd\.it|v\.redd\.it|reddit\.com/gallery)/', body, re.I):
            title += '. Вложение: изображение или видео; его содержание в тексте не раскрыто.'
        result.append(public_news.item("reddit", community, key, url,
                      entry.findtext("a:published", "", ATOM) or entry.findtext("a:updated", "", ATOM), html.escape(title)))
    return result


def fetch(community):
    community = public_news.names([community], "reddit")[0]
    return parse(_fetch(f"https://www.reddit.com/r/{community}/new/.rss?limit=25"), community)


def fetch_post(url):
    url = public_news.references([url], "reddit", "posts")[0]
    rows = parse(_fetch(url + ".rss?sort=top&limit=25"), "Discussion", with_comments=True)
    thread = re.search(r'/comments/([^/]+)/', url)[1]
    post = next((r for r in rows if not r['id'].startswith('reddit:t1_') and f'/comments/{thread}/' in r['url']), None)
    return [_discussion(post, rows)] if post else []


def _discussion(post, rows):
    match = re.search(r'/comments/([^/]+)/', post['url'])
    if not match:
        raise ValueError('Reddit thread URL is missing')
    thread = match[1]
    comments, seen = [], set()
    for row in rows:
        if (not row['id'].startswith('reddit:t1_') or f'/comments/{thread}/' not in row['url']
                or not row['body'] or row['published_at'] is None or len(row['body']) > 600 or editor.is_advertising(row['body'])
                or row['body'].strip().casefold() in ('[deleted]', '[removed]')
                or row['body'].casefold() in seen):
            continue
        comments.append(row)
        seen.add(row['body'].casefold())
        if len(comments) == 5:
            break
    origin = [dict(o) for o in post['origin']]
    origin[0]['comments_status'] = 'available' if comments else 'none'
    if not comments:
        return {**post, 'origin': origin}
    body = ('Текст поста (фрагмент):\n' + post['body'][:4000] +
            '\n\nКомментарии отдельных участников (не проверенные факты и не мнение всего сообщества):\n' +
            '\n'.join(f'{i + 1}. {r["body"]}' for i, r in enumerate(comments)))
    origin[0]['description'] = body
    origin.extend({**r['origin'][0], 'kind': 'comment', 'published_at': r['published_at'].isoformat()} for r in comments)
    return {**post, 'body': body, 'origin': origin}


def enrich(post):
    """Only newly selected posts incur a thread request; failures retain the original news."""
    try:
        url = public_news.references([post['url']], 'reddit', 'posts')[0]
        rows = parse(_fetch(url + '.rss?sort=top&limit=25'), '', with_comments=True)
        return _discussion(post, rows)
    except (OSError, ValueError):
        return {**post, 'origin': [{**o, 'comments_status': 'unavailable'} for o in post['origin']]}


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
