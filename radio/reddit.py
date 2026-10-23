"""Temporary public Reddit Atom feeds; unavailable feeds are reported, never bypassed."""
import html
import xml.etree.ElementTree as ET

from . import public_news

# shortcut: public RSS ends November 13, 2026; replace only with approved API access when available.
ATOM = {"a": "http://www.w3.org/2005/Atom"}


def parse(text, community):
    root = ET.fromstring(text)
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
        # Feed titles are sufficient for a short bulletin; don't voice feed chrome or crawl linked articles.
        result.append(public_news.item("reddit", community, key, url,
                      entry.findtext("a:published", "", ATOM) or entry.findtext("a:updated", "", ATOM), html.escape(title)))
    return result


def fetch(community):
    community = public_news.names([community], "reddit")[0]
    return parse(public_news.fetch(f"https://www.reddit.com/r/{community}/new/.rss?limit=25"), community)
