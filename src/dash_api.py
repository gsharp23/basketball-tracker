"""The shared JSON API of the dashboard, and THE CONTRACT for the screen modules.

dashboard.py includes this router and one router per screen. Everything here is used by
more than one screen; a screen's own calls live in its own module.

Routes in this module (all under /api)
    GET  /api/health                 {"ok", "card", "busy"}: a quick "is it alive"
    GET  /api/branding               school name, mascot, logo/hero URLs, colours (neutral when no branding folder)
    GET  /api/games                  {"games": [...], "chosen": video, "busy": words}; each game as dash_common.list_games()
    GET  /api/game/chosen            {"video": "clips/test.mp4" or null}
    PUT  /api/game/chosen            body {"video": "clips/test.mp4"}  (remembered in data/_dashboard/chosen_game.json)
    GET  /api/settings               the settings (dash_common.DEFAULT_SETTINGS for the keys) and "court_sizes"
    PUT  /api/settings               body: any of those keys; returns all of them
    GET  /api/storage                {"attached", "folder" (as Windows names it), "drive", "used", "free", "total"} (bytes)
    GET  /api/roster                 {"home": {"name", "players": {"12": "Name"}}, "away": {...}}
    PUT  /api/roster                 the same shape; checked and saved to roster.json at the project root
    POST /api/jobs/start             body {"kind", "video", "options": []} -> {"name"}; 409 with a plain message when refused
    GET  /api/jobs                   every job this dashboard started
    GET  /api/jobs/{name}            dash_common.job_info(): "running", "starting", "exit_code", "lines",
                                     "progress" {step, step_number, step_count, how_far, left, detail, ended},
                                     "address" (a click page's own address), "flagged" (NOTE:/WARNING: lines)
    POST /api/jobs/{name}/stop       stop it the way Ctrl+C would

================================  CONTRACT FOR THE SCREEN MODULES  ================================

MODULES. One screen = one router module + one html/js pair; nothing else is touched.
    src/api_games.py    -> web/games.html + games.js      (and game.html + game.js: the results)
    src/api_shots.py    -> web/shots.html + shots.js
    src/api_train.py    -> web/train.html + train.js
    src/api_opponent.py -> web/opponent.html + opponent.js
    src/api_settings.py -> web/settings.html + settings.js       (done by the foundation)
  A module defines  router = APIRouter(prefix="/api/<area>")  and dashboard.py includes it.
  Use dash_common for games, jobs, settings, roster and branding; the logic modules
  (shots.py, ball_labels.py, stats.py, tactical_view.py) for the real work. Never import gradio.
  Errors the coach can cause: raise fastapi.HTTPException(400 or 409, detail="plain words");
  app.js shows `detail` as a toast. Write every JSON file with config.save_json.

THE CURRENT GAME. A screen's URL carries it: games.html?game=test (the stem, without "clips/").
  In JS:  const game = await currentGame();   -> {"video": "clips/test.mp4", "name": "test"} or null
  It reads ?game= first, else GET /api/game/chosen; when it came from the URL it also PUTs it as the
  remembered choice. Links to another screen keep it: href = withGame('shots.html').
  API calls take the video as main.py does: ?video=clips/test.mp4 (or in the JSON body).

FILES. The browser reads the data folders' files at  /files/<folder>/<path>  with <folder> one of
  outputs, crops, clips, data, branding; the path is inside that folder, for example
      /files/outputs/test/tracked.mp4        /files/clips/test.mp4 (videos support seeking: HTTP Range)
      /files/outputs/test/scoring_heat_map_white.png        /files/branding/logo.png
  In JS: fileUrl('outputs', 'test/tracked.mp4'). Nothing outside those folders is ever served.

JOBS. To run a long program:  POST /api/jobs/start {"kind": "run", "video": "clips/game1.mp4"}
  Kinds (dash_common.JOB_KINDS): "run" (main.py; options --force, --from <stage>), "court_pictures",
  "court_landmarks", "hoops" (the three click pages; one open at a time, job name "click_page").
  A router may add a kind:  dash_common.JOB_KINDS["pick_frames"] = {"script": "...py", "heavy": False,
  "name": "pick_{stem}", "options": []}  (the script is run as  python src/<script> --video <video> options).
  Then poll:  const stop = pollJob('run_game1', info => {...}, 2000);  // every 2 s until info.running is false
  A click page's own address is info.address: open it in a new tab (window.open) when it appears.
  GET /api/jobs/run_<stem> also answers for a run started in a terminal (from the game's run_log.txt).

HEADER. Every screen's html has, right after <body>:
      <header class="site-header" id="site-header"></header>
  and its js calls  renderHeader('games')  ('games' | 'shots' | 'train' | 'settings' marks the active link).
  The header draws the logo from /api/branding and the nav; the nav links keep ?game=.
  Page body:  <main class="page"> ... </main>; the page title row is .page-head with an <h1 class="title">.

CSS CLASSES (web/app.css; the mockup's look). Cards: .card (white, 14px radius, 20px padding; .card-pad24 for
  24px), .card-title (the h2 inside), .card-row (title + something on the right). Grids: .grid-cards (games
  cards), .grid-tiles (stat tiles: .tile, .tile-dark, .tile-label, .tile-value), .two-col (3:2 flex-wrap).
  Buttons: .btn plus .btn-red | .btn-dark | .btn-outline | .btn-light (grey) | .btn-green | .btn-link,
  size .btn-big (52px) and .btn-tall (64px); .btn-icon for a round icon button. Chips: .chip plus
  .chip-green | .chip-blue | .chip-red | .chip-grey | .chip-dark. Progress: <div class="progress"><span
  style="width:57%"></span></div> with .progress-blue | .progress-green | .progress-red | .progress-dark.
  Segmented choice: .segment with buttons/links, the chosen one .on. Tables: .table-wrap > table.table.
  Forms: .field > label + input/select, .check (a checkbox row). Text: .muted, .small, .hint, .big-number.
  Status dot: .dot .dot-green | .dot-grey | .dot-red. Toasts come from app.js: toast('text', 'bad'|'good'|'').

FORMATTING (app.js): fmtClock(sec) -> "17:42", fmtLength(sec) -> "52 min" / "20 sec", fmtFeet(ft),
  fmtPercent(x) -> "21%", fmtBytes(n) -> "9 GB", esc(text) (HTML-escape; use it on anything from a file),
  el(tag, attrs, children) (make elements), api(path, options) / getJSON / putJSON / postJSON.
  Say when a number is provisional: a game with stand_in_court true was marked with stand-in clicks.
=====================================================================================================
"""
from fastapi import APIRouter, HTTPException

