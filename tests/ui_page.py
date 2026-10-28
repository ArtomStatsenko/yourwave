"""The page as one text, for tests that read or run parts of it: the theme's index.html with its stylesheet and
scripts inlined in place, so the source reads in the order the browser runs it."""
import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "radio/web"


def page_source(theme: str = "terminal") -> str:
    def file(url):
        return (WEB / url.removeprefix("/ui/")).read_text()
    html = (WEB / "themes" / theme / "index.html").read_text()
    html = re.sub(r'<link rel="stylesheet" href="([^"]+)">', lambda m: f"<style>\n{file(m[1])}</style>", html)
    return re.sub(r'<script src="([^"]+)"></script>', lambda m: f"<script>\n{file(m[1])}</script>", html)


def core_source() -> str:
    """The page core and model (radio/web/core), for harnesses that run theme code on top of them."""
    return (WEB / "core/core.js").read_text() + "\n" + (WEB / "core/model.js").read_text()


def theme_source(theme: str = "terminal") -> str:
    """The theme's own script, for slicing a theme function out by name (the core has functions of the same name)."""
    return (WEB / "themes" / theme / "view.js").read_text()
