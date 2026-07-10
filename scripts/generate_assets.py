#!/usr/bin/env python3
"""
Generates the Game Boy-styled banner and live stats "cartridge" for the
GitHub profile README. Run locally or from the update-tracker workflow.

Usage:
    python scripts/generate_assets.py --username leodah20 --token $GITHUB_TOKEN
"""
import argparse
import os
import sys
from datetime import datetime, timezone

import requests
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FONT_PATH = os.path.join(ROOT, "assets", "fonts", "PressStart2P.ttf")
OUT_DIR = os.path.join(ROOT, "assets")

# Classic Game Boy (DMG) 4-shade palette
GB_LIGHTEST = "#9bbc0f"
GB_LIGHT = "#8bac0f"
GB_DARK = "#306230"
GB_DARKEST = "#0f380f"
GB_BEZEL = "#8f9490"
GB_BEZEL_DARK = "#5a5f5c"


def font(size):
    return ImageFont.truetype(FONT_PATH, size)


def text_w(draw, text, f):
    bbox = draw.textbbox((0, 0), text, font=f)
    return bbox[2] - bbox[0]


def pixel_rect(draw, xy, fill):
    """Draws a rectangle without anti-aliasing artifacts (crisp pixel edges)."""
    draw.rectangle(xy, fill=fill)


# ---------------------------------------------------------------------------
# GitHub data
# ---------------------------------------------------------------------------

