"""The shared JSON API of the dashboard, and THE CONTRACT for the v3 screen modules.

dashboard.py includes this router first, then api_settings.py, then one router per screen
(the seven modules listed under MODULES, each only if its file exists). Everything here is
used by more than one screen; a screen's own calls live in its own module.

Routes in this module (all under /api)
    GET  /api/health                 {"ok", "card", "busy"}: a quick "is it alive"
    GET  /api/branding               school name, mascot, logo/hero URLs, colours (neutral when no branding folder)
    GET  /api/games                  {"games": [library.list_games() rows, each with "info" and "season_id"],
                                      "chosen": video, "busy": words, "seasons": {id: name}, "teams": {id: {name, short, colour}}}
    PUT  /api/games/{stem}/info      body: any of season_id ("unassigned" or null = none), opponent_id, opponent (a name:
                                     a new name makes the team), date, home, our_team, score_us, score_them,
                                     processed_on, conditions {gym, light, camera} -> the whole game_info
    GET  /api/game/chosen            {"video": "clips/test.mp4" or null}   (the game the coach worked on last)
    PUT  /api/game/chosen            body {"video": "clips/test.mp4"}  (remembered in data/_dashboard/chosen_game.json)
    GET  /api/seasons                {"seasons": [{"id", "name", "start", "current", "roster", "games"}], "current": id}
    POST /api/seasons                body {"name", "start"?, "roster"?, "current"?} -> {"id", ...season}
    PUT  /api/seasons/{id}           body: any of name, start, current (true), roster {name, players {number: name}}
    DELETE /api/seasons/{id}         not the current one; its games become Unassigned
    POST /api/seasons/{id}/schedule  a scheduled game: {"date", "time"?, "opponent" (name) or "opponent_id", "home"?,
                                     "score_us"?, "score_them"?, "kind"? (league | non-league | tournament), "note"?,
                                     "stem"? (link a video)} -> the fixture {"id", ...}
    PUT  /api/seasons/{id}/schedule/{fixture_id}   any of those fields; {"stem": "<video>"} links a video, null unlinks
    DELETE /api/seasons/{id}/schedule/{fixture_id}  (a linked video stays in the season, unscheduled)
    POST /api/seasons/{id}/schedule/import          {"rows": [fixture fields...]} -> {"added", "skipped"}
    GET  /api/teams                  {"teams": [{"id", "name", "short", "colour", "notes", "games"}]}
    POST /api/teams                  body {"name", "short"?, "colour"?} -> {"id", ...team}
    PUT  /api/teams/{id}             body: any of name, short, colour, notes {number: text; "" removes}, place, lat, lon,
                                     links [{label, url}], info {record, points_for, points_against, coach, level, source},
                                     roster {number: name}
    PUT  /api/teams/home             {"place", "lat", "lon"}: where our own gym is (the map's home marker)
    GET  /api/play-types             {"offense": [...], "defense": [...]}
    PUT  /api/play-types             the same shape; checked and saved to data/_dashboard/play_types.json
    GET  /api/settings               the settings (dash_common.DEFAULT_SETTINGS for the keys) plus "court_sizes",
                                     "models" (dash_common.model_files()) and "drive" (the folder check)
    PUT  /api/settings               body: any of those keys; returns the same as GET. drive_folder_path must be a
                                     folder on this computer (or ""), detector_model a .pt file in models/
    GET  /api/settings/drive-folder?path=   look at a Drive folder path without saving it: {"ok", "message", "entries", ...}
    GET  /api/storage                {"attached", "folder" (as Windows names it), "drive", "used", "free", "total"} (bytes)
    POST /api/jobs/start             body {"kind", "video", "options": []} -> {"name"}; 409 with a plain message when refused
    GET  /api/jobs                   every job this dashboard started
    GET  /api/jobs/{name}            dash_common.job_info(): "running", "starting", "exit_code", "lines",
                                     "progress" {step, step_number, step_count, how_far, left, detail, ended},
                                     "address" (a click page's own address), "flagged" (NOTE:/WARNING: lines)
    POST /api/jobs/{name}/stop       stop it the way Ctrl+C would

================================  CONTRACT FOR THE SCREEN MODULES (v3)  ================================

TWO AREAS, ONE DOOR. index.html is the start page and the only link between the two areas.
  Game analysis  (body class "area-analysis": dark header, white cards, red accent)
  Model training (body class "area-training": charcoal sidebar, white content, yellow accent)
  Inside an area the header/sidebar has "← Start page" and NO link to the other area. Never add one.

MODULES. One screen module = one router + its html/js pages. A module never imports another screen module
(api_*.py); it imports library.py, dash_common.py, config.py and the logic modules (shots.py, possessions.py,
training_data.py, training_versions.py, drive_link.py, stats.py, court_model.py...). dashboard.py includes each of these seven if its file exists:
    src/api_seasons.py   -> web/seasons.html (seasons library), season.html (one season), games.html (every game, grouped
                            by season, "Add a game", run here / on Colab), players.html (season totals per player, a
                            player's season shot map)                                        [area-analysis]
    src/api_teams.py     -> web/teams.html (teams library), team.html (one team: tendencies, their players, games
                            against them, the printable scouting report)                     [area-analysis]
    src/api_game.py      -> web/game.html (one game: video + possession timeline, live court, score tiles, players,
                            heat map; also the court set-up and processing panels)           [area-analysis]
    src/api_tag.py       -> web/tag.html ("Tag shots and plays": shots and possessions on one page; the
                            play types from GET /api/play-types)                             [area-analysis]
    src/api_training.py  -> web/train.html (overview, TrainHome), upload.html (1 · Upload: frames from chosen games with
                            condition tags), annotate.html (2 · Annotate), dataset.html (3 · Dataset)   [area-training]
    src/api_models.py    -> web/models.html (4 · Train and 5 · Models: train on Colab, score by version, models table,
                            "Use this one" writes settings.detector_model, "Try on a clip")  [area-training]
    src/api_drive.py     -> the Google Drive folder (settings.drive_folder_path): send a game or a dataset zip to the
                            inbox, read the outbox status.json, bring results back. Routes only, used by games.html
                            and models.html; a page of its own (drive.html) only if it needs one.
  Foundation (do not edit): dashboard.py, dash_api.py, dash_common.py, library.py, api_settings.py, app.css,
  app.js, index.html/.js, settings.html/.js. Need a shared change? Ask, do not copy.
  A module defines  router = APIRouter(prefix="/api/<name>")  with <name> the part after "api_" (api_seasons ->
  /api/seasons, api_game -> /api/game ...). dash_api.py owns the exact paths listed above (GET/POST /api/seasons,
  PUT/DELETE /api/seasons/{id}, GET/POST /api/teams, PUT /api/teams/{id}, GET /api/games, PUT /api/games/{stem}/info,
  GET/PUT /api/game/chosen): add sub-paths beside them (GET /api/seasons/{id}/summary, GET /api/game/{stem}/moments...),
  never those. Errors the coach can cause: raise fastapi.HTTPException(400 or 409, detail="plain words"); a
  library.Problem is a 400 with its text; app.js shows `detail` as a toast. Write every JSON file with
  config.save_json. Never import gradio. Anything that loads a YOLO model or EasyOCR runs as a job under
  `flock /tmp/hoops-heavy.lock` (dash_common.JOB_KINDS "heavy": True) and never on game1/game2 while testing.

THE DATA MODEL is library.py's docstring: data/_library/seasons.json, data/_library/teams.json,
data/<stem>/game_info.json (INFO_DEFAULTS), data/_dashboard/play_types.json. Use its functions, do not reread
the files yourself:
    load_seasons() / save_seasons(data)      current_season_id()      create_season(name, start, roster, current)
    update_season(id, changes)   set_current_season(id)   delete_season(id)   roster_for(season_id) -> {number: name}
    load_teams() / save_teams(data)   find_team(name)   create_team(name, short, colour)   team_or_create(name)
    update_team(id, changes)         load_play_types() / save_play_types(types)   play_label(side, value)
    game_info(stem)   save_game_info(stem, changes)   assign_game(stem, season_id, opponent, **more)
    list_games()  (dash_common.list_games() + "info" + "season_id")     games_in(season_id=, team_id=)
    sides(stem) -> {"labels", "colours", "our_team", "their_team", "guessed"}     team_labels(stem)
    shots_of(stem)   possessions_of(stem)   stats_of(stem)   court_of(stem)   marks_of(stem, label)
    score_tiles(shots)   player_rows(stem, label, names)   game_row(game)   record_of(rows)
    game_summary(stem)   season_summary(season_id)  ("unassigned" for the games in no season)   team_summary(team_id)
    add_fixture(season_id, fields)   update_fixture(season_id, fixture_id, changes)   delete_fixture(season_id, fixture_id)
    import_schedule(season_id, rows)   schedule_rows(season_id)   fixture_of_stem(stem)
    migrate()   UNASSIGNED
  Each function's docstring gives its return shape. "Our team" in a game is sides(stem)["our_team"], a colour label
  of teams.json (a colour word); player names come from the season's roster, never from roster.json.

THE CURRENT GAME, SEASON, TEAM. A page's URL carries them: game.html?game=test, season.html?season=2026-27,
team.html?team=riverside-high (ids as the library makes them). In JS:
    const game = await currentGame();      -> {"video": "clips/test.mp4", "name": "test", "game": row} or null
                                              (?game= first, else the remembered choice; it remembers a URL choice)
    const season = await currentSeason();  -> {"id", "name", ...} (?season= first, else the choice remembered in this
                                              browser, else the current season)
    const team = currentTeam();            -> the id from ?team= (else the remembered one) or null
    withParams('game.html', {game: 'test'})    withGame('tag.html')    withSeason('players.html')
  API calls take the game as the stem in the path (/api/game/{stem}/...) or as ?video=clips/test.mp4 (main.py's way),
  one or the other, as the module's docstring says.

FILES. The browser reads the data folders' files at  /files/<folder>/<path>  with <folder> one of outputs, crops,
clips, data, branding; the path is inside that folder. Videos support seeking (HTTP Range).
    /files/clips/test.mp4   /files/outputs/test/tracked.mp4   /files/data/_training/frames/test/test_000123.jpg
    /files/branding/logo.png      In JS: fileUrl('outputs', 'test/tracked.mp4'). Nothing else is ever served
    (data/_library and data/_dashboard, the names, settings and logs, are refused: the pages use /api for those).

GUARD. The server answers only requests addressed to 127.0.0.1 or localhost, and accepts a PUT, POST or DELETE
  only with the header  X-Hoops: 1  (dashboard.py, own_pages_only). api() in web/app.js adds it to every request;
  a page that sends a request some other way (settings.js's logo upload) adds it itself.

JOBS. To run a long program:  POST /api/jobs/start {"kind": "run", "video": "clips/game1.mp4"}
  Kinds (dash_common.JOB_KINDS): "run" (main.py; options --force, --from <stage>), "court_pictures",
  "court_landmarks", "hoops" (the three click pages; one open at a time, job name "click_page").
  A router may add a kind:  dash_common.JOB_KINDS["pick_frames"] = {"script": "...py", "heavy": False,
  "name": "pick_{stem}", "options": []}  (the script is run as  python src/<script> --video <video> options).
  Then poll:  const stop = pollJob('run_game1', info => {...}, 2000);  // every 2 s until info.running is false
  A click page's own address is info.address: open it in a new tab (window.open) when it appears.
  GET /api/jobs/run_<stem> also answers for a run started in a terminal (from the game's run_log.txt).
  Every long job shows progress and time left; a job inside the dashboard (a thread) answers a GET every second.

PAGE SKELETON. Every page: <!doctype html>, the viewport meta, the Google Fonts <link> (Barlow, Barlow Condensed)
as in index.html, <link rel="icon" href="/favicon.svg">, <link rel="stylesheet" href="app.css">, then
  Game analysis:
      <body class="area-analysis">
      <header class="site-header" id="site-header"></header>
      <main class="page"> ... </main>
      <script src="app.js"></script><script src="<screen>.js"></script>
    and the js calls  renderHeader('seasons')  ('seasons' | 'teams' | 'games' | 'players' | '' marks the active link).
    The header draws the logo from /api/branding, "HOOPS TRACKER / GAME ANALYSIS", the nav, the search box
    (renderHeader('seasons', {search: text => ...}) to answer typing) and "← Start page".
  Model training:
      <body class="area-training">
      <div class="train-shell"><aside class="side-bar" id="side-bar"></aside><main class="train-main"> ... </main></div>
    and the js calls  renderSidebar('annotate')  ('overview' | 'upload' | 'annotate' | 'dataset' | 'train' | 'models').
  The page title row: <div class="page-head"><div class="head-text"><a class="back-link">← Seasons</a>
  <h1 class="title">SEASONS</h1><div class="subtitle">...</div></div><div class="head-side">buttons</div></div>.

CSS CLASSES (web/app.css; the mockup's look, same names in both areas). Cards: .card (white, 1px line, 14px radius,
  20px 22px padding, column flex, gap 12px), .card-head (title row: h2 + something right), .card-title (h2),
  .card-note (the small grey line right of a title), .card-link (a card that is a link). Grids: .grid-cards
  (auto-fill 380px: seasons), .grid-cards-small (300px: teams), .grid-tiles (auto-fit 170px), .two-col (3:2 flex-wrap:
  .col-main + .col-side), .two-col-even (1:1). Stat tiles: .tile (white) / .tile-dark, .tile-label (12px, 2px
  letter-spacing), .tile-value (Barlow Condensed italic 44px; .tile-value-sm 40px); small facts inside a card: .fact-grid
  (3 up), .fact (.fact-label, .fact-value). Buttons: .btn plus .btn-red | .btn-dark | .btn-outline | .btn-light |
  .btn-green | .btn-link, sizes .btn-sm (36px) .btn-big (52px); .btn-icon (round, 44px). Chips: .chip plus
  .chip-green | .chip-blue | .chip-red | .chip-grey | .chip-dark | .chip-yellow. Pills (a choice row): .pills > .pill,
  chosen .on. Segmented choice: .segment > button/a, chosen .on. Progress: <div class="progress"><span style="width:57%">
  </span></div> with .progress-red | .progress-blue | .progress-green | .progress-dark | .progress-yellow, .progress-thin
  (6px); a labelled bar: .bar-row (.bar-label, .bar-value) above a .progress. Split bar: .split-bar > div (flex: share).
  Tables: .table-wrap > table.table (th 13px grey, 2px rule; td 11px 10px); .table .num (800), .table tr.hilite.
  Result colours: .win (green text), .loss (red text). Forms: .field > label + input/select/textarea, .form-grid,
  .check (a checkbox row), .check-box (a boxed checkbox row). Text: .muted, .small, .hint, .note, .eyebrow (13px, 2px
  spacing, 700), .big-number; notes: .callout (yellow), .callout-red, .callout-grey. Status dot: .dot .dot-green |
  .dot-grey | .dot-red | .dot-yellow. Court drawings: <svg class="court"> (tan background); drawHalfCourt() fills it.
  Leaders / step rows: .lead-row (.lead-number square, .lead-text, .lead-value). Step strip (training overview):
  ol.steps > li.step (.step-done | .step-now | .step-todo). Thumbnails: .thumb (96x56 dark box). Video: .video-pane.
  Toasts come from app.js: toast('text', 'bad'|'good'|''). Phone width: everything wraps; nothing to add.

JS HELPERS (app.js): api(path, options) / getJSON / putJSON / postJSON / deleteJSON, toast, tryToast(action),
  esc(text) (HTML-escape anything from a file or typing), el(tag, attrs, children), fileUrl(folder, path),
  renderHeader, renderSidebar, currentGame, currentSeason, currentTeam, withParams/withGame/withSeason/withTeam,
  rememberSeason(id), rememberTeam(id), pollJob, drawHalfCourt(svg, court, marks, colour) (hoop at the top; marks as
  library marks_of gives them), pointsByArea(marks), undrawnNote(marks), fmtClock(sec) "17:42", fmtLength(sec)
  "52 min", fmtDate("2026-01-24") "24 Jan 2026", fmtScore(us, them) "58–49", fmtResult(row) "W 58–49", fmtMadeTried({made,
  tried}) "5/11", fmtPct(x) "44%", fmtFeet, fmtMiles, fmtBytes, plural(n, word).
  Say when a number is provisional: a game with stand_in_court true was marked with stand-in clicks.

PRIVACY. Player names only ever live under data/ (seasons.json) and never leave the laptop; opponent players are
jersey numbers only. No school name, mascot, colours or jersey numbers in code or comments. No invented test data in
the real data folders: patch config.DATA_DIR to a scratch copy for anything that writes.
=====================================================================================================
"""
from fastapi import APIRouter, HTTPException