import config
import dash_common

router = APIRouter(prefix="/api")

PLAIN_TITLE = "Hoops Tracker"


@router.get("/health")
def health():
    return {"ok": True, "card": dash_common.sd_card_ok(), "busy": dash_common.what_is_running()}


@router.get("/branding")
def branding():
    """School name, mascot, the logo and artwork as /files URLs, and the logo's colours."""
    brand = dash_common.load_branding()
    colours = dash_common.brand_colours(brand["logo"])
    return {"school_name": brand["school_name"], "mascot": brand["mascot"], "title": PLAIN_TITLE,
            "logo_url": f"/files/branding/{brand['logo'].name}" if brand["logo"] else None,
            "hero_url": f"/files/branding/{brand['hero'].name}" if brand["hero"] else None,
            "colours": dict(colours, on_accent=dash_common.text_on(colours["accent"]))}


@router.get("/games")
def games():
    try:
        listed = dash_common.list_games()
    except OSError:
        raise HTTPException(503, "The data folders cannot be read. See \"If a data folder cannot be reached\" in README.md.")
    return {"games": listed, "chosen": dash_common.chosen_game(), "busy": dash_common.what_is_running()}


@router.get("/game/chosen")
def chosen():
    return {"video": dash_common.chosen_game()}


@router.put("/game/chosen")
def choose(body: dict):
    video = str(body.get("video") or "")
    if not dash_common.is_clip_video(video):
        raise HTTPException(400, "That game is not in the clips folder any more.")
    dash_common.remember_game(video)
    return {"video": video}


def settings_reply():
    sizes = {key: {"label": label, "length_ft": length, "width_ft": width}
             for key, (label, length, width) in dash_common.COURT_SIZES.items()}
    return dict(dash_common.load_settings(), court_sizes=sizes)


@router.get("/settings")
def settings():
    return settings_reply()


@router.put("/settings")
def save_settings(body: dict):
    try:
        dash_common.save_settings(body)
    except ValueError as problem:
        raise HTTPException(400, str(problem))
    return settings_reply()


@router.get("/storage")
def storage():
    return dash_common.storage_info()


@router.get("/roster")
def roster():
    return dash_common.load_roster()


@router.put("/roster")
def save_roster(body: dict):
    try:
        return dash_common.save_roster(body)
    except ValueError as problem:
        raise HTTPException(400, str(problem))


@router.post("/jobs/start")
def start_job(body: dict):
    kind, video = str(body.get("kind") or ""), str(body.get("video") or "")
    options = body.get("options") or []
    if kind == "run" and dash_common.load_settings()["run_where"] == "colab" and body.get("where") != "laptop":
        raise HTTPException(409, "Running on Colab is coming next. Choose \"Run here\" to process it on this laptop.")
    try:
        name = dash_common.start_job(kind, video, options)
    except ValueError as problem:
        raise HTTPException(409, str(problem))
    return {"name": name}


@router.get("/jobs")
def jobs():
    return dash_common.all_jobs()


@router.get("/jobs/{name}")
def job(name: str):
    if not name.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(400, "That is not a job name.")
    return dash_common.job_info(name)


@router.post("/jobs/{name}/stop")
def stop_job(name: str):
    if not dash_common.job_running(name):
        return {"name": name, "running": False, "message": "That job was not running."}
    dash_common.stop_job(name, wait=60)         # a click page can take half a minute to shut down
    return {"name": name, "running": dash_common.job_running(name)}
