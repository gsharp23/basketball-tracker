"""The "Train the model" screen's own API (web/train.html + train.js).

The screen is where the coach clicks the ball in frames of his own games. The real work
(picking frames, keeping the answers, the zip for Colab) is in ball_labels.py; this file
only answers the page. Everything is under /api/train and takes the game as ?video=clips/game1.mp4.

    GET  /api/train/status?video=      how far this game is, the frame list, every game's count,
                                       the test game, the latest zip, and the two long jobs
    GET  /api/train/frame?video=&index= one frame: its picture URL, the saved answer, the detector's hints
    POST /api/train/answer             body {video, frame, answer, box, width, height}: save one answer
    POST /api/train/clear              body {video, frame}: take an answer back (Undo)
    POST /api/train/pick               body {video, count}: pick frames in the background
    GET  /api/train/pick               how the picking is going
    PUT  /api/train/test_game          body {video, keep}: keep this game for testing (or not)
    POST /api/train/export             make the zip in the background
    GET  /api/train/export             how the zip is going, and the result
    GET  /api/train/models             the ball model files in models/ and which one is "in use"
    PUT  /api/train/models/in_use      body {name}

The two long jobs (picking frames, making the zip) take seconds to minutes and load no
model, so they run in a thread inside the dashboard rather than as a separate program:
the page asks every second how far they are.
"""
import re
import threading
import time
import zipfile
from datetime import datetime

from fastapi import APIRouter, HTTPException

import ball_labels
import config
import dash_common
import main as pipeline      # only for windows_path()

router = APIRouter(prefix="/api/train")

ANSWERS = ("ball", "no ball", "skipped")
MAX_FRAMES_PER_GAME = 2000
IN_USE_FILE = dash_common.DASH_DIR / "ball_model.json"      # {"in_use": "ball_model_v2.pt"}


# ---------------------------------------------------------------- small helpers

def stem_of(video):
    """"clips/test.mp4" -> "test", or a plain 400 when the video is not in the clips folder."""
    if not dash_common.is_clip_video(video):
        raise HTTPException(400, "Choose a game first: that video is not in the clips folder.")
    return config.video_stem(video)


def labels_or_400():
    try:
        return ball_labels.load_labels()
    except ValueError as problem:
        raise HTTPException(400, str(problem))


def save_or_503(labels):
    try:
        ball_labels.save_labels(labels)
    except OSError as problem:
        raise HTTPException(503, f"The answer could not be saved ({problem}). Check the data folder and try again.")


def frame_url(stem, path):
    """The picture of a frame as the browser fetches it: /files/data/_training/frames/<game>/<file>."""
    try:
        inside = path.relative_to(config.DATA_DIR)
    except ValueError:
        raise HTTPException(500, "The training folder is not inside the data folder, so its pictures cannot be shown.")
    return "/files/data/" + "/".join(inside.parts)


def windows_name(path):
    """A file as Windows names it (D:\\... or \\\\wsl.localhost\\...), because that is where the coach will look for it."""
    return pipeline.windows_path(path)


# ---------------------------------------------------------------- the two long jobs, in threads

# One dictionary per job. "running" while the thread works; "error" holds a plain message when it failed.
picking = {"running": False, "game": None, "done": 0, "total": 0, "seconds_left": None, "unreadable": 0, "error": None}
exporting = {"running": False, "done": 0, "total": 0, "checking": False, "result": None, "error": None}


def run_pick(video, count):
    picking.update(running=True, game=config.video_stem(video), done=0, total=0, seconds_left=None, unreadable=0, error=None)
    try:
        for progress in ball_labels.pick_frames(video, count):
            picking.update(progress)
    except (ValueError, OSError) as problem:
        picking["error"] = str(problem)
    except Exception as problem:
        picking["error"] = f"The frames could not be picked: {problem}"
    finally:
        picking["running"] = False


