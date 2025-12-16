#!/usr/bin/env python3
"""
Generate a neofetch-like SVG card (dark/light) for GitHub profile README.

Dynamic:
- repos owned, contributed repos, stars owned
- commits authored by the user (scanned + cached)
- LOC (add/del/net) authored by the user (scanned + cached)
- followers

Static (as requested):
- Status: Running (stable)
- Tools / Hobbies
- Contact: timezone, email, website, LinkedIn
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import requests

GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"


@dataclass(frozen=True)
class Theme:
    bg: str
    border: str
    text: str
    key: str
    value: str
    dots: str
    green: str


DARK = Theme(
    bg="#0d1117",
    border="#30363d",
    text="#c9d1d9",
    key="#d2a8ff",
    value="#79c0ff",
    dots="#8b949e",
    green="#3fb950",
)

LIGHT = Theme(
    bg="#ffffff",
    border="#d0d7de",
    text="#24292f",
    key="#8250df",
    value="#0969da",
    dots="#57606a",
    green="#1a7f37",
)


def gh_graphql(token: str, query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    resp = requests.post(
        GITHUB_GRAPHQL_URL,
        headers=headers,
        json={"query": query, "variables": variables},
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    if isinstance(payload, dict) and payload.get("errors"):
        raise RuntimeError(f"GitHub GraphQL errors: {payload['errors']}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("Unexpected GitHub GraphQL response shape.")
    return data


def format_int(n: int) -> str:
    return f"{n:,}"


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def load_cache(cache_path: str) -> Dict[str, Any]:
    if not os.path.exists(cache_path):
        return {"repos": {}}
    with open(cache_path, "r", encoding="utf-8") as f:
        obj = json.load(f)
    if not isinstance(obj, dict):
        return {"repos": {}}
    if "repos" not in obj or not isinstance(obj["repos"], dict):
        obj["repos"] = {}
    return obj


def save_cache(cache_path: str, cache_obj: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    tmp = cache_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache_obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, cache_path)


def fetch_user_id_and_followers(token: str, login: str) -> Tuple[str, int]:
    query = """
    query($login: String!){
      user(login: $login) {
        id
        followers { totalCount }
      }
    }
    """
    data = gh_graphql(token, query, {"login": login})
    user = data["user"]
    return str(user["id"]), int(user["followers"]["totalCount"])


def fetch_contributed_repo_count(token: str, login: str) -> int:
    query = """
    query($login: String!){
      user(login: $login) {
        repositoriesContributedTo(first: 1) { totalCount }
      }
    }
    """
    data = gh_graphql(token, query, {"login": login})
    return int(data["user"]["repositoriesContributedTo"]["totalCount"])


def fetch_owned_repos_with_stars_and_commit_total(token: str, login: str) -> Tuple[int, int, Dict[str, int]]:
    """
    Returns:
    - owned repo totalCount
    - total stars across owned repos
    - mapping: nameWithOwner -> default branch commit history totalCount (0 if empty)
    """
    query = """
    query($login: String!, $cursor: String) {
      user(login: $login) {
        repositories(first: 60, after: $cursor, ownerAffiliations: [OWNER]) {
          totalCount
          nodes {
            nameWithOwner
            stargazerCount
            defaultBranchRef {
              target {
                ... on Commit {
                  history { totalCount }
                }
              }
            }
          }
          pageInfo { hasNextPage endCursor }
        }
      }
    }
    """
    cursor: Optional[str] = None
    owned_total = 0
    stars_total = 0
    repo_commit_totals: Dict[str, int] = {}

    while True:
        data = gh_graphql(token, query, {"login": login, "cursor": cursor})
        repos = data["user"]["repositories"]
        owned_total = int(repos["totalCount"])

        nodes = repos.get("nodes") or []
        for n in nodes:
            name = str(n["nameWithOwner"])
            stars_total += int(n["stargazerCount"])
            default_ref = n.get("defaultBranchRef")
            if default_ref is None:
                repo_commit_totals[name] = 0
                continue
            target = default_ref.get("target")
            if target is None or "history" not in target or target["history"] is None:
                repo_commit_totals[name] = 0
                continue
            repo_commit_totals[name] = int(target["history"]["totalCount"])

        page = repos["pageInfo"]
        if page["hasNextPage"] is True and page["endCursor"]:
            cursor = str(page["endCursor"])
            continue
        break

    return owned_total, stars_total, repo_commit_totals


def scan_repo_history_for_user_loc(
    token: str,
    owner: str,
    name: str,
    user_id: str,
) -> Tuple[int, int, int]:
    """
    Returns: (my_commits, additions, deletions) for commits authored by the user only.
    """
    query = """
    query($owner: String!, $name: String!, $cursor: String, $author_id: ID!) {
      repository(owner: $owner, name: $name) {
        defaultBranchRef {
          target {
            ... on Commit {
              history(first: 100, after: $cursor, author: { id: $author_id }) {
                edges {
                  node { additions deletions }
                }
                pageInfo { hasNextPage endCursor }
              }
            }
          }
        }
      }
    }
    """

    cursor: Optional[str] = None
    my_commits = 0
    additions = 0
    deletions = 0

    while True:
        data = gh_graphql(
            token,
            query,
            {"owner": owner, "name": name, "cursor": cursor, "author_id": user_id},
        )
        repo = data["repository"]
        default_ref = repo.get("defaultBranchRef")
        if default_ref is None:
            return 0, 0, 0

        target = default_ref.get("target")
        if target is None:
            return 0, 0, 0

        history = target.get("history")
        if history is None:
            return 0, 0, 0

        edges = history.get("edges") or []
        my_commits += len(edges)
        for e in edges:
            node = e["node"]
            additions += int(node.get("additions", 0))
            deletions += int(node.get("deletions", 0))

        page = history["pageInfo"]
        if page["hasNextPage"] is True and page["endCursor"]:
            cursor = str(page["endCursor"])
            continue
        break

    return my_commits, additions, deletions


def build_line_segments(label: str, value_plain: str, label_pad: int) -> Tuple[str, str]:
    """
    Returns: (left, value) as plain strings. No dots.
    Left side is padded with spaces so values align nicely.
    """
    left = f"· {label}:"
    left = left.ljust(label_pad)
    return left + " ", value_plain



def render_svg(lines: list[Dict[str, Any]], theme: Theme) -> str:
    """
    lines: list of dicts representing a single rendered line.
      Supported types:
        - {"type":"text", "label":..., "value":..., "valueSegments":[("text","class"), ...] optional}
        - {"type":"sep", "text":"- Contact -----------------------------------"}
        - {"type":"blank"}
    """
    label_pad = 0
    for line in lines:
        if line.get("type") == "text":
            label_pad = max(label_pad, len(f"· {line['label']}:"))
            label_pad += 2  # a little spacing before value
    font_size = 14
    line_height = 20
    padding_x = 18
    padding_y = 18
    width_px = 700
    height_px = padding_y * 2 + line_height * (len(lines) + 1)

    def esc(s: str) -> str:
        return html.escape(s)

    tspans: list[str] = []
    y0 = padding_y + font_size

    current_y = y0
    for i, line in enumerate(lines):
        tspan_parts: list[str] = []

        if line["type"] == "blank":
            current_y += line_height
            continue

        if line["type"] == "sep":
            tspan_parts.append(f'<tspan class="text">{esc(line["text"])}</tspan>')
        elif line["type"] == "text":
            left, value_plain = build_line_segments(line["label"], line["valuePlain"], label_pad)

            tspan_parts.append(f'<tspan class="key">{esc(left)}</tspan>')

            # value can be segmented for coloring (Status green dot)
            segs = line.get("valueSegments")
            if isinstance(segs, list) and len(segs) > 0:
                for seg_text, seg_class in segs:
                    tspan_parts.append(f'<tspan class="{esc(seg_class)}">{esc(seg_text)}</tspan>')
            else:
                tspan_parts.append(f'<tspan class="value">{esc(value_plain)}</tspan>')
        else:
            raise RuntimeError(f"Unknown line type: {line['type']}")

        joined = "".join(tspan_parts)
        tspans.append(f'<tspan x="{padding_x}" y="{current_y}">{joined}</tspan>')
        current_y += line_height

    style = f"""
    <style>
      text {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; font-size: {font_size}px; }}
      .text {{ fill: {theme.text}; }}
      .key {{ fill: {theme.key}; }}
      .value {{ fill: {theme.value}; }}
      .dots {{ fill: {theme.dots}; }}
      .green {{ fill: {theme.green}; }}
    </style>
    """

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width_px}" height="{height_px}" viewBox="0 0 {width_px} {height_px}">
  {style}
  <rect x="1" y="1" width="{width_px - 2}" height="{height_px - 2}" rx="16" fill="{theme.bg}" stroke="{theme.border}" />
  <text xml:space="preserve">
    {''.join(tspans)}
  </text>
</svg>
"""


