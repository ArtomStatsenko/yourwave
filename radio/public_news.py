"""Small public-feed importer. No accounts, LLM, article crawling or access workarounds."""
import hashlib
import re
import unicodedata
import urllib.parse
import urllib.request
from html.parser import HTMLParser

from . import content

LIMIT = 2_000_000
HOSTS = {"www.reddit.com", "reddit.com", "t.me"}


class _Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def check_url(url):
    p = urllib.parse.urlsplit(url)
    if p.scheme != "https" or p.hostname not in HOSTS or p.port not in (None, 443) or p.username or p.password:
        raise ValueError("public news URL is outside the supported hosts")


def fetch(url):
    check_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": "Yourwave/0.1 (public news experiment)",
                                               "Accept": "application/atom+xml,text/html"})
    with urllib.request.build_opener(_Redirect()).open(req, timeout=15) as response:
        data = response.read(LIMIT + 1)
    if len(data) > LIMIT:
        raise ValueError("public feed is too large")
    return data.decode("utf-8")


def names(values, source):
    pattern = r"[A-Za-z0-9_]{2,21}" if source == "reddit" else r"[A-Za-z][A-Za-z0-9_]{3,31}"
    if not isinstance(values, list) or len(values) > 20 or any(not isinstance(v, str) or not re.fullmatch(pattern, v) for v in values):
        raise ValueError(f"sources.{source}: use up to 20 bare public names")
    return list(dict.fromkeys(v.lower() for v in values))


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        if tag in ("br", "p", "div", "li"):
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)
        if tag in ("p", "div", "li"):
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain(text):
    parser = PlainText()
    parser.feed(text)
    return " ".join(unicodedata.normalize("NFKC", "".join(parser.parts)).split())


def item(source, community, key, url, published, text):
    body = plain(text)
    return {"id": f"{source}:{key}", "source": source, "topic": f"{source.title()}: {community}",
            "url": url, "published_at": content.parse_time(published), "body": body,
            "origin": [{"repo": f"{source.title()}: {community}", "url": url, "description": body}]}


def take(db, cfg, items):
    added = []
    for it in items:
        if not it["body"] or not content.is_fresh(it["published_at"], cfg["sources"]["max_age_hours"]):
            continue
        if any(w.casefold() in it["body"].casefold() for w in cfg["sources"]["blocked_words"]):
            continue
        # Dedup survives restarts: source id, permalink, or identical normalized text across feeds.
        fingerprint = hashlib.sha256(" ".join(it["body"].casefold().split()).encode()).hexdigest()
        keys = [it["id"], "url:" + it["url"], "text:" + fingerprint]
        if db.execute("SELECT 1 FROM items WHERE id=?", (it["id"],)).fetchone() or db.execute(
                "SELECT 1 FROM public_news_seen WHERE key IN (?,?,?)", keys).fetchone():
            continue
        db.execute("SAVEPOINT public_news")
        try:
            content.add_item(db, cfg, it, kind="raw_post")
            db.executemany("INSERT INTO public_news_seen(key) VALUES(?)", [(k,) for k in keys])
            db.execute("RELEASE public_news")
        except Exception:
            db.execute("ROLLBACK TO public_news")
            db.execute("RELEASE public_news")
            raise
        added.append(it["id"])
        if len(added) >= 3:
            break
    return added