def run_export():
    exporting.update(running=True, done=0, total=0, checking=False, result=None, error=None)
    try:
        for step in ball_labels.export_dataset():
            if "result" in step:
                result = step["result"]
                exporting["result"] = {"name": result["path"].name, "path": windows_name(result["path"]),
                                       "folder": windows_name(result["path"].parent), "megabytes": result["megabytes"],
                                       "number": result["number"], "by_game": result["by_game"],
                                       "explanation": result["explanation"], "counts": result["counts"],
                                       "notebook": windows_name(result["notebook"]) if result["notebook"] else None}
            else:
                exporting.update(step)
    except (ValueError, OSError) as problem:
        exporting["error"] = str(problem)
    except Exception as problem:            # a fault in the program: the page must still get a message
        exporting["error"] = f"The zip could not be made: {problem}"
    finally:
        exporting["running"] = False


# ---------------------------------------------------------------- status and frames

def latest_zip():
    """The newest zip in data/_training/export/, as {"name", "path", "megabytes", "when"}, or None."""
    folder = ball_labels.TRAINING_DIR / "export"
    zips = sorted(folder.glob("ball_dataset_*.zip"), key=lambda path: path.stat().st_mtime) if folder.is_dir() else []
    if not zips:
        return None
    info = zips[-1].stat()
    return {"name": zips[-1].name, "path": windows_name(zips[-1]), "folder": windows_name(zips[-1].parent),
            "megabytes": info.st_size / 1e6, "when": datetime.fromtimestamp(info.st_mtime).strftime("%d %b %Y %H:%M")}


@router.get("/status")
def status(video: str = ""):
    stem = stem_of(video)
    labels = labels_or_400()
    frames = ball_labels.frame_files(stem)
    answers = labels["frames"]
    listed = [{"frame": frame, "answer": (answers.get(ball_labels.frame_key(stem, frame)) or {}).get("answer")}
              for frame, _ in frames]
    first_to_do = next((index for index, item in enumerate(listed) if not item["answer"]), None)
    games = [{"name": name, "counts": ball_labels.count_answers(labels, name)} for name in ball_labels.games_with_frames()]
    answered = sum(game["counts"]["ball"] + game["counts"]["no ball"] for game in games)
    return {"game": stem, "counts": ball_labels.count_answers(labels, stem, frames), "frames": listed,
            "first_to_do": first_to_do, "test_game": labels.get("test_game"),
            "games": games, "answered": answered, "games_answered": sum(1 for game in games
                                                                       if game["counts"]["ball"] + game["counts"]["no ball"]),
            "min_to_export": ball_labels.MIN_FRAMES_TO_EXPORT, "default_frames": ball_labels.DEFAULT_FRAMES,
            "split": ball_labels.split_frames(labels)[2], "latest_zip": latest_zip(),
            "picking": dict(picking), "exporting": dict(exporting)}


