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
from PIL import Image, ImageDraw, ImageFont, ImageChops, ImageFilter

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

# Neon-pixel palette for the contribution heatmap (arcade-cabinet vibe)
NEON_BG = "#080b09"
NEON_GRID = "#141d17"
NEON_TEXT = "#7cffb2"
NEON_LEVELS = ["#131a15", "#0b6b3a", "#12a44c", "#2bff7a", "#c8ffe0"]
MONTH_ABBR = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


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


def fetch_repos(username, token):
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
    return repos


def compute_repo_stats(repos, token):
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
        return {"total": 0, "streak": 0, "weeks": []}
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

    return {"total": cal["totalContributions"], "streak": streak, "weeks": cal["weeks"]}


# ---------------------------------------------------------------------------
# Project feed (markdown, injected into README.md between marker comments)
# ---------------------------------------------------------------------------

FEED_START = "<!-- FEED:START -->"
FEED_END = "<!-- FEED:END -->"


def relative_age(days):
    if days <= 0:
        return "hoje"
    if days == 1:
        return "ontem"
    if days < 30:
        return f"há {days}d"
    return f"há {days // 30}m"


def repo_status(days):
    if days <= 14:
        return "🟢 Em desenvolvimento"
    if days <= 90:
        return "🟡 Manutenção"
    return "⚪ Pausado"


def build_feed_markdown(repos, username, limit=8):
    now = datetime.now(timezone.utc)
    candidates = [
        r for r in repos
        if not r.get("fork") and not r.get("archived") and r["name"].lower() != username.lower()
    ]
    candidates.sort(key=lambda r: r["pushed_at"], reverse=True)
    candidates = candidates[:limit]

    lines = ["| Projeto | Linguagem | Última atividade | Status |", "|---|---|---|---|"]
    for r in candidates:
        pushed = datetime.strptime(r["pushed_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        days = (now - pushed).days
        name = f"[`{r['name']}`]({r['html_url']})"
        lang = r.get("language") or "—"
        lines.append(f"| {name} | {lang} | {relative_age(days)} | {repo_status(days)} |")

    ts = now.strftime("%Y-%m-%d %H:%M UTC")
    lines.append("")
    lines.append(f"<sub>Auto-gerado via API do GitHub pelo mesmo script do tracker acima — sem curadoria manual. Última sincronização: {ts}</sub>")
    return "\n".join(lines)


def update_readme_feed(feed_md, readme_path):
    with open(readme_path, "r", encoding="utf-8") as f:
        content = f.read()
    if FEED_START not in content or FEED_END not in content:
        print("README has no feed markers — skipping feed update", file=sys.stderr)
        return
    start = content.index(FEED_START) + len(FEED_START)
    end = content.index(FEED_END)
    content = content[:start] + "\n\n" + feed_md + "\n\n" + content[end:]
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"updated feed in {readme_path}")


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


def contrib_level(count):
    if count == 0:
        return 0
    if count <= 2:
        return 1
    if count <= 5:
        return 2
    if count <= 9:
        return 3
    return 4


def build_contrib_heatmap(weeks, total, streak, out_path):
    if not weeks:
        print("no contribution data (missing token?) — skipping contrib.png")
        return

    cell, gap = 11, 3
    pitch = cell + gap
    left_pad = 34
    top_pad = 46
    outer = 18
    legend_h = 26

    n_weeks = len(weeks)
    grid_w = n_weeks * pitch
    grid_h = 7 * pitch

    W = outer * 2 + left_pad + grid_w
    H = outer * 2 + top_pad + grid_h + legend_h

    img = Image.new("RGB", (W, H), NEON_BG)
    draw = ImageDraw.Draw(img)

    f_title = font(14)
    f_tiny = font(8)

    grid_x = outer + left_pad
    grid_y = outer + top_pad

    # ---- glow layer: bright squares for "lit" cells, blurred, screen-blended in
    glow = Image.new("RGB", (W, H), "#000000")
    glow_draw = ImageDraw.Draw(glow)

    day_labels = {1: "MON", 3: "WED", 5: "FRI"}
    last_month = None

    for wi, week in enumerate(weeks):
        x0 = grid_x + wi * pitch
        for di, day in enumerate(week["contributionDays"]):
            y0 = grid_y + di * pitch
            level = contrib_level(day["contributionCount"])
            color = NEON_LEVELS[level]

            if level >= 2:
                glow_draw.rectangle([x0, y0, x0 + cell, y0 + cell], fill=color)

            pixel_rect(draw, [x0, y0, x0 + cell, y0 + cell], color)

            # Month label: first week that contains the 1st of a new month
            day_date = datetime.strptime(day["date"], "%Y-%m-%d")
            if day_date.day <= 7 and day_date.month != last_month:
                last_month = day_date.month
                draw.text((x0, grid_y - 16), MONTH_ABBR[day_date.month - 1], font=f_tiny, fill=NEON_TEXT)

    glow = glow.filter(ImageFilter.GaussianBlur(radius=3))
    img = ImageChops.screen(img, glow)
    draw = ImageDraw.Draw(img)

    # Re-draw crisp cells on top of the glow (glow blur softens edges otherwise)
    for wi, week in enumerate(weeks):
        x0 = grid_x + wi * pitch
        for di, day in enumerate(week["contributionDays"]):
            y0 = grid_y + di * pitch
            level = contrib_level(day["contributionCount"])
            pixel_rect(draw, [x0, y0, x0 + cell, y0 + cell], NEON_LEVELS[level])

    for row, label in day_labels.items():
        draw.text((outer, grid_y + row * pitch - 1), label, font=f_tiny, fill=NEON_TEXT)

    # Title + live stats line
    draw.text((outer, outer), "CONTRIB.PRG", font=f_title, fill=NEON_TEXT)
    subtitle = f"{total} CONTRIBUTIONS IN THE LAST YEAR  ·  STREAK {streak}D"
    draw.text((outer, outer + 22), subtitle, font=f_tiny, fill="#3fae74")

    # Legend
    leg_y = grid_y + grid_h + 12
    draw.text((grid_x, leg_y), "LESS", font=f_tiny, fill="#3fae74")
    lx = grid_x + 40
    for lvl_color in NEON_LEVELS:
        pixel_rect(draw, [lx, leg_y - 1, lx + cell, leg_y - 1 + cell], lvl_color)
        lx += pitch
    draw.text((lx + 6, leg_y), "MORE", font=f_tiny, fill="#3fae74")

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
    repos = fetch_repos(args.username, args.token)
    repo_stats = compute_repo_stats(repos, args.token)
    contrib = fetch_contributions(args.username, args.token)

    build_tracker(profile, repo_stats, contrib, os.path.join(OUT_DIR, "tracker.png"))
    build_contrib_heatmap(contrib["weeks"], contrib["total"], contrib["streak"], os.path.join(OUT_DIR, "contrib.png"))

    if not args.skip_banner:
        build_banner(profile, os.path.join(OUT_DIR, "banner.png"))

    feed_md = build_feed_markdown(repos, args.username)
    update_readme_feed(feed_md, os.path.join(ROOT, "README.md"))


if __name__ == "__main__":
    try:
        main()
    except requests.RequestException as e:
        print(f"GitHub API error: {e}", file=sys.stderr)
        sys.exit(1)