import config
import dash_common
import library

router = APIRouter(prefix="/api")

PLAIN_TITLE = "Hoops Tracker"


def problem(error):
    """A library.Problem as the browser gets it: a 400 with the plain text."""
    return HTTPException(400, str(error))


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


# ---------------------------------------------------------------- games

@router.get("/games")
def games():
    try:
        listed = library.list_games()
        seasons = library.load_seasons()["seasons"]
        teams = library.load_teams()["teams"]
    except OSError:
        raise HTTPException(503, "The data folders cannot be read. See \"If a data folder cannot be reached\" in README.md.")
    except library.Problem as error:
        raise problem(error)
    return {"games": listed, "chosen": dash_common.chosen_game(), "busy": dash_common.what_is_running(),
            "seasons": {key: row["name"] for key, row in seasons.items()},
            "teams": {key: {"name": row["name"], "short": row["short"], "colour": row["colour"]} for key, row in teams.items()}}


@router.put("/games/{stem}/info")
def save_game_info(stem: str, body: dict):
    """What the coach said about a game: its season, opponent, date, home or away, the score, which colour is us."""
    if not library.video_of(stem):
        raise HTTPException(400, "That game is not in the clips folder any more.")
    try:
        return library.save_game_info(stem, body if isinstance(body, dict) else {})
    except library.Problem as error:
        raise problem(error)
    except (OSError, SystemExit):
        raise HTTPException(503, "Could not write to the game's data folder.")


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


