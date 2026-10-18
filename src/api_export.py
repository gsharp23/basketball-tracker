"""Export for the web: a read-only copy of the stats pages (Season, Teams, Players) that works with no server,
for GitHub Pages or any folder (web/season.html's "Export for the web").

    POST /api/export   body {"first_names_only": true}  -> {"path", "windows_path", "megabytes", "files", "readme"}

What goes into the folder (the data folder's `export/`, never the repo): the pages season, seasons, squad, teams,
team, players, player with app.css, app.js, team_parts.js, the favicon and the logo, a README.md with the
commands to publish, and under api/ one JSON file per reply those pages ask the dashboard for (the same
functions answer here, in-process). Every page gets `window.HOOPS_STATIC = true` first, which makes app.js read
those files and strip every control, edit link and link to a page that is not in the copy. Pictures of people
(thumbnails) and videos are never copied. "first_names_only" keeps the first word of every player's name.
"""
import datetime
import json
import re
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException

import api_seasons
import api_teams
import config
import dash_api
import dash_common
import library
import main as pipeline

router = APIRouter(prefix="/api/export")

PAGES = ["season", "seasons", "squad", "teams", "team", "players", "player"]
SHARED = ["app.css", "app.js", "team_parts.js", "game_rows.js"]
WEB = config.PROJECT_ROOT / "web"


def export_dir():
    """The data folder's neighbour `export/` (the data folder is a link into the data drive: resolved first, so the
    copy lands next to the real data, never in the code folder)."""
    return config.DATA_DIR.resolve().parent / "export"


def first_names(value, parent_key=None, under_players=False):
    """A copy of the reply with every player's name cut to its first word (names live under "players", "roster",
    "leaders" and "lines": a dict key "name" there, or the roster's {number: name} values)."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            here = under_players or key in ("players", "roster", "leaders", "lines", "us", "them")
            if key == "name" and isinstance(item, str) and under_players:
                out[key] = item.split()[0] if item.strip() else item
            elif key == "players" and isinstance(item, dict) and all(isinstance(v, str) for v in item.values()):
                out[key] = {number: (name.split()[0] if name.strip() else name) for number, name in item.items()}
            else:
                out[key] = first_names(item, key, here)
        return out
    if isinstance(value, list):
        return [first_names(item, parent_key, under_players) for item in value]
    return value


def scrub(value):
    """No pictures of people, no videos, no paths of this laptop in the copy."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key in ("thumbnail_url", "videos", "stats_csv", "clips_folder", "zip_path"):
                out[key] = None
            elif key in ("logo_url", "hero_url") and isinstance(item, str):
                out[key] = item.lstrip("/")
            else:
                out[key] = scrub(item)
        return out
    if isinstance(value, list):
        return [scrub(item) for item in value]
    return value


def write_reply(folder, path, reply, first_only):
    """api/<path with ? as __>.json"""
    target = folder / "api" / (path.replace("?", "__") + ".json")
    target.parent.mkdir(parents=True, exist_ok=True)
    reply = scrub(reply)
    if first_only:
        reply = first_names(reply)
    target.write_text(json.dumps(reply, ensure_ascii=False), encoding="utf-8")
    return target


def replies():
    """(path, reply) for everything the exported pages ask for."""
    out = [("branding", dash_api.branding()), ("seasons", dash_api.seasons()), ("teams", dash_api.teams())]
    for season_id in library.load_seasons()["seasons"]:
        out.append((f"seasons/{season_id}/summary", api_seasons.season_summary(season_id)))
        out.append((f"seasons/{season_id}/players", api_seasons.season_players(season_id)))
    for view in ("opponents", "ours", "all"):
        out.append((f"teams/library?view={view}", api_teams.teams_library(view)))
    for team_id in library.load_teams()["teams"]:
        try:
            out.append((f"teams/{team_id}/summary", api_teams.team_summary(team_id)))
        except HTTPException:
            continue
    return out


README = """# Hoops Tracker stats, the web copy

A read-only copy of the Season, Teams and Players pages, made by Hoops Tracker's "Export for the web" on {date}.
It needs no server: open `season.html` in a browser, or publish the folder.

## Publish it on GitHub Pages (three commands, once)

    cd "{folder}"
    git init && git add . && git commit -m "Hoops Tracker stats"
    gh repo create hoops-stats --public --source . --push

Then on GitHub: the repository's Settings, Pages, "Deploy from a branch", branch `main`, folder `/ (root)`, Save.
A minute later the site is at https://<your GitHub name>.github.io/hoops-stats/season.html

## After processing another game

In Hoops Tracker press "Export for the web" again (it rewrites this folder), then:

    cd "{folder}"
    git add -A && git commit -m "Stats update" && git push

## What is in it

The pages and their numbers as files under `api/` (one per reply the pages ask for). No videos, no pictures of
players, no edit controls. Names: {names}. The map on the Teams page fetches its tiles from OpenStreetMap when the page is open.
"""


@router.post("")
def export(body: dict):
    body = body if isinstance(body, dict) else {}
    first_only = bool(body.get("first_names_only", True))
    folder = export_dir()
    try:
        if folder.exists():
            shutil.rmtree(folder)
        (folder / "api").mkdir(parents=True)
        files = 0
        for name in PAGES:
            html = (WEB / f"{name}.html").read_text(encoding="utf-8")
            html = html.replace('href="/favicon.svg"', 'href="favicon.svg"')
            html = html.replace('<script src="app.js"></script>', '<script>window.HOOPS_STATIC = true;</script>\n<script src="app.js"></script>', 1)
            (folder / f"{name}.html").write_text(html, encoding="utf-8")
            shutil.copy2(WEB / f"{name}.js", folder / f"{name}.js")
            files += 2
        for name in SHARED:
            shutil.copy2(WEB / name, folder / name)
            files += 1
        try:
            import dashboard                     # the drawing the dashboard serves as /favicon.svg
            (folder / "favicon.svg").write_text(dashboard.FAVICON, encoding="utf-8")
        except Exception:
            pass
        brand = dash_common.load_branding()
        if brand.get("logo"):
            (folder / "files" / "branding").mkdir(parents=True, exist_ok=True)
            shutil.copy2(brand["logo"], folder / "files" / "branding" / Path(brand["logo"]).name)
            files += 1
        for path, reply in replies():
            write_reply(folder, path, reply, first_only)
            files += 1
        (folder / "index.html").write_text('<!doctype html><meta http-equiv="refresh" content="0; url=season.html">', encoding="utf-8")
        readme = README.format(date=datetime.date.today().isoformat(), folder=pipeline.windows_path(folder),
                               names="first names and numbers only" if first_only else "full names, as on the roster")
        (folder / "README.md").write_text(readme, encoding="utf-8")
        files += 2
    except (OSError, library.Problem) as problem:
        raise HTTPException(500, f"The export stopped: {problem}")
    size = sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())
    return {"path": str(folder), "windows_path": pipeline.windows_path(folder), "megabytes": round(size / 1e6, 1), "files": files,
            "first_names_only": first_only, "open": pipeline.windows_path(folder / "season.html")}