def gh_headers(token):
    h = {"Accept": "application/vnd.github+json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def fetch_profile(username, token):
    r = requests.get(f"https://api.github.com/users/{username}", headers=gh_headers(token), timeout=20)
    r.raise_for_status()
    return r.json()


def fetch_repo_stats(username, token):
    repos = []
    page = 1
    while True:
        r = requests.get(
            f"https://api.github.com/users/{username}/repos",
            params={"per_page": 100, "page": page, "type": "owner"},
            headers=gh_headers(token),
            timeout=20,
        )
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        repos.extend(batch)
        page += 1
        if page > 5:
            break

    total_stars = sum(repo.get("stargazers_count", 0) for repo in repos)

    lang_bytes = {}
    for repo in repos:
        if repo.get("fork"):
            continue
        try:
            lr = requests.get(repo["languages_url"], headers=gh_headers(token), timeout=20)
            lr.raise_for_status()
            for lang, n in lr.json().items():
                lang_bytes[lang] = lang_bytes.get(lang, 0) + n
        except requests.RequestException:
            continue

    total_bytes = sum(lang_bytes.values()) or 1
    top_langs = sorted(lang_bytes.items(), key=lambda kv: kv[1], reverse=True)[:4]
    top_langs = [(lang, round(100 * n / total_bytes)) for lang, n in top_langs]

    return {
        "public_repos": len(repos),
        "total_stars": total_stars,
        "top_langs": top_langs,
    }


CONTRIB_QUERY = """
query($login: String!) {
  user(login: $login) {
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays {
            date
            contributionCount
          }
        }
      }
    }
  }
}
"""


def fetch_contributions(username, token):
    if not token:
        return {"total": 0, "streak": 0}
    r = requests.post(
        "https://api.github.com/graphql",
        json={"query": CONTRIB_QUERY, "variables": {"login": username}},
        headers=gh_headers(token),
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    cal = data["data"]["user"]["contributionsCollection"]["contributionCalendar"]
    days = [d for week in cal["weeks"] for d in week["contributionDays"]]

    today = datetime.now(timezone.utc).date().isoformat()
    streak = 0
    for day in reversed(days):
        if day["date"] == today and day["contributionCount"] == 0:
            continue
        if day["contributionCount"] > 0:
            streak += 1
        else:
            break

    return {"total": cal["totalContributions"], "streak": streak}


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def build_tracker(profile, repo_stats, contrib, out_path):
    W, H = 760, 380
    img = Image.new("RGB", (W, H), GB_DARKEST)
    draw = ImageDraw.Draw(img)

    # Screen area (GB-light background), with a dark bezel border.
    bezel = 14
    pixel_rect(draw, [0, 0, W, H], GB_BEZEL_DARK)
    pixel_rect(draw, [bezel, bezel, W - bezel, H - bezel], GB_LIGHTEST)

    f_title = font(16)
    f_label = font(10)
    f_big = font(22)
    f_small = font(9)

    pad = bezel + 18
    y = pad

    draw.text((pad, y), "LEODAH20.GB", font=f_title, fill=GB_DARKEST)
    y += 34
    pixel_rect(draw, [pad, y, W - pad, y + 3], GB_DARK)
    y += 22

    # Stat row: repos / stars / followers
    stats_row = [
        ("REPOS", str(repo_stats["public_repos"])),
        ("STARS", str(repo_stats["total_stars"])),
        ("FOLLOWERS", str(profile.get("followers", 0))),
    ]
    col_w = (W - 2 * pad) // 3
    for i, (label, value) in enumerate(stats_row):
        x = pad + i * col_w
        draw.text((x, y), label, font=f_small, fill=GB_DARK)
        draw.text((x, y + 16), value, font=f_big, fill=GB_DARKEST)
    y += 60

    # Contributions
    draw.text((pad, y), "CONTRIBUTIONS (1Y)", font=f_small, fill=GB_DARK)
    y += 16
    draw.text((pad, y), str(contrib["total"]), font=f_big, fill=GB_DARKEST)
    draw.text((pad + 220, y + 6), f"STREAK {contrib['streak']}D", font=f_label, fill=GB_DARK)
    y += 46

    # Top languages as pixel bars
    draw.text((pad, y), "TOP LANGUAGES", font=f_small, fill=GB_DARK)
    y += 20
    bar_x = pad + 130
    bar_max_w = (W - pad) - bar_x - 44
    for lang, pct in repo_stats["top_langs"] or [("N/A", 0)]:
        draw.text((pad, y), lang[:10].upper(), font=f_small, fill=GB_DARKEST)
        pixel_rect(draw, [bar_x, y + 2, bar_x + bar_max_w, y + 10], GB_LIGHT)
        fill_w = int(bar_max_w * pct / 100)
        if fill_w > 0:
            pixel_rect(draw, [bar_x, y + 2, bar_x + fill_w, y + 10], GB_DARKEST)
        draw.text((bar_x + bar_max_w + 8, y - 2), f"{pct}%", font=f_small, fill=GB_DARK)
        y += 22

    # Footer: last updated timestamp (UTC)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    draw.text((pad, H - bezel - 20), f"LAST SYNC {ts}", font=f_small, fill=GB_DARK)

    img.save(out_path)
    print(f"wrote {out_path}")


def build_banner(profile, out_path):
    W, H = 760, 220
    img = Image.new("RGB", (W, H), GB_BEZEL_DARK)
    draw = ImageDraw.Draw(img)

    bezel = 14
    pixel_rect(draw, [bezel, bezel, W - bezel, H - bezel], GB_LIGHTEST)

    # scanline texture
    for yy in range(bezel, H - bezel, 4):
        pixel_rect(draw, [bezel, yy, W - bezel, yy + 1], GB_LIGHT)

    f_name = font(22)
    f_role = font(12)
    f_flavor = font(9)

    name = "LEONARDO CORDEIRO"
    role = "> ANALISTA DE REDES JR."
    flavor = "PRESS START TO VIEW PROFILE"

    draw.text(((W - text_w(draw, name, f_name)) // 2, 70), name, font=f_name, fill=GB_DARKEST)
    draw.text(((W - text_w(draw, role, f_role)) // 2, 108), role, font=f_role, fill=GB_DARK)
    draw.text(((W - text_w(draw, flavor, f_flavor)) // 2, 160), flavor, font=f_flavor, fill=GB_DARK)

    img.save(out_path)
    print(f"wrote {out_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", default="leodah20")
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""))
    parser.add_argument("--skip-banner", action="store_true", help="Only regenerate the tracker card")
    args = parser.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)

    profile = fetch_profile(args.username, args.token)
    repo_stats = fetch_repo_stats(args.username, args.token)
    contrib = fetch_contributions(args.username, args.token)

    build_tracker(profile, repo_stats, contrib, os.path.join(OUT_DIR, "tracker.png"))

    if not args.skip_banner:
        build_banner(profile, os.path.join(OUT_DIR, "banner.png"))


if __name__ == "__main__":
    try:
        main()
    except requests.RequestException as e:
        print(f"GitHub API error: {e}", file=sys.stderr)
        sys.exit(1)