# ---------------------------------------------------------------- seasons and teams

def games_per(key):
    """{season id or team id: how many games}, from the game infos."""
    counts = {}
    for game in library.list_games():
        value = game["season_id"] if key == "season_id" else game["info"]["opponent_id"]
        counts[value] = counts.get(value, 0) + 1
    return counts


def seasons_reply():
    data = library.load_seasons()
    counts = games_per("season_id")
    listed = [dict(row, id=key, games=counts.get(key, 0)) for key, row in data["seasons"].items()]
    listed.sort(key=lambda row: row["start"], reverse=True)
    return {"seasons": listed, "current": library.current_season_id(), "unassigned": counts.get(library.UNASSIGNED, 0)}


@router.get("/seasons")
def seasons():
    try:
        return seasons_reply()
    except library.Problem as error:
        raise problem(error)


@router.post("/seasons")
def new_season(body: dict):
    try:
        season_id, row = library.create_season(body.get("name"), body.get("start"), body.get("roster"), bool(body.get("current")))
    except library.Problem as error:
        raise problem(error)
    return dict(row, id=season_id)


@router.put("/seasons/{season_id}")
def change_season(season_id: str, body: dict):
    try:
        return dict(library.update_season(season_id, body if isinstance(body, dict) else {}), id=season_id)
    except library.Problem as error:
        raise problem(error)


