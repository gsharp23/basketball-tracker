"""The Game screen (web/game.html + game.js): one game, the way a video platform shows it.

The video with a possession timeline under it, a live top-down court that follows the video,
the score tiles, the players table and the scoring heat map; below them the processing card
and the court set-up panel (the three click pages).

    GET  /api/game/{stem}/summary      library.game_summary(stem) plus what this screen adds (see summary())
    GET  /api/game/{stem}/positions    the compact positions table the live court follows (format below)
    GET  /api/game/{stem}/court        the court set-up: the three steps, which are done, the check pictures

Starting and stopping the processing and the click pages go through the shared job routes
(POST /api/jobs/start with kind "run", "court_pictures", "court_landmarks" or "hoops"). The final
score and "our team wears" are saved with PUT /api/games/{stem}/info (dash_api.py).

THE POSITIONS TABLE (GET /api/game/{stem}/positions): a whole game in one answer, a few MB at most.
    {"ready": true,
     "fps": 29.97,                the game video's frame rate
     "stride": 3,                 every stride-th frame of the video was looked at (about 10 looks a second)
     "step": 2,                   every step-th look is in the table: 1 for a short clip; a long game is thinned
                                  so that the table has at most MAX_ROWS rows (5 rows a second for an hour)
     "court": {"length_ft": 84, "width_ft": 50},
     "players": {"7": {"team": "<colour group>", "number": "<jersey number>"}},   player tag -> its colour group and number
                                  (null when the number was not read); only tags that appear in the rows
     "frames": [0, 6, 12, ...],   the frame number (in the game video) each row stands for
     "rows": [[[7, 612, 234], [9, 588, 301]], ...]}
                                  one row per frame: [tag, x, y] with x and y in TENTHS OF A FOOT, whole numbers.
                                  x runs along the court from the left baseline as the camera sees it, y across
                                  from the near sideline (court_model.py's coordinates). An empty row means nobody
                                  had a position at that moment.
    The browser finds the row for a video time t with frame = t * fps, then the nearest entry of "frames".
    tracked.mp4 is written at fps/stride pictures a second, so its clock is the game video's clock.
    When the game has not been processed far enough: {"ready": false, "why": "plain words"}.
"""
import json
import math

import cv2
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

import config
import court_mapper
import dash_common
import library
import shots
import stats

router = APIRouter(prefix="/api/game")

# The positions table keeps at most this many rows (an hour of game at 5 looks a second). A longer
# game is thinned by taking every 2nd, 3rd... look, so a full game stays a few MB.
MAX_ROWS = 18000

# A sprint: faster than SPRINT_FT_S (about 8 mph, a real run rather than a jog) for at least
# SPRINT_SECONDS in a row. A step faster than stats.MAX_SPEED_FT_S is a tracking mistake and ends the run.
SPRINT_FT_S = 12.0
SPRINT_SECONDS = 1.0

# From the README's measured estimate: a 52-minute game is a day or two on this laptop.
LAPTOP_HOURS_PER_VIDEO_MINUTE = 0.5

# The three court set-up steps, in order, with the README's one-line guidance for each.
COURT_STEPS = [
    {"kind": "court_pictures", "title": "Choose the court pictures", "file": "court_setup.json",
     "picture": "court_views.jpg",
     "guidance": "Choose the court size, untick any picture that is blurred or does not show the court, "
                 "then press \"Save and close\"."},
    {"kind": "court_landmarks", "title": "Click the court landmarks", "file": "court_calibration.json",
     "picture": "court_calibration_check.jpg",
     "guidance": "Pick a picture, choose a landmark from the list, click it in the picture and again in the "
                 "enlarged picture. At least 4 landmarks at each end, 10 to 14 in all, including the two points "
                 "where the half-court line crosses the centre circle. The green court should sit on the real lines."},
    {"kind": "hoops", "title": "Mark the hoops (optional)", "file": "hoops.json", "picture": "hoops_check.jpg",
     "guidance": "For each hoop the video shows, click the two ends of the rim. Only needed for shots later."},
]


def need_game(stem):
    """The game's video as "clips/<file>", or a plain 400 when the stem is not a video in the clips folder."""
    video = library.video_of(stem)
    if not video:
        raise HTTPException(400, "That game is not in the clips folder any more.")
    return video


