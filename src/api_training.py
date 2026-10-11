"""Model training, steps Overview, 1 · Upload and 2 · Annotate (web/train.html, upload.html, annotate.html).

The work is done by training_data.py (the pictures, their conditions, the boxes, label assist);
this file only answers the pages. Everything is under /api/training. A picture is named by its
game and frame number, as in labels.json: /api/training/frame/test/123.

    GET  /api/training/overview            the Overview screen: the project, the five steps, dataset health, next steps
    GET  /api/training/games               the Upload screen: every video in clips/ with its pictures so far, the
                                           detector in use, the test game, and the picking job's state
    POST /api/training/upload              body {"games": ["game1", ...], "per_game": 150, "assist": true}: start the
                                           background job that picks the pictures and runs label assist -> {"name"}
    POST /api/training/assist              body {"game"}: label assist on that game's pictures that have none yet
    PUT  /api/training/test-game           body {"game": "game1" or null}: keep one whole game for testing
    GET  /api/training/pictures?game=      the pictures of one game, one short row each
    GET  /api/training/queue?mode=&game=   the unchecked pictures in the order to check them (hard | order | unsure)
    GET  /api/training/frame/{game}/{frame}      one picture: its record, picture URL, neighbours for the filmstrip
    PUT  /api/training/frame/{game}/{frame}      body {"status", "boxes"}: the coach's answer (fast: no picture work)

The picking job runs as a separate program (training_data.py) through the shared job routes, under the
heavy lock, because it loads the detector: GET /api/jobs/pick_frames says how far it is.
"""
from fastapi import APIRouter, HTTPException

import config
import dash_common
import library
import main as pipeline      # only for windows_path()
import training_data as td

router = APIRouter(prefix="/api/training")

# The Upload screen's background job: one at a time, under the heavy lock (it loads the detector).
dash_common.JOB_KINDS["pick_frames"] = {"script": "training_data.py", "heavy": True, "name": "pick_frames",
                                        "options": ["--frames", "--more", "--no-assist", "--assist-only"]}
JOB_NAME = "pick_frames"
MAX_FRAMES_PER_GAME = 2000
NEED_MORE_BELOW = 150           # fewer pictures than this in a condition: "need more"
QUEUE_MODES = ("hard", "order", "unsure")
NEIGHBOURS = 4                  # pictures either side in the filmstrip


def plainly(action):
    """Run a training_data call; a Problem becomes a plain 400."""
    try:
        return action()
    except td.Problem as problem:
        raise HTTPException(400, str(problem))
    except library.Problem as problem:
        raise HTTPException(400, str(problem))


def game_words(game):
    """"vs Riverside · 24 Jan 2026" from the game's info, or just the video's name."""
    info = library.game_info(game)
    words = []
    if info.get("opponent"):
        words.append(f"vs {info['opponent']}")
    if info.get("date"):
        words.append(info["date"])
    return " · ".join(words) or game


def picture_url(game, frame):
    """The full picture as the browser fetches it, and its small copy (None when there is none)."""
    try:
        inside = td.frame_path(game, frame).relative_to(config.DATA_DIR)
    except ValueError:
        raise HTTPException(500, "The training folder is not inside the data folder, so its pictures cannot be shown.")
    full = "/files/data/" + "/".join(inside.parts)
    thumb = td.thumb_path(game, frame)
    return full, full.replace("/frames/", "/thumbs/", 1) if thumb.is_file() else None


def reason_words(reason):
    return {"model unsure": "Model unsure", "dim": "Dim gym", "blur": "Blurred, fast play",
            "small ball": "Small ball (zoomed out)"}.get(reason, "In order")


def short_record(record):
    """One picture as a list row: no boxes, just the counts."""
    return {"game": record["game"], "frame": record["frame"], "time": record.get("time"), "status": record["status"],
            "conditions": record["conditions"], "queue_reason": record["queue_reason"],
            "boxes": len(record["boxes"]),
            "model_boxes": sum(1 for box in record["boxes"] if box.get("source") == "model")}


def job_state():
    """The picking job as the pages show it: running, its progress and last lines."""
    info = dash_common.job_info(JOB_NAME, last=12)
    return {"running": info["running"], "starting": info["starting"], "progress": info["progress"],
            "lines": info["lines"], "exit_code": info["exit_code"], "flagged": info["flagged"]}


def versions():
    """The dataset versions the Dataset step built: [{"number", "built", "counts", "test_game"}], newest last."""
    folder = td.training_dir() / "versions"
    found = []
    if folder.is_dir():
        for entry in sorted(folder.glob("v*")):
            if not entry.name[1:].isdigit():
                continue
            card = dash_common.read_json(entry / "version.json", {})
            card = card if isinstance(card, dict) else {}
            found.append({"number": int(entry.name[1:]), "built": card.get("built"), "counts": card.get("counts"),
                          "test_game": card.get("test_game")})
    found.sort(key=lambda version: version["number"])
    return found