@router.delete("/seasons/{season_id}")
def remove_season(season_id: str):
    try:
        library.delete_season(season_id)
    except library.Problem as error:
        raise problem(error)
    return {"deleted": season_id}


@router.post("/seasons/{season_id}/schedule")
def add_fixture(season_id: str, body: dict):
    """A game on the season's schedule: date, time, opponent (a name makes the team), home, scores, kind, note,
    and stem to link a video at once."""
    try:
        return library.add_fixture(season_id, body if isinstance(body, dict) else {})
    except library.Problem as error:
        raise problem(error)


@router.put("/seasons/{season_id}/schedule/{fixture_id}")
def change_fixture(season_id: str, fixture_id: str, body: dict):
    """Change a scheduled game; {"stem": "<video>"} links a video to it, {"stem": null} unlinks."""
    try:
        return library.update_fixture(season_id, fixture_id, body if isinstance(body, dict) else {})
    except library.Problem as error:
        raise problem(error)


@router.delete("/seasons/{season_id}/schedule/{fixture_id}")
def remove_fixture(season_id: str, fixture_id: str):
    try:
        library.delete_fixture(season_id, fixture_id)
    except library.Problem as error:
        raise problem(error)
    return {"deleted": fixture_id}


@router.post("/seasons/{season_id}/schedule/import")
def import_schedule(season_id: str, body: dict):
    """Many scheduled games at once: {"rows": [{"date", "time", "opponent", "home", "score_us", "score_them", "kind"}]}."""
    rows = body.get("rows") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        raise HTTPException(400, "Send the games as a list of rows.")
    try:
        added, skipped = library.import_schedule(season_id, rows)
    except library.Problem as error:
        raise problem(error)
    return {"added": added, "skipped": skipped}


