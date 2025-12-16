#!/usr/bin/env python3
"""
Generate a terminal-style SVG stats card for GitHub profile README.

Environment:
- GH_TOKEN: GitHub token (PAT recommended) or fallback to GITHUB_TOKEN
- GH_USER: GitHub username (defaults to repo owner in workflow)
"""

from __future__ import annotations

import datetime as dt
import html
import os
from typing import Any, Dict, Optional

import requests


GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        raise RuntimeError(f"Missing environment variable: {name}")
    return value.strip()


def gh_graphql(token: str, query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    resp = requests.post(
        GITHUB_GRAPHQL_URL,
        headers=headers,
        json={"query": query, "variables": variables},
        timeout=25,
    )
    resp.raise_for_status()
    payload = resp.json()
    if isinstance(payload, dict) and payload.get("errors"):
        raise RuntimeError(f"GitHub GraphQL errors: {payload['errors']}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("Unexpected GitHub GraphQL response shape.")
    return data


def fetch_owned_repo_count_and_stars(token: str, login: str) -> Dict[str, int]:
    query = """
    query($login: String!, $cursor: String) {
      user(login: $login) {
        repositories(first: 100, after: $cursor, ownerAffiliations: [OWNER]) {
          totalCount
          nodes { stargazerCount }
          pageInfo { hasNextPage endCursor }
        }
      }
    }
    """
    cursor: Optional[str] = None
    total_count = 0
    total_stars = 0

    while True:
        data = gh_graphql(token, query, {"login": login, "cursor": cursor})
        repos = data["user"]["repositories"]
        total_count = int(repos["totalCount"])
        nodes = repos.get("nodes") or []
        total_stars += sum(int(n["stargazerCount"]) for n in nodes)

        page = repos["pageInfo"]
        if page["hasNextPage"] is True and page["endCursor"]:
            cursor = str(page["endCursor"])
            continue
        break

    return {"repos_owned": total_count, "stars_owned": total_stars}


def fetch_other_stats(token: str, login: str) -> Dict[str, int]:
    now = dt.datetime.now(dt.timezone.utc)
    start = dt.datetime(now.year, 1, 1, tzinfo=dt.timezone.utc)

    query = """
    query($login: String!, $from: DateTime!, $to: DateTime!) {
      user(login: $login) {
        followers { totalCount }
        repositoriesContributedTo(first: 1) { totalCount }
        contributionsCollection(from: $from, to: $to) {
          contributionCalendar { totalContributions }
        }
      }
    }
    """
    data = gh_graphql(token, query, {"login": login, "from": start.isoformat(), "to": now.isoformat()})
    user = data["user"]

    followers = int(user["followers"]["totalCount"])
    contrib_repos = int(user["repositoriesContributedTo"]["totalCount"])
    contrib_ytd = int(user["contributionsCollection"]["contributionCalendar"]["totalContributions"])

    return {
        "followers": followers,
        "contrib_repos": contrib_repos,
        "contrib_ytd": contrib_ytd,
    }


def row(label: str, value: str, width: int = 54) -> str:
    left = f"{label}: "
    value_str = value
    # keep at least " . " spacing
    dots_len = max(1, width - len(left) - len(value_str) - 1)
    return f"{left}{'.' * dots_len} {value_str}"


def render_svg(lines: list[str], theme: str) -> str:
    # Theme colors
    if theme == "dark":
        bg = "#0d1117"
        fg = "#c9d1d9"
        dim = "#8b949e"
        border = "#30363d"
    else:
        bg = "#ffffff"
        fg = "#24292f"
        dim = "#57606a"
        border = "#d0d7de"

    padding_x = 18
    padding_y = 18
    font_size = 14
    line_height = 18
    width_px = 640
    height_px = padding_y * 2 + line_height * (len(lines) + 1)

    # Use tspans per line to guarantee line breaks.
    tspan_lines = []
    first_y = padding_y + font_size
    for idx, line in enumerate(lines):
        esc = html.escape(line)
        if idx == 0:
            tspan_lines.append(f'<tspan x="{padding_x}" y="{first_y}">{esc}</tspan>')
        else:
            tspan_lines.append(f'<tspan x="{padding_x}" dy="{line_height}">{esc}</tspan>')

    tspans = "\n      ".join(tspan_lines)

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width_px}" height="{height_px}" viewBox="0 0 {width_px} {height_px}">
  <rect x="1" y="1" width="{width_px - 2}" height="{height_px - 2}" rx="16" fill="{bg}" stroke="{border}" />
  <text xml:space="preserve"
        style="font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;
               font-size: {font_size}px;
               fill: {fg};">
      {tspans}
  </text>
</svg>
"""


def main() -> None:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token is None or token.strip() == "":
        raise RuntimeError("Missing GH_TOKEN (recommended) or GITHUB_TOKEN.")

    login = os.environ.get("GH_USER") or _require_env("GITHUB_REPOSITORY_OWNER")

    owned = fetch_owned_repo_count_and_stars(token.strip(), login)
    other = fetch_other_stats(token.strip(), login)

    # --- Customize these (static fields) ---
    os_line = "Windows, Linux"
    ide_line = "VS Code"
    prog_langs = "C, C++, TypeScript, Python, Java, C#, Bash"
    markup_langs = "HTML, CSS, SQL"
    human_langs = "Turkish, English"
    # --------------------------------------

    header = f"{login} --------------------------------------------------"
    lines = [
        header,
        "",
        row("OS", os_line),
        row("IDE", ide_line),
        "",
        row("Langs (prog)", prog_langs),
        row("Langs (other)", markup_langs),
        row("Langs (human)", human_langs),
        "",
        "GitHub Stats ------------------------------------------",
        row("Repos (owned)", f"{owned['repos_owned']:,}"),
        row("Stars (owned)", f"{owned['stars_owned']:,}"),
        row("Followers", f"{other['followers']:,}"),
        row("Contrib (YTD)", f"{other['contrib_ytd']:,}"),
        row("Contrib repos", f"{other['contrib_repos']:,}"),
    ]

    os.makedirs("assets", exist_ok=True)
    with open("assets/stats_dark.svg", "w", encoding="utf-8") as f:
        f.write(render_svg(lines, "dark"))
    with open("assets/stats_light.svg", "w", encoding="utf-8") as f:
        f.write(render_svg(lines, "light"))


if __name__ == "__main__":
    main()