def laptop_estimate(seconds):
    """How long processing takes on this laptop, in words."""
    if seconds is None:
        return "many hours"
    if seconds < 300:
        return "a few minutes"
    hours = seconds / 60 * LAPTOP_HOURS_PER_VIDEO_MINUTE
    if hours < 10:
        return f"about {hours:.0f} hours"
    return f"about {hours / 24:.0f} day{'s' if hours >= 36 else ''} ({hours:.0f} hours)"


# ---------------------------------------------------------------- the video the coach watches

def video_fps(path):
    """Frames per second of a video, from its header only (None when it cannot be read)."""
    capture = cv2.VideoCapture(str(path))
    fps = capture.get(cv2.CAP_PROP_FPS)
    capture.release()
    return fps if fps and fps > 0 else None


def watch_info(stem, video):
    """Which video the page plays and how its clock relates to the game's: {"url", "kind", "scale"}.

    The game video itself when the browser can play it (the same rule as the Tag page, shots.watch_video):
    it is smooth, and clean. tracked.mp4 is the pipeline's own check video (a tracking ID over every
    person, "ball?" guesses, a frame counter) at 10 pictures a second, so it stands in only for a game
    file the browser cannot play. game seconds = watched seconds * scale: the tracked video is written at
    fps/stride pictures a second, so the scale is 1 unless its header says otherwise.
    """
    tracked = config.OUTPUTS_DIR / stem / "tracked.mp4"
    if shots.watch_video(video)[1] == "game" or not tracked.is_file():
        return {"url": f"/files/{video}", "kind": "game", "scale": 1.0}
    scale = 1.0
    cached = positions_of(stem)
    tracked_fps = video_fps(tracked)
    if cached and tracked_fps:
        scale = round((cached["fps"] / cached["stride"]) / tracked_fps, 4)
    return {"url": f"/files/outputs/{stem}/tracked.mp4", "kind": "tracked", "scale": scale}


# ---------------------------------------------------------------- the positions table and the sprints

_cache = {}     # stem -> {"stamp", "fps", "stride", "table", "sprints"}; only the last game is kept (a full game is big)


def who_is_who(stem):
    """{player tag: {"team": colour group or None, "number": jersey number or None}} from teams.json and jerseys.json."""
    teams = library.read_file(config.DATA_DIR / stem / "teams.json", {})
    jerseys = library.read_file(config.DATA_DIR / stem / "jerseys.json", {})
    labels = library.team_labels(stem)
    read = jerseys.get("tracks", {}) if isinstance(jerseys, dict) else {}
    players = {}
    for key, index in (teams.get("tracks", {}) if isinstance(teams, dict) else {}).items():
        number = read.get(key, {}).get("number") if isinstance(read.get(key), dict) else None
        players[str(key)] = {"team": labels[index] if index is not None and index < len(labels) else None,
                             "number": str(number) if number is not None else None}
    return players


def count_sprints(path, stride, fps):
    """How many times a player ran faster than SPRINT_FT_S for at least SPRINT_SECONDS in a row.

    path is {frame: (x, y)} in feet, every stride-th frame. A gap (no position one look
    earlier) or a tracking jump ends a run, as in stats.measure.
    """
    look_s = stride / fps
    fast_run, sprints = 0.0, 0
    for frame in sorted(path):
        before = path.get(frame - stride)
        speed = math.dist(before, path[frame]) / look_s if before else None
        if speed is not None and SPRINT_FT_S <= speed <= stats.MAX_SPEED_FT_S:
            fast_run += look_s
            continue
        if fast_run >= SPRINT_SECONDS:
            sprints += 1
        fast_run = 0.0
    if fast_run >= SPRINT_SECONDS:
        sprints += 1
    return sprints


def sprints_by_player(stem, spots, stride, fps):
    """{colour group: {jersey number: sprints}} for every numbered player of the stats (their tags joined as stats does)."""
    found = {}
    for row in library.stats_of(stem).get("players", []):
        if not row.get("identified") or not row.get("team"):
            continue
        path = {}
        # the tag with the most positions first; where two tags overlap, the first one's position stays
        for tag in sorted(row.get("tracking_ids", []), key=lambda tag: -len(spots.get(tag, {}))):
            for frame, spot in spots.get(tag, {}).items():
                path.setdefault(frame, spot)
        found.setdefault(row["team"], {})[str(row["number"])] = count_sprints(path, stride, fps)
    return found


