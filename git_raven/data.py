"""Fetch the activity that shapes the raven from the GitHub GraphQL API."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import subprocess
import urllib.request
from collections import Counter

QUERY = """
query($login: String!) {
  user(login: $login) {
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
    }
    repositories(first: 100, ownerAffiliations: OWNER, isFork: false,
                 orderBy: {field: PUSHED_AT, direction: DESC}) {
      nodes {
        stargazerCount
        languages(first: 5, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name color } }
        }
      }
    }
  }
}
"""

FALLBACK = (110, 118, 129)


def _token() -> str:
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        return tok
    try:
        return subprocess.check_output(["gh", "auth", "token"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        raise SystemExit("No token: set GITHUB_TOKEN or run `gh auth login`.")


def _hex(c: str | None, name: str):
    if c and c.startswith("#") and len(c) == 7:
        return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))
    if not name:
        return FALLBACK
    d = hashlib.sha256(name.encode()).digest()
    return (80 + d[0] % 150, 80 + d[1] % 150, 80 + d[2] % 150)


def fetch(login: str, today: dt.date | None = None) -> dict:
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": QUERY, "variables": {"login": login}}).encode(),
        headers={"Authorization": f"bearer {_token()}", "User-Agent": "git-raven"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.load(r)
    if "errors" in payload or not payload.get("data", {}).get("user"):
        raise SystemExit(f"GitHub API error: {payload.get('errors') or 'user not found'}")
    return summarize(payload["data"]["user"], today)


def summarize(user: dict, today: dt.date | None = None) -> dict:
    today = today or dt.datetime.now(dt.timezone.utc).date()
    cal = user["contributionsCollection"]["contributionCalendar"]
    days = {d["date"]: d["contributionCount"]
            for w in cal["weeks"] for d in w["contributionDays"]}

    yesterday = days.get((today - dt.timedelta(days=1)).isoformat(), 0)

    # streak: consecutive active days, allowing today to still be empty
    day = today if days.get(today.isoformat(), 0) else today - dt.timedelta(days=1)
    streak = 0
    while days.get(day.isoformat(), 0) > 0:
        streak += 1
        day -= dt.timedelta(days=1)

    langs, colors, stars = Counter(), {}, 0
    for repo in user["repositories"]["nodes"]:
        stars += repo["stargazerCount"]
        for e in repo["languages"]["edges"]:
            langs[e["node"]["name"]] += e["size"]
            colors[e["node"]["name"]] = e["node"]["color"]
    top = [n for n, _ in langs.most_common(2)]
    palette = [_hex(colors.get(n), n) for n in top]
    while len(palette) < 2:
        palette.append(palette[0] if palette else FALLBACK)

    return {
        "total": cal["totalContributions"],
        "streak": streak,
        "yesterday": yesterday,
        "stars": stars,
        "languages": top,
        "sheen": palette,
    }