@router.get("/frame")
def frame(video: str = "", index: int = 0):
    """One picked frame: the picture, the saved answer (if any), the detector's hints (if the game was processed)."""
    stem = stem_of(video)
    frames = ball_labels.frame_files(stem)
    if not frames:
        raise HTTPException(400, "No frames have been picked from this game yet.")
    index = min(max(index, 0), len(frames) - 1)
    number, path = frames[index]
    labels = labels_or_400()
    hint = ball_labels.load_hints(stem).get(number, {})
    entry = labels["frames"].get(ball_labels.frame_key(stem, number))
    # The first idea of the ball's size is worked out on the page (see train.js, sizeAt), the same way
    # as ball_labels.propose_size: nearest player box, else the coach's usual size in this game, else a share of the width.
    before = sorted(entry["box"]["w"] for entry in labels["frames"].values()
                    if entry["game"] == stem and entry["answer"] == "ball")
    return {"index": index, "count": len(frames), "frame": number, "url": frame_url(stem, path),
            "answer": entry, "guess": hint.get("guess"), "players": hint.get("players", []),
            "usual_size": before[len(before) // 2] if before else None,
            "sizes": {"share_of_player": ball_labels.BALL_SHARE_OF_PLAYER, "share_of_width": ball_labels.DEFAULT_BALL_SHARE,
                      "min": ball_labels.MIN_BALL_PX}}


# ---------------------------------------------------------------- answers

@router.post("/answer")
def answer(body: dict):
    stem = stem_of(str(body.get("video") or ""))
    frames = dict(ball_labels.frame_files(stem))
    number = body.get("frame")
    if not isinstance(number, int) or number not in frames:
        raise HTTPException(400, "That picture is not one of this game's picked frames any more. Reload the page.")
    said = body.get("answer")
    if said not in ANSWERS:
        raise HTTPException(400, "The answer must be the ball, no ball, or can't tell.")
    try:
        width, height = int(body.get("width")), int(body.get("height"))
        if width <= 0 or height <= 0:
            raise ValueError
    except (TypeError, ValueError):
        raise HTTPException(400, "The picture's size is missing. Reload the page and answer again.")
    box = None
    if said == "ball":
        given = body.get("box") or {}
        try:
            box = (float(given["cx"]), float(given["cy"]), int(given["size"]))
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, "Click the ball in the picture first.")
        if not (0 <= box[0] <= width and 0 <= box[1] <= height) or box[2] < ball_labels.MIN_BALL_PX:
            raise HTTPException(400, "That click is outside the picture. Click the ball in the picture first.")
    labels = labels_or_400()
    ball_labels.set_answer(labels, stem, number, width, height, said, box)
    save_or_503(labels)
    return {"saved": labels["frames"][ball_labels.frame_key(stem, number)], "counts": ball_labels.count_answers(labels, stem)}


@router.post("/clear")
def clear(body: dict):
    """Take back the answer for one frame (Undo), so it is "to do" again."""
    stem = stem_of(str(body.get("video") or ""))
    labels = labels_or_400()
    key = ball_labels.frame_key(stem, body.get("frame"))
    if key in labels["frames"]:
        del labels["frames"][key]
        save_or_503(labels)
    return {"counts": ball_labels.count_answers(labels, stem)}


# ---------------------------------------------------------------- picking frames

@router.post("/pick")
def pick(body: dict):
    video = str(body.get("video") or "")
    stem = stem_of(video)
    try:
        count = int(body.get("count") or ball_labels.DEFAULT_FRAMES)
    except (TypeError, ValueError):
        raise HTTPException(400, "How many frames? Type a whole number, like 150.")
    if count < 1 or count > MAX_FRAMES_PER_GAME:
        raise HTTPException(400, f"Pick between 1 and {MAX_FRAMES_PER_GAME} frames.")
    if picking["running"]:
        raise HTTPException(409, f"Frames are already being picked from {picking['game']}. Wait for that to finish.")
    have = len(ball_labels.frame_files(stem))
    if count <= have:
        raise HTTPException(409, f"This game already has {have} frames. Ask for more than that to add some.")
    labels_or_400()                                 # the data folder must be reachable before the thread starts
    threading.Thread(target=run_pick, args=(video, count), daemon=True).start()
    time.sleep(0.2)                                 # so the first answer already says "running"
    return dict(picking)


@router.get("/pick")
def pick_progress():
    return dict(picking)


# ---------------------------------------------------------------- the test game and the zip

@router.put("/test_game")
def test_game(body: dict):
    stem = stem_of(str(body.get("video") or ""))
    labels = labels_or_400()
    if body.get("keep"):
        if stem not in ball_labels.games_with_frames():
            raise HTTPException(400, "Pick some frames from this game first.")
        labels["test_game"] = stem
    elif labels.get("test_game") == stem:
        labels["test_game"] = None
    save_or_503(labels)
    parts, by_game, explanation = ball_labels.split_frames(labels)
    return {"test_game": labels["test_game"], "split": explanation, "by_game": by_game}