def positions_of(stem):
    """The positions table and the sprints of a game, read once from positions.json until the file changes.

    Returns {"fps", "stride", "table", "sprints"} or None when the positions are not made yet
    (the court stage first writes a raw positions.json; only the cleaned one is used, as stats.py does).
    """
    path = config.DATA_DIR / stem / "positions.json"
    video = library.video_of(stem)
    if not path.is_file() or not video:
        return None
    stamp = (path.stat().st_mtime, path.stat().st_size)
    cached = _cache.get(stem)
    if cached and cached["stamp"] == stamp:
        return cached
    saved = library.read_file(path, {})
    if not isinstance(saved, dict) or "cleaned" not in saved or not saved.get("frames"):
        return None
    fps = video_fps(config.video_path(video)) or 30.0
    frames, rows, spots = [], [], {}
    for record in saved["frames"]:
        frames.append(record["frame"])
        rows.append([[player["id"], int(round(player["x"] * 10)), int(round(player["y"] * 10))] for player in record["players"]])
        for player in record["players"]:
            spots.setdefault(player["id"], {})[record["frame"]] = (player["x"], player["y"])
    # the stride is the gap between two looks; positions.json does not say it, but its frame numbers do
    gaps = [later - earlier for earlier, later in zip(frames, frames[1:]) if later > earlier]
    stride = min(gaps) if gaps else config.FRAME_STRIDE
    step = max(1, math.ceil(len(frames) / MAX_ROWS))
    used = {tag for row in rows[::step] for tag, _, _ in row}
    table = {"ready": True, "fps": fps, "stride": stride, "step": step, "court": saved["court"],
             "players": {tag: who for tag, who in who_is_who(stem).items() if int(tag) in used},
             "frames": frames[::step], "rows": rows[::step]}
    _cache.clear()
    _cache[stem] = {"stamp": stamp, "fps": fps, "stride": stride, "table": table,
                    "sprints": sprints_by_player(stem, spots, stride, fps)}
    return _cache[stem]


def not_ready_words(stem):
    """Why there is no positions table yet, in plain words."""
    folder = config.DATA_DIR / stem
    if not (folder / "tracks.json").is_file():
        return "The court follows the video once the game has been processed."
    return "The players' positions are made at the court steps of processing. Mark the court below, then process the game."


# ---------------------------------------------------------------- the routes

@router.get("/{stem}/summary")
def summary(stem: str):
    """Everything the Game screen shows: library.game_summary(stem) plus
    "seconds" (the video's length), "watch" ({"url", "kind", "scale"}: the video to play), "sprints"
    ({colour group: {number: count}}), "positions_ready" (is the live court's table there?),
    "estimate" (how long processing would take here, in words)."""
    video = need_game(stem)
    try:
        result = library.game_summary(stem)
    except library.Problem as error:
        raise HTTPException(400, str(error))
    cached = positions_of(stem)
    result["seconds"] = dash_common.video_seconds(config.video_path(video))
    result["watch"] = watch_info(stem, video)
    result["sprints"] = cached["sprints"] if cached else {}
    result["positions_ready"] = cached is not None
    result["estimate"] = laptop_estimate(result["seconds"])
    return result


@router.get("/{stem}/positions")
def positions(stem: str):
    """The compact positions table (format at the top of this file), or {"ready": false, "why"}."""
    need_game(stem)
    cached = positions_of(stem)
    if not cached:
        return {"ready": False, "why": not_ready_words(stem)}
    # Dumped by hand without spaces: a full game's table is a few MB, and every byte is downloaded by the page.
    return Response(json.dumps(cached["table"], separators=(",", ":")), media_type="application/json")


@router.get("/{stem}/court")
def court_setup(stem: str):
    """The three court set-up steps for a game: which are done, their check pictures, and the open click page."""
    video = need_game(stem)
    folder, out = config.DATA_DIR / stem, config.OUTPUTS_DIR / stem
    steps = []
    for number, step in enumerate(COURT_STEPS, 1):
        steps.append({"number": number, "kind": step["kind"], "title": step["title"], "guidance": step["guidance"],
                      "done": (folder / step["file"]).is_file(),
                      "picture_url": f"/files/outputs/{stem}/{step['picture']}" if (out / step["picture"]).is_file() else None})
    page = dash_common.job_info("click_page", last=3) if dash_common.job_running("click_page") else None
    return {"video": video, "steps": steps, "ready": folder.is_dir() and not court_mapper.setup_missing(video),
            "stand_in_court": dash_common.stand_in_court(video),
            "court_check_url": f"/files/outputs/{stem}/court_check.jpg" if (out / "court_check.jpg").is_file() else None,
            "open_page": {"kind": page["kind"], "video": page["video"], "address": page["address"]} if page else None}
