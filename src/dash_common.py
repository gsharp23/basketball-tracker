"""Helpers shared by the dashboard (dashboard.py) and its API modules (dash_api.py, api_*.py).

Nothing in here knows about web pages or Gradio: it is plain functions over the files
in the data folders and the programs that run in the background.

What is in here
    DASH_DIR, work_dir(name)    data/_dashboard/ and a named working folder inside it
    SERVED_DIRS                 the folders the browser may be shown files from (see dashboard.py)
    sd_card_ok()                are the five linked data folders reachable?

    load_branding()             school name, mascot, logo and artwork from the branding folder
    save_branding(changes)      change the names in branding.json
    brand_colours(logo)         the logo's main colours, for theming
    text_on(colour)             white or black, whichever reads on that colour

    video_files(), list_games() the videos in clips/, and the status of each stage for each
    video_seconds(path)         how long a video is
    stand_in_court(video)       was this game's court marked with stand-in clicks, not by the coach?
    chosen_game(), remember_game(video)       the game the coach is working on

    load_settings(), save_settings(changes)   data/_dashboard/settings.json
    load_roster(), save_roster(roster)        roster.json at the project root (never leaves the laptop)
    storage_info()              are the data folders in place, where, and how full is the drive

    JOB_KINDS, start_job(kind, video, options)   run a long program in the background
    job_info(name)              what it is doing now: lines, progress, the address of a click page
    job_running(name), stop_job(name), stop_all_jobs()
    running_videos(), is_running(video), what_is_running()    is another run going?

Privacy: the school's name, logo and colours are read from the branding folder in the
data folder while the program runs. They must never be written into this code.
"""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np

import config
import court_mapper
import main         # for its own "is this stage done" checks; importing it runs nothing

# The dashboard's own working folders. A folder in data/ whose name starts with "_" is not a game.
DASH_DIR = config.DATA_DIR / "_dashboard"
BRANDING_DIR = Path(os.environ.get("HOOPS_BRANDING_DIR") or DASH_DIR / "branding")

# The browser may be shown files from these folders only, under /files/<name>/... (see dashboard.py).
SERVED_DIRS = {"outputs": config.OUTPUTS_DIR, "crops": config.CROPS_DIR, "clips": config.CLIPS_DIR,
               "data": config.DATA_DIR, "branding": BRANDING_DIR}

VIDEO_TYPES = (".mp4", ".mov", ".m4v", ".avi", ".mkv")

# Court sizes the coach can choose from, in feet.
COURT_SIZES = {"high_school": ("High school", 84, 50), "junior_high": ("Junior high", 74, 42),
               "college": ("College", 94, 50)}

DEFAULT_SETTINGS = {
    "court_size": "high_school",        # a key of COURT_SIZES
    "run_where": "laptop",              # "laptop" or "colab" (Colab is coming next)
    "make_videos": True,                # make the videos with player boxes and the court view
    "drive_folder": "Hoops Tracker",    # Google Drive, coming next
    "drive_delete_after": True,
}


def sd_card_ok():
    """True when all five linked data folders (clips, models, data, outputs, crops) can be reached."""
    folders = [config.CLIPS_DIR, config.MODELS_DIR, config.DATA_DIR, config.OUTPUTS_DIR, config.CROPS_DIR]
    try:
        return all(folder.is_dir() for folder in folders)
    except OSError:                # a damaged disk: treat it as not reachable
        return False