@router.post("/export")
def export():
    if exporting["running"]:
        raise HTTPException(409, "The zip is being made already. Wait for it to finish.")
    labels = labels_or_400()
    # The same checks the export makes, done first so the coach gets the message at once.
    answered = sum(1 for entry in labels["frames"].values() if entry["answer"] != "skipped")
    if answered < ball_labels.MIN_FRAMES_TO_EXPORT:
        raise HTTPException(400, f"Only {answered} frames have an answer so far. Label at least "
                                 f"{ball_labels.MIN_FRAMES_TO_EXPORT} before making the zip (a useful model needs a few hundred).")
    threading.Thread(target=run_export, daemon=True).start()
    time.sleep(0.2)
    return dict(exporting)


@router.get("/export")
def export_progress():
    return dict(exporting)


# ---------------------------------------------------------------- the ball models

def read_scores(path):
    """The scores file the notebook writes next to a model: {"precision", "recall", "map50", "trained", "dataset"} or None."""
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    scores = {}
    for name in ("precision", "recall", "map50"):
        found = re.search(rf"{name}\s+([0-9.]+)", text, re.IGNORECASE)
        if found:
            scores[name] = float(found[1])
    trained = re.search(r"trained (\d{4}-\d{2}-\d{2}) from (\S+)", text)
    if trained:
        scores["trained"], scores["dataset"] = trained[1], trained[2]
    return scores or None


def labels_in_zip(name):
    """How many pictures a dataset zip holds (train + val), read from its data.yaml, or None if the zip is gone."""
    path = ball_labels.TRAINING_DIR / "export" / name
    if not path.is_file():
        return None
    try:
        with zipfile.ZipFile(path) as archive:
            yaml = archive.read("data.yaml").decode("utf-8", errors="replace")
    except (OSError, KeyError, zipfile.BadZipFile):
        return None
    counts = {found[1]: int(found[2]) for found in re.finditer(r"# (train|val|test): (\d+) frames", yaml)}
    return counts.get("train", 0) + counts.get("val", 0) if counts else None


def in_use_model():
    saved = dash_common.read_json(IN_USE_FILE, {})
    return saved.get("in_use") if isinstance(saved, dict) else None


def looks_like_ball_model(path):
    return "ball" in path.stem.lower()


@router.get("/models")
def models():
    """The model files that look like ball models, newest first."""
    if not config.MODELS_DIR.is_dir():
        raise HTTPException(503, "The models folder cannot be reached.")
    chosen = in_use_model()
    found = []
    for path in config.MODELS_DIR.glob("*.pt"):
        if not looks_like_ball_model(path):
            continue
        info = path.stat()
        version = re.match(r"ball_model_v(\d+)", path.stem)
        scores = read_scores(path.with_name(path.stem + "_scores.txt"))
        found.append({"name": path.name, "label": f"Version {version[1]}" if version else path.stem,
                      "megabytes": info.st_size / 1e6, "date": datetime.fromtimestamp(info.st_mtime).strftime("%d %b %Y"),
                      "trained": path.stem.startswith("ball_model_v"), "scores": scores,
                      "labels": labels_in_zip(scores["dataset"]) if scores and scores.get("dataset") else None,
                      "in_use": path.name == chosen,
                      "note": "An earlier ball model that came with the tool. It was tried and found unreliable."
                              if path.name == "ball_detector_model.pt" else ""})
    found.sort(key=lambda model: (model["trained"], model["date"], model["name"]), reverse=True)
    return {"models": found, "in_use": chosen}


@router.put("/models/in_use")
def choose_model(body: dict):
    name = str(body.get("name") or "")
    path = config.MODELS_DIR / name
    if "/" in name or not path.is_file() or path.suffix != ".pt" or not looks_like_ball_model(path):
        raise HTTPException(400, "That model file is not in the models folder.")
    dash_common.DASH_DIR.mkdir(exist_ok=True)
    config.save_json(IN_USE_FILE, {"in_use": name}, indent=2)
    return {"in_use": name}