def teams_reply():
    counts = games_per("opponent_id")
    data = library.load_teams()
    listed = [dict(row, id=key, games=counts.get(key, 0)) for key, row in data["teams"].items()]
    listed.sort(key=lambda row: row["name"].lower())
    return {"teams": listed, "home": data["home"]}


@router.get("/teams")
def teams():
    try:
        return teams_reply()
    except library.Problem as error:
        raise problem(error)


@router.post("/teams")
def new_team(body: dict):
    try:
        team_id, row = library.create_team(body.get("name"), body.get("short"), body.get("colour"))
    except library.Problem as error:
        raise problem(error)
    return dict(row, id=team_id)


@router.put("/teams/home")
def change_home(body: dict):
    """Where our own gym is, for the map: {"place", "lat", "lon"}."""
    try:
        return library.set_home_location(body if isinstance(body, dict) else {})
    except library.Problem as error:
        raise problem(error)


@router.put("/teams/{team_id}")
def change_team(team_id: str, body: dict):
    try:
        return dict(library.update_team(team_id, body if isinstance(body, dict) else {}), id=team_id)
    except library.Problem as error:
        raise problem(error)


@router.get("/play-types")
def play_types():
    return library.load_play_types()


@router.put("/play-types")
def save_play_types(body: dict):
    try:
        return library.save_play_types(body)
    except library.Problem as error:
        raise problem(error)


