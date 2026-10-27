"""New repositories of the week: GitHub's official search API, repos created in the last N days, by stars.
No key needed: one request per hour against an unauthenticated limit of 10 per minute."""
import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from .github_trending import USER_AGENT, RateLimited

API = "https://api.github.com/search/repositories"


def fetch_new(days: int = 7, n: int = 20, timeout: int = 20) -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    url = f"{API}?q=created:%3E{since}&sort=stars&order=desc&per_page={n}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read(2_000_000))
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):  # GitHub signals the search rate limit with 403 as well
            raise RateLimited(float(e.headers.get("Retry-After") or 3600)) from e
        raise
    out = []
    for rank, r in enumerate(data.get("items", []), 1):
        owner, _, repo = (r.get("full_name") or "/").partition("/")
        if owner and repo and not r.get("fork") and not r.get("archived"):
            out.append({"owner": owner, "repo": repo, "description": (r.get("description") or "").strip(),
                        "language": r.get("language") or "", "stars_total": r.get("stargazers_count"),
                        "stars_today": None, "rank": rank})
            out[-1]['created_at'] = r.get('created_at')
    return out