def model_in_use():
    """The detector the analysis side runs, with its score card when the notebook wrote one."""
    name = td.detector_name()
    card = next((model for model in dash_common.model_files() if model["name"] == name), None)
    scores = (card or {}).get("scores") or {}
    ball = scores.get("Ball") if isinstance(scores.get("Ball"), dict) else None
    return {"name": name, "label": dash_common.model_label(name, card or {}), "trained": (card or {}).get("trained"),
            "dataset_version": (card or {}).get("dataset_version"),
            "ball_recall": ball.get("recall") if ball else None, "scored": bool(scores)}


def shots_tagged():
    """How many shots the coach has tagged in game analysis, over every game (each is a labelled clip)."""
    return sum(dash_common.shots_count(path.stem)[0] for path in dash_common.video_files())


def minutes_words(pictures):
    seconds = pictures * td.SECONDS_PER_CHECK
    if seconds < 90:
        return "A minute or two"
    if seconds < 3600:
        return f"About {round(seconds / 60 / 5) * 5 or 5} minutes"
    return f"About {seconds / 3600:.1f} hours".replace(".0 hours", " hours")


# ---------------------------------------------------------------- the Overview screen

@router.get("/overview")
def overview():
    labels = plainly(td.load_labels)
    project = plainly(td.load_project)
    records = td.records_of(labels)
    counts = td.status_counts(records)
    games = td.games_with_frames()
    gyms = {library.game_info(game)["conditions"].get("gym") for game in games} - {"", None}
    conditions = td.condition_counts(records)
    by_class = td.class_counts(records, project["classes"])
    built = versions()
    latest = built[-1] if built else None
    model = model_in_use()

    # The next things to do, in order, as cards.
    steps = []
    if not counts["pictures"]:
        steps.append({"title": "Get pictures from your games", "what": "Choose games on the Upload screen; about 150 pictures each",
                      "href": "upload.html"})
    if counts["unchecked"]:
        steps.append({"title": f"Check {counts['unchecked']:,} pictures", "what": f"{minutes_words(counts['unchecked'])} with label assist on",
                      "href": "annotate.html"})
    next_version = (latest["number"] + 1) if latest else 1
    steps.append({"title": f"Build dataset v{next_version}", "what": "Adds dark, blur and zoom variations", "href": "dataset.html"})
    steps.append({"title": f"Train v{next_version} on Colab", "what": "About an hour; score comes back here", "href": "models.html#train"})

    # Which classes are rare next to Player, for the note under the bars.
    player = by_class.get("Player", 0)
    rare = [name for name, count in by_class.items() if name != "Player" and player and count < player / 5]
    if not counts["checked"]:
        note = "No picture is checked yet. The labels a model learns from are the boxes on checked pictures."
    elif rare:
        note = (f"{' and '.join(rare)} {'is' if len(rare) == 1 else 'are'} rare compared with players. "
                "The hard-pictures queue is showing dim and blurred frames first.")
    else:
        note = "Every class has a fair share of labels. The hard-pictures queue is showing dim and blurred frames first."

    return {
        "project": {"classes": project["classes"], "test_game": project["test_game"], "games": len(games), "gyms": len(gyms)},
        "counts": counts, "assisted": td.assisted_count(records),
        "steps": {
            "upload": {"pictures": counts["pictures"], "games": len(games)},
            "annotate": {"checked": counts["checked"], "pictures": counts["pictures"]},
            "dataset": {"version": latest["number"] if latest else None, "built": latest["built"] if latest else None},
            "train": {"running": False},
            "models": model,
        },
        "classes": [{"name": name, "count": count, "share": (count / max(by_class.values()) if max(by_class.values()) else 0)}
                    for name, count in by_class.items()],
        "conditions": [{"name": "Good light", "count": conditions["good"], "need_more": False},
                       {"name": "Dim gym", "count": conditions["dim"], "need_more": conditions["dim"] < NEED_MORE_BELOW},
                       {"name": "Zoomed out / small ball", "count": conditions["wide"], "need_more": conditions["wide"] < NEED_MORE_BELOW},
                       {"name": "Blurred / fast play", "count": conditions["blur"], "need_more": conditions["blur"] < NEED_MORE_BELOW}],
        "need_more_below": NEED_MORE_BELOW, "note": note, "next_steps": steps, "job": job_state(),
    }


# ---------------------------------------------------------------- the Upload screen

@router.get("/games")
def games():
    labels = plainly(td.load_labels)
    project = plainly(td.load_project)
    rows = []
    for path in dash_common.video_files():
        game = path.stem
        records = td.records_of(labels, game)
        counts = td.status_counts(records)
        rows.append({"game": game, "video": f"clips/{path.name}", "words": game_words(game),
                     "seconds": dash_common.video_seconds(path),
                     "processed": (config.DATA_DIR / game / "tracks.json").is_file(),
                     "counts": counts, "conditions": td.condition_counts(records), "assisted": td.assisted_count(records),
                     "test_game": project["test_game"] == game})
    return {"games": rows, "test_game": project["test_game"], "default_per_game": td.DEFAULT_FRAMES,
            "max_per_game": MAX_FRAMES_PER_GAME, "detector": td.detector_name(), "job": job_state(),
            "clips_folder": pipeline.windows_path(config.CLIPS_DIR)}


