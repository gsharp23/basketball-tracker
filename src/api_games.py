"""The Games screen and the Game results screen (web/games.html + games.js, web/game.html + game.js).

    GET  /api/games/list                 every video in clips/ with everything the cards show (see game_card)
    PUT  /api/games/info?video=          body {"opponent", "date", "our_team"} -> saved in data/<stem>/game_info.json
    GET  /api/games/results?video=       the results screen: score tiles, players table, the heat map's marks, videos
    GET  /api/games/court?video=         the court set-up: the three steps, which are done, the check pictures

Starting and stopping the processing, and the three click pages, go through the shared
job routes in dash_api.py (POST /api/jobs/start with kind "run", "court_pictures",
"court_landmarks" or "hoops"; GET /api/jobs/<name>; POST /api/jobs/<name>/stop).

data/<stem>/game_info.json   what the coach typed about a game (read and written through
shots.game_info / save_game_info, shared with the Opponent and Tag shots screens):
    "opponent"   who the game was against (the title reads "vs <opponent>"); "" until typed
    "date"       the date as text ("2026-01-24"); default: the day the video file was last changed
    "our_team"   the label of the coach's team in this game ("white"), as teams.json names them;
                 until chosen, the team whose jersey numbers match the roster (possessions.guess_our_team)
"""
import datetime

import cv2
from fastapi import APIRouter, HTTPException

import config
import court_mapper
import dash_common
import main
import possessions
import shots
import stats

router = APIRouter(prefix="/api/games")

# Plain words for each stage of main.py, for the progress line.
STEP_WORDS = {"detect": "Finding the players", "court": "Mapping the court", "teams": "Sorting the players into teams",
              "jerseys": "Reading the jersey numbers", "tactical": "Drawing the court view", "stats": "Working out the stats"}

# From the README's measured estimate: a 52-minute game is a day or two on this laptop
# (detection 8 to 14 hours, jersey reading 10 or more, court 3, court view 2 to 3).
LAPTOP_HOURS_PER_VIDEO_MINUTE = 0.5

THUMBNAIL_WIDTH = 640

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


# ---------------------------------------------------------------- the games list

def need_video(video):
    """The video as "clips/<file>", or a plain 400 when it is not in the clips folder."""
    if not dash_common.is_clip_video(video):
        raise HTTPException(400, "That game is not in the clips folder any more. Press Refresh.")
    return video


def thumbnail_url(video):
    """One frame of the video, kept as outputs/<stem>/thumbnail.jpg (made once), as a /files URL. None if it fails."""
    stem = config.video_stem(video)
    path = config.OUTPUTS_DIR / stem / "thumbnail.jpg"
    if not path.is_file():
        seconds = dash_common.video_seconds(config.video_path(video)) or 0
        capture = cv2.VideoCapture(str(config.video_path(video)))
        # A minute in (the start of a recording is often an empty gym), or the middle of a short clip.
        capture.set(cv2.CAP_PROP_POS_MSEC, 1000 * min(60, seconds / 2))
        ok, frame = capture.read()
        capture.release()
        if not ok:
            return None
        height = int(frame.shape[0] * THUMBNAIL_WIDTH / frame.shape[1])
        small = cv2.resize(frame, (THUMBNAIL_WIDTH, height), interpolation=cv2.INTER_AREA)
        try:
            config.output_dir(video)
            cv2.imwrite(str(path), small, [cv2.IMWRITE_JPEG_QUALITY, 80])
        except (OSError, SystemExit, cv2.error):
            return None
    return f"/files/outputs/{stem}/thumbnail.jpg"


def laptop_estimate(seconds):
    """How long processing takes on this laptop, in words, from the README's measured estimate."""
    if seconds is None:
        return "many hours"
    if seconds < 300:
        return "a few minutes"
    hours = seconds / 60 * LAPTOP_HOURS_PER_VIDEO_MINUTE
    if hours < 10:
        return f"about {hours:.0f} hours"
    return f"about {hours / 24:.0f} day{'s' if hours >= 36 else ''} ({hours:.0f} hours)"