def main() -> None:
    token = os.environ.get("GH_TOKEN")
    login = os.environ.get("GH_USER")

    if token is None or token.strip() == "":
        raise RuntimeError("Missing GH_TOKEN.")
    if login is None or login.strip() == "":
        raise RuntimeError("Missing GH_USER.")

    login = login.strip()
    token = token.strip()

    cache_path = os.path.join("cache", f"stats_{sha256_hex(login)[:16]}.json")
    cache_obj = load_cache(cache_path)

    user_id, followers = fetch_user_id_and_followers(token, login)
    contributed = fetch_contributed_repo_count(token, login)

    owned_total, stars_total, repo_commit_totals = fetch_owned_repos_with_stars_and_commit_total(token, login)

    # Scan commits/LOC for owned repos (cached per repo by commit_total)
    repos_cache: Dict[str, Any] = cache_obj["repos"]

    total_my_commits = 0
    total_add = 0
    total_del = 0

    for full_name, commit_total in repo_commit_totals.items():
        cached = repos_cache.get(full_name)
        if not isinstance(cached, dict) or int(cached.get("commit_total", -1)) != int(commit_total):
            owner, repo_name = full_name.split("/", 1)
            my_commits, adds, dels = scan_repo_history_for_user_loc(token, owner, repo_name, user_id)
            repos_cache[full_name] = {
                "commit_total": int(commit_total),
                "my_commits": int(my_commits),
                "add": int(adds),
                "del": int(dels),
            }

        total_my_commits += int(repos_cache[full_name].get("my_commits", 0))
        total_add += int(repos_cache[full_name].get("add", 0))
        total_del += int(repos_cache[full_name].get("del", 0))

    # Cleanup cache entries for repos that no longer exist
    for key in list(repos_cache.keys()):
        if key not in repo_commit_totals:
            del repos_cache[key]

    cache_obj["repos"] = repos_cache
    cache_obj["updated_at_unix"] = int(time.time())
    save_cache(cache_path, cache_obj)

    total_net = total_add - total_del

    # ---- Static content (your requirements) ----
    operating_systems = "Linux, Windows, macOS"
    progamming_languages = "C, C++, Python, Java, C#, JavaScript, TypeScript, Bash, PowerShell"
    other_languages = "HTML, CSS, SQL, YAML, JSON, XML"
    natural_languages = "Turkish (native), English (fluent), German (basic)"
    status_plain = "● Running (stable)"
    tools = "Docker, Kubernetes, Terraform, Grafana, Prometheus, Loki, Ansible, Jenkins, Helm, Kustomize, HAproxy, Keepalived, NGINX, Wireshark, Postman"
    hobbies = (
        "exploring new places and discovering new flavors, reading books, writing poetry, "
        "working out / staying active, scuba diving, fishing, painting / drawing, making music, "
        "watching films and documentaries, camping"
    )
    timezone = "TRT (UTC+3)"
    email = "me@necdetsanli.com"
    website = "necdetsanli.com"
    linkedin = "linkedin.com/in/necdetsanli"
    # -------------------------------------------

    header = f"github@{login} --------------------------------------------------"

    stats_line_1 = f"{format_int(owned_total)} {{Contributed: {format_int(contributed)}}} | Stars: {format_int(stars_total)}"
    stats_line_2 = f"{format_int(total_my_commits)} | Followers: {format_int(followers)}"
    stats_line_3 = f"{format_int(total_net)} ( {format_int(total_add)}++, {format_int(total_del)}-- )"

    lines = [
        {"type": "sep", "text": header},
        {"type": "blank"},
        {
            "type": "text",
            "label": "Status",
            "valuePlain": status_plain,
            "valueSegments": [("●", "green"), (" ", "text"), ("Running (stable)", "value")],
        },
        {"type": "text", "label": "Operating Systems", "valuePlain": operating_systems},
        {"type": "text", "label": "Programming Languages", "valuePlain": progamming_languages},
        {"type": "text", "label": "Other Languages", "valuePlain": other_languages},
        {"type": "text", "label": "Tools", "valuePlain": tools},
        {"type": "text", "label": "Natural Languages", "valuePlain": natural_languages},
        {"type": "text", "label": "Hobbies", "valuePlain": hobbies},
        {"type": "blank"},
        {"type": "sep", "text": "- Contact -------------------------------------------------------"},
        {"type": "text", "label": "Timezone", "valuePlain": timezone},
        {"type": "text", "label": "Email", "valuePlain": email},
        {"type": "text", "label": "Website", "valuePlain": website},
        {"type": "text", "label": "LinkedIn", "valuePlain": linkedin},
        {"type": "blank"},
        {"type": "sep", "text": "- GitHub Stats ---------------------------------------------------"},
        {"type": "text", "label": "Repos", "valuePlain": stats_line_1},
        {"type": "text", "label": "Commits", "valuePlain": stats_line_2},
        {"type": "text", "label": "Lines of Code", "valuePlain": stats_line_3},
    ]

    os.makedirs("assets", exist_ok=True)
    with open("assets/stats_dark.svg", "w", encoding="utf-8") as f:
        f.write(render_svg(lines, DARK))
    with open("assets/stats_light.svg", "w", encoding="utf-8") as f:
        f.write(render_svg(lines, LIGHT))


if __name__ == "__main__":
    main()