def start_pick(videos, options):
    try:
        return dash_common.start_job("pick_frames", videos[0], options)
    except ValueError as problem:
        raise HTTPException(409, str(problem))


@router.post("/upload")
def upload(body: dict):
    chosen = body.get("games") if isinstance(body.get("games"), list) else []
    videos = []
    for game in chosen:
        video = library.video_of(str(game))
        if not video:
            raise HTTPException(400, f"\"{game}\" is not a video in the clips folder any more. Press Refresh.")
        videos.append(video)
    if not videos:
        raise HTTPException(400, "Tick at least one game to take pictures from.")
    try:
        per_game = int(body.get("per_game") or td.DEFAULT_FRAMES)
    except (TypeError, ValueError):
        raise HTTPException(400, "Pictures per game: type a whole number, like 150.")
    if per_game < 1 or per_game > MAX_FRAMES_PER_GAME:
        raise HTTPException(400, f"Pictures per game: between 1 and {MAX_FRAMES_PER_GAME}.")
    options = ["--frames", str(per_game)]
    for video in videos[1:]:
        options += ["--more", video]
    if body.get("assist") is False:
        options.append("--no-assist")
    plainly(td.load_labels)                     # the data folder must be reachable before the job starts
    return {"name": start_pick(videos, options), "job": job_state()}


@router.post("/assist")
def assist(body: dict):
    game = str(body.get("game") or "")
    video = library.video_of(game)
    if not video:
        raise HTTPException(400, "That game is not in the clips folder any more.")
    labels = plainly(td.load_labels)
    if not td.frames_without_assist(labels, game):
        raise HTTPException(400, "Every picture of this game has its first boxes already.")
    return {"name": start_pick([video], ["--assist-only"]), "job": job_state()}


@router.put("/test-game")
def test_game(body: dict):
    game = body.get("game")
    project = plainly(lambda: td.set_test_game(str(game) if game else None))
    return {"test_game": project["test_game"]}


@router.get("/pictures")
def pictures(game: str = ""):
    if not library.video_of(game) and game not in td.games_with_frames():
        raise HTTPException(400, "No such game.")
    labels = plainly(td.load_labels)
    rows = [short_record(record) for record in td.records_of(labels, game)]
    return {"game": game, "words": game_words(game), "pictures": rows,
            "thumbs": {row["frame"]: picture_url(game, row["frame"])[1] for row in rows}}


# ---------------------------------------------------------------- the Annotate screen

@router.get("/queue")
def queue(mode: str = "hard", game: str = ""):
    if mode not in QUEUE_MODES:
        raise HTTPException(400, "The queue is \"hard\" (hard pictures first), \"order\" or \"unsure\".")
    labels = plainly(td.load_labels)
    project = plainly(td.load_project)
    counts = td.status_counts(td.records_of(labels, game or None))
    keys = [{"key": key, "game": record["game"], "frame": record["frame"], "reason": record["queue_reason"]}
            for key, record in td.queue(labels, mode, game or None)]
    job = job_state()
    return {"mode": mode, "game": game, "queue": keys, "counts": counts, "classes": project["classes"],
            "detector": model_in_use(), "shots_tagged": shots_tagged(), "job_running": bool(job["running"] or job["starting"])}


@router.get("/frame/{game}/{frame}")
def frame(game: str, frame: int):
    labels = plainly(td.load_labels)
    record = labels["frames"].get(td.frame_key(game, frame))
    if record is None or not td.frame_path(game, frame).is_file():
        raise HTTPException(404, "That picture is not in the training pictures any more.")
    url, thumb = picture_url(game, frame)
    # the filmstrip: the pictures of the same game either side of this one
    same_game = td.records_of(labels, game)
    index = next(i for i, other in enumerate(same_game) if other["frame"] == frame)
    neighbours = []
    for other in same_game[max(0, index - NEIGHBOURS):index + NEIGHBOURS + 1]:
        full, small = picture_url(game, other["frame"])
        neighbours.append({"game": game, "frame": other["frame"], "status": other["status"], "thumb": small or full,
                           "current": other["frame"] == frame})
    return {"record": record, "url": url, "thumb": thumb, "words": game_words(game),
            "condition_words": td.condition_words(record["conditions"]), "reason_words": reason_words(record["queue_reason"]),
            "neighbours": neighbours, "index": index, "count": len(same_game)}


@router.put("/frame/{game}/{frame}")
def answer(game: str, frame: int, body: dict):
    status = str(body.get("status") or "")
    try:
        # read, change and write under the labels lock: the Upload job may be saving the same file (see training_data)
        with plainly(td.labels_lock):
            labels = plainly(td.load_labels)
            record = plainly(lambda: td.set_answer(labels, game, frame, status, body.get("boxes")))
            td.save_labels(labels)
    except OSError as problem:
        raise HTTPException(503, f"The answer could not be saved ({problem}). Check the data folder and try again.")
    return {"record": record, "counts": td.status_counts(td.records_of(labels))}