def game_card(game):
    """One game as the Games screen shows it: dash_common.list_games() plus the title, the picture and the progress."""
    card = dict(game)
    card["info"] = shots.game_info(game["video"])
    card["thumbnail_url"] = thumbnail_url(game["video"])
    card["estimate"] = laptop_estimate(game["seconds"])
    card["step_words"] = STEP_WORDS
    card["progress"] = None
    if game["running"] or game["status"] == "stopped part-way":
        job = dash_common.job_info(f"run_{game['name']}", last=1)
        card["progress"] = job["progress"]
        card["job_running"] = job["running"]
    return card


@router.get("/list")
def list_games():
    try:
        games = [game_card(game) for game in dash_common.list_games()]
    except OSError:
        raise HTTPException(503, "The data folders cannot be reached. See \"If a data folder cannot be reached\" in README.md.")
    return {"games": games, "chosen": dash_common.chosen_game(), "busy": dash_common.what_is_running(),
            "clips_folder": main.windows_path(config.CLIPS_DIR),
            "run_where": dash_common.load_settings()["run_where"]}


@router.put("/info")
def save_info(video: str, body: dict):
    """Keep what the coach typed about a game (its opponent, date and which team is ours)."""
    need_video(video)
    changes = {}
    if "opponent" in body:
        changes["opponent"] = str(body["opponent"]).strip()[:80]
    if "date" in body:
        date = str(body["date"]).strip()
        if date:
            try:
                datetime.date.fromisoformat(date)
            except ValueError:
                raise HTTPException(400, "The date should look like 2026-01-24.")
        changes["date"] = date
    if "our_team" in body:
        labels = team_labels(video)
        if body["our_team"] not in labels:
            raise HTTPException(400, "The teams of this game are: " + " and ".join(labels) + ".")
        changes["our_team"] = body["our_team"]
    try:
        return shots.save_game_info(video, changes)
    except (OSError, SystemExit):
        raise HTTPException(503, "Could not write to the game's data folder.")


# ---------------------------------------------------------------- the results screen

def team_labels(video):
    """The two team labels of a game, as teams.json names them (empty before the teams stage)."""
    teams = dash_common.read_json(config.DATA_DIR / config.video_stem(video) / "teams.json", {})
    if not isinstance(teams, dict) or "teams" not in teams:
        return []
    return [teams["teams"][key]["label"] for key in ("0", "1") if key in teams["teams"]]


def our_team(video):
    """Which team is the coach's: what he chose, else the team whose numbers match the roster, else the first."""
    labels = team_labels(video)
    chosen = shots.game_info(video)["our_team"]
    if chosen in labels:
        return chosen
    try:
        guessed = possessions.guess_our_team(video)
    except (shots.Problem, SystemExit, OSError, KeyError, TypeError):
        guessed = None
    return guessed if guessed in labels else (labels[0] if labels else None)


def shot_marks(video, team):
    """Our tagged shots folded onto one half court, for the heat map (same shape as the opponent report's)."""
    if not team:
        return []
    try:
        return possessions.shot_marks(video, team)
    except (shots.Problem, SystemExit):
        return []          # the game is not processed far enough for positions; the tiles still show


def made_tried(shot_list):
    return {"made": sum(1 for shot in shot_list if shot.get("made")), "tried": len(shot_list)}


def score_tiles(shot_list):
    """Points, field goals, three-pointers and free throws from one team's shots."""
    field_goals = [shot for shot in shot_list if shot.get("kind") == "field goal"]
    return {"points": sum(int(shot.get("worth") or 0) for shot in shot_list if shot.get("made")),
            "field_goals": made_tried(field_goals),
            "threes": made_tried([shot for shot in field_goals if shot.get("worth") == 3]),
            "free_throws": made_tried([shot for shot in shot_list if shot.get("kind") == "free throw"])}


def player_names(video, stats_data):
    """{(team label, number): name} from the roster as it is now (stats.json keeps the names of its own run)."""
    try:
        return shots.load_teams(video)["names"]
    except (shots.Problem, SystemExit, KeyError, TypeError):
        return {(row["team"], row["number"]): row["name"] for row in stats_data.get("players", []) if row.get("name")}


