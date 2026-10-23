"""Only the text and dates Telegram exposes in its public web preview, no history pagination."""
from html.parser import HTMLParser

from . import public_news


# shortcut: current public preview page only; use an authorized API if full history is requested.
class Preview(HTMLParser):
    def __init__(self, channel):
        super().__init__()
        self.channel, self.items, self.stack = channel, [], []
        self.post, self.text_depth, self.parts = None, None, []
        self.published = ""
        self.seen_post = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag not in ("br", "img", "meta", "link", "input", "hr", "source", "wbr"):
            self.stack.append(tag)
        classes = attrs.get("class", "").split()
        if "tgme_widget_message" in classes and "data-post" in attrs:
            self.post = attrs["data-post"]
            self.post_depth = len(self.stack)
            self.parts, self.published = [], ""
            self.seen_post = True
        if self.post and "tgme_widget_message_text" in classes:
            self.text_depth = len(self.stack)
        if self.post and tag == "time":
            self.published = attrs.get("datetime", "")
        if self.text_depth and tag == "br":
            self.parts.append(" ")

    def handle_data(self, data):
        if self.text_depth:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if not self.stack or tag not in self.stack:
            return
        if self.text_depth == len(self.stack):
            self.text_depth = None
        if self.post and len(self.stack) == self.post_depth:
            channel, sep, number = self.post.partition("/")
            if sep and channel.lower() == self.channel.lower() and number.isdigit():
                # Escape text already decoded by HTMLParser before the shared HTML normalizer.
                import html
                self.items.append(public_news.item("telegram", self.channel, self.post.lower(),
                                  "https://t.me/" + self.post, self.published, html.escape("".join(self.parts))))
            self.post = None
        while self.stack:
            if self.stack.pop() == tag:
                break


def parse(text, channel):
    parser = Preview(channel)
    parser.feed(text)
    if not parser.seen_post:
        raise ValueError("Telegram public preview unavailable (or no public posts)")
    return parser.items[-25:]


def fetch(channel):
    channel = public_news.names([channel], "telegram")[0]
    return parse(public_news.fetch(f"https://t.me/s/{channel}"), channel)