def work_dir(name):
    """The dashboard working folder data/_dashboard/<name>/, created if missing."""
    folder = DASH_DIR / name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def read_json(path, default=None):
    """A JSON file's contents, or `default` when it is missing or damaged. Never stops the program."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


# ---------------------------------------------------------------- branding

# Used when there is no branding folder, or no logo in it.
NEUTRAL_COLOURS = {"main": "#141112", "accent": "#d81e27", "plate": "#e9e6e2"}


def load_branding():
    """What the branding folder says about the school. Never fails.

    Returns {"school_name": text, "mascot": text, "logo": Path or None, "hero": Path or None}.
    Anything missing or unreadable comes back empty, and the pages then show a neutral title.
    """
    said = read_json(BRANDING_DIR / "branding.json", {})
    if not isinstance(said, dict):
        said = {}
    brand = {"school_name": str(said.get("school_name") or ""), "mascot": str(said.get("mascot") or "")}
    for key, usual_name in (("logo", "logo.png"), ("hero", "hero.svg")):
        path = BRANDING_DIR / str(said.get(key) or usual_name)
        try:
            brand[key] = path if path.is_file() else None
        except OSError:            # a damaged disk answers "Invalid argument" instead of "no such file"
            brand[key] = None
    return brand


def save_branding(changes):
    """Change the school name and mascot in branding.json (the picture names are kept)."""
    said = read_json(BRANDING_DIR / "branding.json", {})
    if not isinstance(said, dict):
        said = {}
    for key in ("school_name", "mascot"):
        if key in changes:
            said[key] = str(changes[key]).strip()
    BRANDING_DIR.mkdir(parents=True, exist_ok=True)
    config.save_json(BRANDING_DIR / "branding.json", said, indent=2)


def hex_colour(bgr):
    """An OpenCV colour (blue, green, red) as the "#rrggbb" text CSS wants."""
    blue, green, red = (int(round(float(value))) for value in bgr)
    return f"#{red:02x}{green:02x}{blue:02x}"


def brand_colours(logo_path):
    """The logo's main colours, read from the picture, for theming the pages.

    Returns {"main": a dark colour, "accent": the strongest bright colour, "plate": the
    logo's own background}, each as "#rrggbb". Falls back to NEUTRAL_COLOURS for whatever
    cannot be found.
    """
    colours = dict(NEUTRAL_COLOURS)
    picture = cv2.imread(str(logo_path), cv2.IMREAD_UNCHANGED) if logo_path else None
    if picture is None:
        return colours
    if picture.ndim == 2:
        picture = cv2.cvtColor(picture, cv2.COLOR_GRAY2BGR)
    pixels = picture[:, :, :3].reshape(-1, 3).astype(int)
    solid = picture[:, :, 3].reshape(-1) >= 128 if picture.shape[2] == 4 else np.ones(len(pixels), bool)

    # The logo's background: the colour of its top-left corner, unless that is see-through.
    if solid[0]:
        colours["plate"] = hex_colour(pixels[0])
        background = pixels[0]
    else:
        background = np.array([255, 255, 255])
    ink = pixels[solid & (np.abs(pixels - background).sum(axis=1) > 60)]     # everything that is not background
    if len(ink) < 50:
        return colours

    # Sort the pixels into coarse groups of similar colour, most common first.
    groups, counts = np.unique(ink // 32, axis=0, return_counts=True)
    order = np.argsort(-counts)
    groups, counts = groups[order] * 32 + 16, counts[order]
    brightness = groups.max(axis=1)
    strength = groups.max(axis=1) - groups.min(axis=1)       # 0 for grey, large for a pure colour

    def group_mean(index):
        return ink[(ink // 32 == groups[index] // 32).all(axis=1)].mean(axis=0)

    enough = counts >= 0.01 * len(ink)                       # ignore the thin blended edges
    dark = [i for i in range(len(groups)) if enough[i] and brightness[i] < 120]
    dark_colour = group_mean(dark[0]) if dark else None
    strong = [i for i in range(len(groups)) if enough[i] and strength[i] >= 96 and brightness[i] >= 120]
    bright_colour = group_mean(strong[0]) if strong else None
    if dark_colour is None and bright_colour is not None:
        dark_colour = bright_colour * 0.3
    if dark_colour is not None:
        colours["main"] = hex_colour(dark_colour)
    if bright_colour is not None:
        colours["accent"] = hex_colour(bright_colour)
    elif dark_colour is not None and dark_colour.max() - dark_colour.min() >= 60:
        colours["accent"] = hex_colour(np.minimum(dark_colour * 1.8 + 40, 255))
    return colours


def text_on(colour):
    """White or near-black, whichever reads better on the given "#rrggbb" colour."""
    red, green, blue = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    return "#111111" if 0.299 * red + 0.587 * green + 0.114 * blue > 150 else "#ffffff"


# ---------------------------------------------------------------- the games

def is_clip_video(video):
    """True when `video` names a video file inside the clips folder itself ("clips/test.mp4").

    The real path of the folder is resolved, so "clips/../something" does not pass. The file
    itself may be a link to a video kept elsewhere on the laptop (clips/game1.mp4 -> the
    Downloads folder). Every API that takes a video from the browser checks with this before
    touching the file or its data folder.
    """
    if not video or not isinstance(video, str):
        return False
    path = config.video_path(video)
    try:
        return (path.suffix.lower() in VIDEO_TYPES and path.is_file()
                and path.parent.resolve() == config.CLIPS_DIR.resolve())
    except OSError:
        return False


def video_files():
    """The video files in clips/, in name order (an empty list if the folder cannot be reached)."""
    if not config.CLIPS_DIR.is_dir():
        return []
    return sorted((path for path in config.CLIPS_DIR.iterdir()
                   if path.suffix.lower() in VIDEO_TYPES and path.is_file()), key=lambda path: path.name.lower())


_lengths = None     # "<file> <time> <size>" -> seconds of video; kept in data/_dashboard/video_lengths.json
                    # so a full game's file (slow to open) is opened once, not at every start


def video_seconds(path):
    """How long a video is, in seconds (None if it cannot be read). Only the file's header is read."""
    global _lengths
    path = Path(path)
    try:
        key = f"{path} {path.stat().st_mtime} {path.stat().st_size}"
    except OSError:
        return None
    if _lengths is None:
        saved = read_json(DASH_DIR / "video_lengths.json", {})
        _lengths = saved if isinstance(saved, dict) else {}
    if key not in _lengths:
        video = cv2.VideoCapture(str(path))
        fps, frames = video.get(cv2.CAP_PROP_FPS), video.get(cv2.CAP_PROP_FRAME_COUNT)
        video.release()
        # A header read that broke off (a slow card) gives 0 or 1 frames: that is not remembered, so it is read again.
        if not (fps and fps > 0 and frames and frames > 1):
            return None
        _lengths[key] = frames / fps
        try:
            DASH_DIR.mkdir(exist_ok=True)
            config.save_json(DASH_DIR / "video_lengths.json", _lengths, indent=1)
        except (OSError, SystemExit):
            pass            # worth remembering, not worth stopping for
    return _lengths[key]


_status_cache = {}      # video -> (what its files looked like, the stage statuses worked out from them)


def files_fingerprint(video):
    """Name, time and size of every file a game's status depends on. If this is unchanged, so is the status."""
    stem = config.video_stem(video)
    seen = []
    for folder in (config.DATA_DIR / stem, config.DATA_DIR / stem / "tracks_parts", config.OUTPUTS_DIR / stem):
        if folder.is_dir():
            for entry in sorted(os.scandir(folder), key=lambda e: e.name):
                info = entry.stat()
                seen.append((entry.path, info.st_mtime, info.st_size))
    for path in (config.video_path(video), config.ROSTER_PATH):
        if path.exists():
            seen.append((str(path), path.stat().st_mtime, path.stat().st_size))
    return seen


def stage_statuses(video):
    """{"detect": "done" | "half" | "todo" | "unknown", ...} for one video, as main.py itself judges it.

    main.py has to read a game's big result files to be sure, so the answer is kept until
    one of the game's files changes. A video nothing has been run on has no data folder
    yet, and none is made just by looking.
    """
    if not (config.DATA_DIR / config.video_stem(video)).is_dir():
        return {stage: "todo" for stage in main.STAGES}
    fingerprint = files_fingerprint(video)
    if video not in _status_cache or _status_cache[video][0] != fingerprint:
        try:
            stages = {stage: main.stage_status(video, stage) for stage in main.STAGES}
        except (SystemExit, OSError, KeyError, TypeError):
            stages = {stage: "unknown" for stage in main.STAGES}      # a damaged or half-written result file
        _status_cache[video] = (fingerprint, stages)
    return dict(_status_cache[video][1])


def stand_in_court(video):
    """True when this game's court landmarks are stand-in clicks, not the coach's own.

    select_court_keypoints.py writes "provisional" into court_calibration.json for such
    clicks. Every position worked out from them is provisional too, and the pages say so.
    """
    saved = read_json(config.DATA_DIR / config.video_stem(video) / "court_calibration.json", {})
    return bool(isinstance(saved, dict) and saved.get("provisional"))


def shots_count(stem):
    """How many shots the coach has tagged for this game, and the points they add up to."""
    saved = read_json(config.DATA_DIR / stem / "shots.json", {})
    shots = saved.get("shots", []) if isinstance(saved, dict) else []
    points = sum(int(shot.get("worth") or 0) for shot in shots if shot.get("made"))
    return len(shots), points


def players_count(stem):
    """How many players with a jersey number the stats found for this game (None before the stats stage)."""
    saved = read_json(config.DATA_DIR / stem / "stats.json")
    if not isinstance(saved, dict) or "players" not in saved:
        return None
    return sum(1 for player in saved["players"] if player.get("identified"))


def list_games():
    """Every video in clips/, in name order, with what has been done for it.

    Each game is a dict:
        "video"         "clips/game1.mp4"   (what main.py --video and the click pages take)
        "name"          "game1"             (also the name of its folders in data/ and outputs/)
        "seconds"       how long the video is (None if it cannot be read)
        "running"       True while main.py or one of the long stages is working on it
        "court_marked"  True when the reference pictures and the landmarks are both saved
        "stand_in_court" True when the landmarks are stand-in clicks: positions are provisional
        "stages"        {"detect": "done" | "half" | "todo" | "unknown", ...}; None while running
        "status"        "not started", "running", "needs the court marked", "stopped part-way", "finished"
        "shots", "points"  what the coach has tagged so far
        "players"       players with a number found by the stats (None before then)
        "roster_newer"  True when roster.json changed after the stats were made: "Carry on" only redoes the stats
    """
    games = []
    busy = running_videos()
    for path in video_files():
        video = f"clips/{path.name}"
        game = {"video": video, "name": path.stem, "seconds": video_seconds(path),
                "running": path.stem in busy, "stages": None}
        has_folder = (config.DATA_DIR / path.stem).is_dir()
        game["court_marked"] = has_folder and not court_mapper.setup_missing(video)
        game["stand_in_court"] = has_folder and stand_in_court(video)
        if game["running"]:
            game["status"] = "running"
        else:
            stages = game["stages"] = stage_statuses(video)
            before_court = [stages[stage] for stage in main.STAGES if stage not in main.NEEDS_COURT]
            if all(status == "done" for status in stages.values()):
                game["status"] = "finished"
            elif all(status == "todo" for status in stages.values()):
                game["status"] = "not started"
            elif not game["court_marked"] and all(status == "done" for status in before_court):
                game["status"] = "needs the court marked"
            else:
                game["status"] = "stopped part-way"
        game["shots"], game["points"] = shots_count(path.stem)
        game["players"] = players_count(path.stem)
        game["roster_newer"] = roster_newer_than_stats(path.stem)
        games.append(game)
    return games


def chosen_game():
    """The video the coach chose last ("clips/game1.mp4"), or None. Kept in data/_dashboard/chosen_game.json."""
    saved = read_json(DASH_DIR / "chosen_game.json", {})
    video = saved.get("video") if isinstance(saved, dict) else None
    return video if isinstance(video, str) and config.video_path(video).is_file() else None


def remember_game(video):
    """Keep the coach's choice of game, so every page and the next start open on the same one."""
    if video and video != chosen_game():
        DASH_DIR.mkdir(exist_ok=True)
        config.save_json(DASH_DIR / "chosen_game.json", {"video": video}, indent=2)


# ---------------------------------------------------------------- settings, roster, storage

def load_settings():
    """The coach's settings, with a default for anything not saved yet."""
    saved = read_json(DASH_DIR / "settings.json", {})
    settings = dict(DEFAULT_SETTINGS)
    if isinstance(saved, dict):
        settings.update({key: saved[key] for key in DEFAULT_SETTINGS if key in saved})
    if settings["court_size"] not in COURT_SIZES:
        settings["court_size"] = DEFAULT_SETTINGS["court_size"]
    return settings


def save_settings(changes):
    """Save the given settings (only the known keys) and return all of them."""
    settings = load_settings()
    for key in DEFAULT_SETTINGS:
        if key in changes:
            settings[key] = changes[key]
    if settings["court_size"] not in COURT_SIZES:
        raise ValueError("That court size is not one of the choices.")
    if settings["run_where"] not in ("laptop", "colab"):
        raise ValueError("\"Run new games on\" must be the laptop or Colab.")
    settings["make_videos"] = bool(settings["make_videos"])
    settings["drive_delete_after"] = bool(settings["drive_delete_after"])
    settings["drive_folder"] = str(settings["drive_folder"]).strip() or DEFAULT_SETTINGS["drive_folder"]
    DASH_DIR.mkdir(exist_ok=True)
    config.save_json(DASH_DIR / "settings.json", settings, indent=2)
    return settings


EMPTY_ROSTER = {"home": {"name": "Home", "players": {}}, "away": {"name": "Away", "players": {}}}


def load_roster():
    """roster.json as {"home": {"name", "players": {number: name}}, "away": {...}}, blank if missing."""
    saved = read_json(config.ROSTER_PATH, {})
    roster = json.loads(json.dumps(EMPTY_ROSTER))
    for side in ("home", "away"):
        team = saved.get(side) if isinstance(saved, dict) else None
        if isinstance(team, dict):
            roster[side]["name"] = str(team.get("name") or roster[side]["name"])
            players = team.get("players")
            if isinstance(players, dict):
                roster[side]["players"] = {str(number): str(name) for number, name in players.items()}
    return roster


def save_roster(roster):
    """Check and save a roster of the same shape as load_roster() gives. Raises ValueError with a plain message."""
    clean = json.loads(json.dumps(EMPTY_ROSTER))
    for side in ("home", "away"):
        team = roster.get(side) if isinstance(roster, dict) else None
        if not isinstance(team, dict):
            raise ValueError(f"The {side} team is missing.")
        clean[side]["name"] = str(team.get("name") or "").strip() or clean[side]["name"]
        for number, name in (team.get("players") or {}).items():
            number, name = str(number).strip(), str(name).strip()
            if not number:
                continue
            if not number.isdigit() or len(number) > 3:
                raise ValueError(f"\"{number}\" is not a jersey number (use the digits only, like 12).")
            if number in clean[side]["players"]:
                raise ValueError(f"Number {number} is listed twice for the {clean[side]['name']} team.")
            clean[side]["players"][number] = name
    # An unchanged roster is not written again: the stats stage is made from roster.json, so a newer
    # file would make every finished game ask for its stats to be done again.
    if read_json(config.ROSTER_PATH) != clean:
        config.save_json(config.ROSTER_PATH, clean, indent=2)
    return clean


def roster_newer_than_stats(stem):
    """True when roster.json was changed after this game's stats were made (so only the stats step wants doing again)."""
    stats_file = config.DATA_DIR / stem / "stats.json"
    try:
        return config.ROSTER_PATH.stat().st_mtime > stats_file.stat().st_mtime
    except OSError:
        return False


def storage_info():
    """Are the data folders in place, where they are (as Windows names it), and how full that drive is (bytes).

    "drive" is the Windows drive letter when the data is on a removable drive (D:), else None.
    """
    info = {"attached": sd_card_ok(), "folder": None, "drive": None, "used": None, "free": None, "total": None}
    folder = Path(os.readlink(config.DATA_DIR)).parent if config.DATA_DIR.is_symlink() else config.PROJECT_ROOT
    info["folder"] = main.windows_path(folder)
    if len(folder.parts) > 2 and folder.parts[1] == "mnt" and len(folder.parts[2]) == 1:
        info["drive"] = folder.parts[2].upper() + ":"
    if info["attached"]:
        try:
            usage = shutil.disk_usage(config.DATA_DIR.resolve())
            info.update(used=usage.used, free=usage.free, total=usage.total)
        except OSError:
            pass
    return info


# ---------------------------------------------------------------- long jobs in the background

# The programs the browser may start, by kind. A router can add its own kind to this table.
#   script      run as  python src/<script> --video <video> [options]
#   heavy       loads the player finder or the number reader: one such job at a time on this laptop
#   name        the job's name; "{stem}" is the video's name. A fixed name means one such job at a time.
JOB_KINDS = {
    "run": {"script": "main.py", "heavy": True, "name": "run_{stem}", "options": ["--force", "--from"]},
    "court_pictures": {"script": "select_court.py", "heavy": False, "name": "click_page", "options": ["--rescan"]},
    "court_landmarks": {"script": "select_court_keypoints.py", "heavy": False, "name": "click_page", "options": []},
    "hoops": {"script": "select_hoops.py", "heavy": False, "name": "click_page", "options": []},
}

_jobs = {}      # name -> {"process", "log", "heavy", "kind", "video", "started"}
_start_lock = threading.Lock()   # two requests at once must not both pass the "already running" check


def job_name_for(kind, video):
    return JOB_KINDS[kind]["name"].format(stem=config.video_stem(video))


def start_job(kind, video, options=()):
    """Start a program of the given kind on a video, in the background. Returns the job's name.

    Raises ValueError with a plain message when it cannot start: unknown kind, no such video,
    the same job still running, or a heavy job while another heavy run is going.
    Everything the program prints goes to data/_dashboard/logs/<name>.log; job_info(name)
    reads it back, also after the browser page was closed or reloaded. The job belongs to
    the dashboard: stopping the dashboard stops it too (the stages save their progress).
    """
    if kind not in JOB_KINDS:
        raise ValueError(f"The dashboard does not know the job \"{kind}\".")
    if not is_clip_video(video):
        raise ValueError("Choose a game first: that video is not in the clips folder.")
    with _start_lock:
        return _start_job_locked(kind, video, options)


def _start_job_locked(kind, video, options):
    what = JOB_KINDS[kind]
    name = job_name_for(kind, video)
    if job_running(name):
        raise ValueError("That is already running." if what["name"] != "click_page" else
                         "A click page is already open. Finish it (Save and close) or close it first.")
    if what["heavy"]:
        busy = what_is_running()
        if busy:
            raise ValueError(f"The laptop is busy with {busy}. One game at a time: wait for it to finish, or stop it.")
    options = [str(option) for option in options]
    for option in options:
        if option.startswith("-") and option not in what["options"]:
            raise ValueError(f"The option {option} is not allowed for this job.")
    command = [sys.executable, f"src/{what['script']}", "--video", video] + options
    log = work_dir("logs") / f"{name}.log"
    with open(log, "w", encoding="utf-8") as output:
        # PYTHONUNBUFFERED: a Python program then prints every line at once, not in blocks.
        # start_new_session: Ctrl+C in the dashboard's terminal then goes to the dashboard only,
        # which stops its jobs itself (stop_all_jobs), so no job gets Ctrl+C twice.
        process = subprocess.Popen(command, cwd=config.PROJECT_ROOT, stdout=output, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, start_new_session=True,
                                   env=dict(os.environ, PYTHONUNBUFFERED="1"))
    _jobs[name] = {"process": process, "log": log, "heavy": what["heavy"], "kind": kind, "video": video,
                   "started": time.time()}
    return name


def job_running(name):
    """True while the job of that name, started by this dashboard, is still going."""
    return name in _jobs and _jobs[name]["process"].poll() is None


def job_exit_code(name):
    """None while the job is running (or was never started); afterwards 0 if it ended well, another number if not."""
    return _jobs[name]["process"].poll() if name in _jobs else None


def tail(path, size=400_000):
    """The last `size` characters of a text file ("" if it cannot be read)."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(f.tell() - size, 0))
            return f.read()
    except OSError:
        return ""


def run_lines(video):
    """What the latest run on this game printed, as a list of lines.

    A run started from this dashboard: the job's own log. Any other run (started in a
    terminal, or before the dashboard was restarted): the last run in the game's run_log.txt.
    """
    name = job_name_for("run", video)
    ours = job_running(name) or (job_exit_code(name) is not None and not is_running(video))
    if ours:
        return tail(_jobs[name]["log"]).splitlines()
    text = tail(config.DATA_DIR / config.video_stem(video) / "run_log.txt")
    return text[text.rfind("\n===== ") + 1:].splitlines()     # main.py starts every run with a "=====" line


def read_progress(lines):
    """What the printed lines of one run say: which step, how far, how long is left, how it ended.

    Returns {"step": "detect", "step_number": 1, "step_count": 6, "how_far": 0.0 to 1.0 or None,
             "left": "about 5 min" or "", "detail": the latest progress line,
             "ended": "finished" | "court" | "stopped" | ""}.
    """
    progress = {"step": "", "step_number": 0, "step_count": 0, "how_far": None, "left": "", "detail": "", "ended": ""}
    for line in lines:
        new_step = re.match(r"=== Stage (\d+) of (\d+): (\w+)", line)
        if new_step:
            progress.update(step=new_step[3], step_number=int(new_step[1]), step_count=int(new_step[2]),
                            how_far=None, left="", detail="")
            continue
        # "  frame 300 of 6000 (5%) | ...", "  200 of 900 frames (...", "  read 40 of 600 frames"
        counted = re.match(r"\s+(?:frame |read )?(\d+) of (\d+)", line)
        # "... | about 5 min left", "... about 2:10 to go)", "... at most 40 min to go"
        left = re.search(r"(about|at most) ([^,|()]+?) (left|to go)", line)
        if counted and int(counted[2]):
            progress["how_far"] = min(int(counted[1]) / int(counted[2]), 1.0)
        if left:
            progress["left"] = f"{left[1]} {left[2]}"
        if counted or left:
            progress["detail"] = line.strip()
        if "already done - skipped" in line:
            progress["detail"] = "already done"
        if line.startswith("=== All stages are done"):
            progress["ended"] = "finished"
        elif line.startswith("STOPPED: the court has not been marked"):
            progress["ended"] = "court"
        elif line.startswith(("Stopped ", "KeyboardInterrupt")):       # the second: stopped while still loading
            progress["ended"] = "stopped"
    return progress


def job_address(lines):
    """The address a click page printed ("http://127.0.0.1:7861"), or None."""
    for line in reversed(lines):
        found = re.search(r"http://127\.0\.0\.1:\d+\S*", line)
        if found:
            return found[0]
    return None


def job_info(name, last=40):
    """Everything a page needs to show one job: whether it runs, its last lines, the progress read from them.

    For "run_<stem>" the lines come from run_lines(), so a run started in a terminal, or
    before the dashboard was restarted, is shown too.
    """
    job = _jobs.get(name)
    if name.startswith("run_"):
        stem = name[len("run_"):]
        video = f"clips/{stem}.mp4"
        matching = [path for path in video_files() if path.stem == stem]
        video = f"clips/{matching[0].name}" if matching else video
        lines = run_lines(video)
        running = job_running(name) or is_running(video)
        starting = job_running(name) and not is_running(video) and not lines
    else:
        lines = tail(job["log"]).splitlines() if job else []
        running = job_running(name)
        starting = running and not lines
    flagged = plain_flags(lines)
    return {"name": name, "known": job is not None or bool(lines), "running": running, "starting": starting,
            "exit_code": job_exit_code(name), "kind": job["kind"] if job else None,
            "video": job["video"] if job else None, "started": job["started"] if job else None,
            "lines": [line for line in lines if line.strip()][-last:], "progress": read_progress(lines),
            "address": job_address(lines), "flagged": flagged}


# The pipeline's NOTE lines, in the coach's words. The raw line (with its command lines and
# half sentences) stays in the log; the page shows these instead.
PLAIN_FLAGS = [
    ("the court landmarks for this video are PROVISIONAL", "The court was marked with stand-in clicks, so every position-based number is provisional. Do step 2 of the court set-up below yourself."),
    ("the court has not been marked", "The court is not marked yet: the position steps wait for it. Do the court set-up below."),
    ("older than tracks.json", "Some result files are older than the tracking, so they may not match it. Process the game again to refresh them."),
    ("no teams yet", "The teams were not sorted yet, so the court view shows every player in grey."),
    ("no jersey numbers yet", "No jersey numbers were read yet, so the court view shows tracking IDs instead."),
    ("do not show which roster team wears which colour", "The tool cannot tell which roster team wears which colour, so no names are used in the stats. Choose \"Our team wears\" above."),
    ("was stopped part-way", "The court mapping stopped part-way. Press \"Carry on\" to finish it."),
    ("could not be read back", "A result file could not be read back. Run the step again."),
]


def plain_flags(lines):
    """The NOTE/WARNING lines of a run, each turned into one plain sentence (unknown ones are left out)."""
    found = []
    for line in lines:
        if not line.lstrip().startswith(("NOTE:", "WARNING:")):
            continue
        for key, words in PLAIN_FLAGS:
            if key.lower() in line.lower() and words not in found:
                found.append(words)
    return found


def all_jobs():
    """A short line for every job this dashboard started: {name: {"running", "kind", "video", "exit_code"}}."""
    return {name: {"running": job_running(name), "kind": job["kind"], "video": job["video"],
                   "exit_code": job_exit_code(name)} for name, job in _jobs.items()}


def stop_job(name, wait=20):
    """Stop a job the way Ctrl+C would, so a stage saves its progress. Waits up to `wait` seconds for it to end."""
    if not job_running(name):
        return
    process = _jobs[name]["process"]
    process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=wait)
    except subprocess.TimeoutExpired:
        process.kill()          # it did not react: end it
        process.wait()


def stop_all_jobs():
    """Stop every job this dashboard started. Called when the dashboard itself stops."""
    for name in list(_jobs):
        stop_job(name)


def running_videos():
    """Names ("game1") of the videos some program is working on right now, wherever it was started.

    main.py and the long stages hold a lock file /tmp/hoops-video-<name>.lock while they
    work (config.lock_video). Linux lists every lock that is held in /proc/locks, by the
    file's number, so we can look without touching the lock ourselves.
    """
    try:
        with open("/proc/locks", "r", encoding="utf-8") as f:
            # a line looks like "2: FLOCK  ADVISORY  WRITE 201 08:30:33562186 0 EOF"; the last part of
            # the 6th word is the file's number. "->" marks a program waiting for a lock, not holding it.
            held = {line.split()[5].split(":")[-1] for line in f if "FLOCK" in line and "->" not in line}
    except OSError:
        return []
    return [path.name[len("hoops-video-"):-len(".lock")] for path in Path("/tmp").glob("hoops-video-*.lock")
            if str(path.stat().st_ino) in held]


def is_running(video):
    """True when some program (this dashboard or a terminal) is working on this video right now."""
    return config.video_stem(video) in running_videos()


def what_is_running():
    """Plain words for the heavy job going on right now, or "" when the laptop is free for one."""
    for name, job in _jobs.items():
        if job["heavy"] and job_running(name):
            return f"the processing of {config.video_stem(job['video'])} (started from this dashboard)"
    videos = running_videos()
    if videos:
        return "the processing of " + " and ".join(videos)
    return ""