def player_rows(video, stats_data, team, shot_list):
    """The players table of one team: stats.json's numbers, the roster's names, the tagged shots' points."""
    names = player_names(video, stats_data)
    by_number = {}
    for shot in shot_list:
        if shot.get("number") is not None:
            by_number.setdefault(str(shot["number"]), []).append(shot)
    rows = []
    for row in stats_data.get("players", []):
        if row.get("team") != team or not row.get("identified"):
            continue
        mine = by_number.get(str(row["number"]), [])
        field_goals = [shot for shot in mine if shot.get("kind") == "field goal"]
        rows.append({"number": row["number"], "name": names.get((team, row["number"])) or row.get("name") or "",
                     "points": sum(int(shot.get("worth") or 0) for shot in mine if shot.get("made")),
                     "shots": made_tried(field_goals), "seen_s": row.get("seen_s"), "on_map_s": row.get("on_map_s"),
                     "distance_ft": row.get("distance_ft"), "paint_pct": row.get("paint_pct"),
                     "two_places": bool(row.get("two_places_frames"))})
    rows.sort(key=lambda row: (-row["points"], -(row["seen_s"] or 0)))
    unnumbered = sum(1 for row in stats_data.get("players", []) if row.get("team") == team and not row.get("identified"))
    return rows, unnumbered


@router.get("/results")
def results(video: str):
    """Everything the Game results screen shows for the coach's team."""
    need_video(video)
    stem = config.video_stem(video)
    folder, out = config.DATA_DIR / stem, config.OUTPUTS_DIR / stem
    stats_data = dash_common.read_json(folder / "stats.json", {})
    stats_data = stats_data if isinstance(stats_data, dict) else {}
    saved_shots = dash_common.read_json(folder / "shots.json", {})
    all_shots = saved_shots.get("shots", []) if isinstance(saved_shots, dict) else []
    team = our_team(video)
    mine = [shot for shot in all_shots if shot.get("team") == team]
    labels = team_labels(video)
    rows, unnumbered = player_rows(video, stats_data, team, mine) if stats_data else ([], 0)
    shooters = sorted({str(shot["number"]) for shot in mine if shot.get("number") is not None}, key=int)
    videos = {name: f"/files/outputs/{stem}/{name}.mp4" for name in ("tactical", "tracked") if (out / f"{name}.mp4").is_file()}
    return {"video": video, "name": stem, "info": shots.game_info(video), "seconds": dash_common.video_seconds(config.video_path(video)),
            "teams": labels, "our_team": team, "opponent_team": next((label for label in labels if label != team), None),
            "our_team_chosen": shots.game_info(video)["our_team"] in labels,
            "stand_in_court": dash_common.stand_in_court(video), "has_stats": bool(stats_data),
            "shots_tagged": len(mine), "tiles": score_tiles(mine), "shooters": shooters,
            "court": dict(zip(("length_ft", "width_ft"), shots.court_size(video))),
            "marks": shot_marks(video, team),
            "players": rows, "players_without_number": unnumbered,
            # The roster's jersey numbers, so the page can say when none of them match this game.
            "roster_numbers": sorted({number for _, names in stats.load_roster() for number in names}, key=str),
            "videos": videos, "stats_csv": main.windows_path(folder / "stats.csv") if (folder / "stats.csv").is_file() else None,
            "warnings": stats_data.get("warnings", [])}


# ---------------------------------------------------------------- the court set-up

@router.get("/court")
def court_setup(video: str):
    """The three court set-up steps for a game: which are done, their check pictures, and the open click page."""
    need_video(video)
    stem = config.video_stem(video)
    folder, out = config.DATA_DIR / stem, config.OUTPUTS_DIR / stem
    steps = []
    for number, step in enumerate(COURT_STEPS, 1):
        done = (folder / step["file"]).is_file()
        steps.append({"number": number, "kind": step["kind"], "title": step["title"], "guidance": step["guidance"],
                      "done": done, "picture_url": f"/files/outputs/{stem}/{step['picture']}"
                      if (out / step["picture"]).is_file() else None})
    page = dash_common.job_info("click_page", last=3) if dash_common.job_running("click_page") else None
    return {"video": video, "steps": steps, "ready": folder.is_dir() and not court_mapper.setup_missing(video),
            "stand_in_court": dash_common.stand_in_court(video),
            "court_check_url": f"/files/outputs/{stem}/court_check.jpg" if (out / "court_check.jpg").is_file() else None,
            "open_page": {"kind": page["kind"], "video": page["video"], "address": page["address"]} if page else None}