# ---------------------------------------------------------------- settings, storage

def settings_reply():
    settings = dash_common.load_settings()
    sizes = {key: {"label": label, "length_ft": length, "width_ft": width}
             for key, (label, length, width) in dash_common.COURT_SIZES.items()}
    try:
        drive = dash_common.check_drive_folder(settings["drive_folder_path"])
    except ValueError as why:
        drive = {"path": settings["drive_folder_path"], "ok": False, "message": str(why), "windows_path": "", "entries": []}
    return dict(settings, court_sizes=sizes, models=dash_common.model_files(), drive=drive)


@router.get("/settings")
def settings():
    return settings_reply()


@router.put("/settings")
def save_settings(body: dict):
    try:
        dash_common.save_settings(body)
    except ValueError as why:
        raise HTTPException(400, str(why))
    return settings_reply()


@router.get("/settings/drive-folder")
def drive_folder(path: str = ""):
    """Look at a Drive folder path the coach typed, without saving it."""
    try:
        return dash_common.check_drive_folder(path)
    except ValueError as why:
        return {"path": path, "ok": False, "message": str(why), "windows_path": "", "entries": []}


@router.get("/storage")
def storage():
    return dash_common.storage_info()


# ---------------------------------------------------------------- jobs

@router.post("/jobs/start")
def start_job(body: dict):
    kind, video = str(body.get("kind") or ""), str(body.get("video") or "")
    options = body.get("options") or []
    try:
        name = dash_common.start_job(kind, video, options)
    except ValueError as why:
        raise HTTPException(409, str(why))
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
