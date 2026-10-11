"""Training data: the pictures, the boxes and the conditions a detector learns from (steps 1 and 2 of Model training).

The successor of the v2 ball-only labelling (one class, the ball) for the four-class project: Ball, Referee,
Player, Hoop. No web code in here; api_training.py answers the browser with these functions, and
the dataset step (3 · Dataset) reads the same files.

    python src/training_data.py --video clips/game1.mp4 [--frames 150] [--more clips/game2.mp4] [--no-assist]
        pick pictures spread over each game (150 per game by default), guess the conditions of each, and
        run LABEL ASSIST: the detector in use draws its first boxes on every new picture
    python src/training_data.py --video clips/game1.mp4 --assist-only
        label assist on this game's pictures that have no model boxes yet
    python src/training_data.py --status          how many pictures and labels there are
    python src/training_data.py --migrate         turn v2's ball-only labels.json into the shape below

The files, all under data/_training/ (never in git: the pictures show the players)
    project.json      {"classes": ["Ball", "Referee", "Player", "Hoop"], "test_game": "<game>" or null}
    frames/<game>/<game>_<frame number, 6 digits>.jpg      the pictures, full size   (frame_path())
    thumbs/<game>/<the same name>.jpg                      small copies for the filmstrip
    labels.json       {"version": 3, "frames": {"<game>/<frame>": RECORD}}
    labels_previous.json      the labels as they were one save earlier (hours of work: kept twice)

RECORD, one per picture
    {"game": "game1", "frame": 1234, "size": [1920, 1080], "time": 41.2 (seconds into the video),
     "status": "unchecked" | "checked" | "skipped",
     "boxes": [{"cls": "Player", "cx": 612.5, "cy": 300.0, "w": 80.0, "h": 190.0,     (pixels of the full picture)
                "source": "model" | "coach", "conf": 0.97 or null}],
     "conditions": {"light": "good" | "dim", "blur": true | false, "zoom": "wide" | "normal"},
     "queue_reason": "model unsure" | "dim" | "blur" | "small ball" | ""}
    "checked" means the coach looked at the picture and its boxes are right (model boxes included).
    "skipped" means the coach could not tell; such a picture is never trained on.

How the conditions are guessed (see light_of, blur_of, zoom_of): light from the mean brightness, blur from
the sharpness of the edges (the variance of the Laplacian), zoom from how tall the players are in the picture
(the pipeline's tracks.json when the game was processed, else the detector's own Player boxes).
"""
import argparse
import fcntl
import json
import os
import random
import sys
import time
from pathlib import Path

import cv2
import numpy as np

import config

CLASSES = ["Ball", "Referee", "Player", "Hoop"]
# The detector's own class names -> the project's (its other classes, Clock, Overlay, Scoreboard, are dropped).
DETECTOR_NAMES = {"Player": "Player", "Ref": "Referee", "Referee": "Referee", "Hoop": "Hoop", "Ball": "Ball"}
STATUSES = ("unchecked", "checked", "skipped")
QUEUE_REASONS = ("model unsure", "dim", "blur", "small ball")      # hardest first

DEFAULT_FRAMES = 150            # pictures to pick per game
MIN_SECONDS_APART = 0.5         # two picked pictures are never closer in time than this
JPEG_QUALITY = 92
THUMB_WIDTH = 240

ASSIST_CONF = 0.25              # label assist keeps the detector's boxes from this confidence up
SURE_CONF = 0.6                 # below this a model box is "unsure": drawn dashed, and the picture is queued first
DIM_BELOW = 80                  # mean brightness (0-255) under this is a dim gym
BLUR_BELOW = 500                # sharpness under this (Laplacian variance at 960 wide) is a blurred picture
WIDE_BELOW = 0.12               # players shorter than this share of the picture's height: a zoomed-out camera
SMALL_BALL_SHARE = 0.012        # a ball box narrower than this share of the picture's width is a small ball
SECONDS_PER_CHECK = 5           # how long one picture takes to check, for the time estimates

HEAVY_LOCK = "/tmp/hoops-heavy.lock"       # one program at a time loads a model on this laptop


class Problem(Exception):
    """Something the coach can put right; the text says what."""


# ---------------------------------------------------------------- folders and files

def training_dir():
    """data/_training/, created if missing. HOOPS_TRAINING_DIR points it elsewhere for tests with made-up labels."""
    folder = Path(os.environ.get("HOOPS_TRAINING_DIR") or config.DATA_DIR / "_training")
    if not folder.parent.is_dir():
        raise Problem(f"Cannot reach the folder {folder.parent}. {config.SD_CARD_HELP.splitlines()[0]}")
    folder.mkdir(exist_ok=True)
    return folder


def frame_key(game, frame):
    return f"{game}/{int(frame)}"


def frame_name(game, frame):
    return f"{game}_{int(frame):06d}.jpg"


def frame_path(game, frame):
    return training_dir() / "frames" / game / frame_name(game, frame)


def thumb_path(game, frame):
    return training_dir() / "thumbs" / game / frame_name(game, frame)


def frame_files(game):
    """The pictures picked from one game, in time order: a list of (frame number, path)."""
    folder = training_dir() / "frames" / game
    if not folder.is_dir():
        return []
    found = []
    for path in folder.glob("*.jpg"):
        number = path.stem.rsplit("_", 1)[-1]
        if number.isdigit():
            found.append((int(number), path))
    return sorted(found)


def games_with_frames():
    """Names of the games pictures have been picked from, in name order."""
    folder = training_dir() / "frames"
    if not folder.is_dir():
        return []
    return sorted(entry.name for entry in folder.iterdir() if entry.is_dir() and frame_files(entry.name))


def read_json(path, default):
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except ValueError:
        raise Problem(f"{path.name} in {path.parent} is damaged and cannot be read. Move it somewhere else and try again.")


# ---------------------------------------------------------------- the project

def load_project():
    """project.json: {"classes": [...], "test_game": "<game>" or None}. Made with the defaults when missing."""
    saved = read_json(training_dir() / "project.json", {})
    project = {"classes": list(CLASSES), "test_game": None}
    if isinstance(saved, dict):
        if isinstance(saved.get("classes"), list) and saved["classes"]:
            project["classes"] = [str(name) for name in saved["classes"]]
        if isinstance(saved.get("test_game"), str):
            project["test_game"] = saved["test_game"]
    return project


def save_project(project):
    config.save_json(training_dir() / "project.json", project, indent=2)


def set_test_game(game):
    """Keep one game aside for honest testing (None = none). Returns the project."""
    project = load_project()
    if game is not None and game not in games_with_frames():
        raise Problem(f"No pictures have been taken from \"{game}\" yet. Get its pictures first.")
    project["test_game"] = game
    save_project(project)
    return project


# ---------------------------------------------------------------- the labels

def new_record(game, frame, size, seconds=None):
    """A picture's record before anyone looked at it."""
    return {"game": game, "frame": int(frame), "size": [int(size[0]), int(size[1])],
            "time": None if seconds is None else round(float(seconds), 1), "status": "unchecked", "boxes": [],
            "conditions": {"light": "good", "blur": False, "zoom": "normal"}, "queue_reason": ""}


def load_labels():
    """labels.json as {"version": 3, "frames": {key: record}} (empty when nothing was saved yet).

    A damaged labels.json (the SD card has done that before) falls back to the copy from one save
    earlier. A v2 file (ball-only answers) is turned into this shape and saved, see migrate_v2().
    """
    folder = training_dir()
    damaged = []
    for path in (folder / "labels.json", folder / "labels_previous.json"):
        if not path.exists():
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                labels = json.load(f)
            if not isinstance(labels.get("frames"), dict):
                raise ValueError
        except (ValueError, AttributeError):
            damaged.append(path.name)
            continue
        if labels.get("version") != 3:
            labels = migrate_v2(labels)
            save_labels(labels)
        return labels
    if damaged:
        raise Problem(f"The saved labels are damaged and cannot be read ({', '.join(damaged)} in {folder}). "
                      "Move those files somewhere else, then start again.")
    return {"version": 3, "frames": {}}


def save_labels(labels):
    """Write labels.json safely, keeping the version before it as labels_previous.json."""
    folder = training_dir()
    path = folder / "labels.json"
    if path.exists():
        os.replace(path, folder / "labels_previous.json")
    config.save_json(path, labels, indent=1)


def labels_lock():
    """Hold data/_training/labels.lock while labels.json is read and written back.

    The Upload job (this file, as a background program) and the Annotate page (api_training.py) both
    change the file; whoever writes second must have read first, or the other's change is lost.
    Linux lets go of the lock when the file handle is closed or the program ends.
    """
    handle = open(training_dir() / "labels.lock", "w")
    fcntl.flock(handle, fcntl.LOCK_EX)
    return handle


def touched_by_coach(record):
    """True once the coach has answered the picture or drawn a box on it."""
    return record["status"] != "unchecked" or any(box.get("source") == "coach" for box in record["boxes"])


def save_job_labels(labels):
    """The picking job's save: labels.json is read again first, so answers the coach saves on the Annotate
    page while the job runs (minutes, with label assist) are kept. A picture the coach has touched wins
    over the job's copy of it, and the job carries on with the coach's version; everything else the job
    knows (new pictures, model boxes, conditions) is written.
    """
    with labels_lock():
        current = load_labels()
        for key, record in labels["frames"].items():
            theirs = current["frames"].get(key)
            if theirs is not None and touched_by_coach(theirs):
                labels["frames"][key] = theirs
            else:
                current["frames"][key] = record
        save_labels(current)


def migrate_v2(old):
    """v2's ball-only labels -> v3 records.

    v2 answered one question per picture: where is the ball. A "ball" answer becomes a coach's Ball box;
    the picture goes back to "unchecked", because nobody has looked at the players, referees and hoop in it
    yet (label assist draws those on the Upload screen). "skipped" stays skipped. The game kept for testing
    moves to project.json.
    """
    labels = {"version": 3, "frames": {}}
    for key, entry in old.get("frames", {}).items():
        if not isinstance(entry, dict) or "game" not in entry:
            continue
        record = new_record(entry["game"], entry.get("frame", 0), (entry.get("width", 0), entry.get("height", 0)))
        if entry.get("answer") == "skipped":
            record["status"] = "skipped"
        box = entry.get("box")
        if entry.get("answer") == "ball" and isinstance(box, dict):
            record["boxes"].append({"cls": "Ball", "cx": float(box["cx"]), "cy": float(box["cy"]),
                                    "w": float(box["w"]), "h": float(box["h"]), "source": "coach", "conf": None})
        labels["frames"][key] = record
    if isinstance(old.get("test_game"), str):
        project = load_project()
        project["test_game"] = old["test_game"]
        save_project(project)
    return labels


def check_boxes(boxes, size, classes):
    """The boxes as the browser sent them, checked and tidied. Raises Problem with plain words."""
    width, height = size
    clean = []
    for box in boxes if isinstance(boxes, list) else []:
        try:
            cls = str(box["cls"])
            cx, cy, w, h = (float(box[name]) for name in ("cx", "cy", "w", "h"))
        except (KeyError, TypeError, ValueError):
            raise Problem("A box is missing its position. Reload the page and try again.")
        if cls not in classes:
            raise Problem(f"\"{cls}\" is not one of the classes ({', '.join(classes)}).")
        if w < 2 or h < 2:
            continue                                # a box dragged down to nothing is dropped
        # keep the part of the box that is inside the picture
        x1, x2 = max(cx - w / 2, 0), min(cx + w / 2, width)
        y1, y2 = max(cy - h / 2, 0), min(cy + h / 2, height)
        if x2 - x1 < 2 or y2 - y1 < 2:
            continue
        conf = box.get("conf")
        clean.append({"cls": cls, "cx": round((x1 + x2) / 2, 1), "cy": round((y1 + y2) / 2, 1),
                      "w": round(x2 - x1, 1), "h": round(y2 - y1, 1),
                      "source": "model" if box.get("source") == "model" else "coach",
                      "conf": round(float(conf), 2) if isinstance(conf, (int, float)) else None})
    return clean


def set_answer(labels, game, frame, status, boxes, classes=None):
    """Save the coach's answer for one picture: its status and its boxes. Returns the record."""
    key = frame_key(game, frame)
    record = labels["frames"].get(key)
    if record is None:
        raise Problem("That picture is not in the training pictures any more. Reload the page.")
    if status not in STATUSES:
        raise Problem("The answer must be checked, unchecked or skipped.")
    record["boxes"] = check_boxes(boxes, record["size"], classes or load_project()["classes"])
    record["status"] = status
    record["queue_reason"] = queue_reason(record)
    return record


# ---------------------------------------------------------------- the conditions

def light_of(grey):
    """"dim" when the picture is dark on average, else "good"."""
    return "dim" if float(grey.mean()) < DIM_BELOW else "good"


def blur_of(grey):
    """True when the edges are soft (fast play, a moving camera). Measured at one width so every video compares alike."""
    small = cv2.resize(grey, (960, max(1, round(960 * grey.shape[0] / grey.shape[1]))), interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(small, cv2.CV_64F).var()) < BLUR_BELOW


def zoom_of(player_heights, picture_height):
    """"wide" when the players are small in the picture (a zoomed-out camera: the ball is small too), else "normal"."""
    if not player_heights:
        return "normal"
    return "wide" if float(np.median(player_heights)) / picture_height < WIDE_BELOW else "normal"


def condition_words(conditions):
    """"Dim gym · fast play · zoomed out" for the This picture panel."""
    words = ["Dim gym" if conditions.get("light") == "dim" else "Good light"]
    if conditions.get("blur"):
        words.append("fast play")
    if conditions.get("zoom") == "wide":
        words.append("zoomed out")
    return " · ".join(words)


def queue_reason(record):
    """Why a picture is shown early in the hard-pictures queue ("" when it is an ordinary one)."""
    width = record["size"][0] or 1
    model_boxes = [box for box in record["boxes"] if box.get("source") == "model"]
    if any((box.get("conf") or 0) < SURE_CONF for box in model_boxes):
        return "model unsure"
    if record["conditions"].get("light") == "dim":
        return "dim"
    if record["conditions"].get("blur"):
        return "blur"
    if record["conditions"].get("zoom") == "wide" or any(
            box["cls"] == "Ball" and box["w"] < SMALL_BALL_SHARE * width for box in record["boxes"]):
        return "small ball"
    return ""


def tracked_player_heights(game):
    """{frame: [player box heights]} from the pipeline's tracks.json, {} when the game was not processed."""
    path = config.DATA_DIR / game / "tracks.json"          # looking must not create the game's folder
    if not path.is_file():
        return {}
    try:
        tracks = config.load_json(path)
    except SystemExit:
        return {}
    return {record["frame"]: [box["bbox"][3] - box["bbox"][1] for box in record.get("players", [])]
            for record in tracks.get("frames", [])}


# ---------------------------------------------------------------- picking pictures from a game

def choose_frame_numbers(frame_count, fps, how_many, already, seed):
    """Which frames of a video to take, so that `how_many` are spread over the whole of it.

    The rule: always put the next picture somewhere in the middle of the biggest gap that is left.
    That spreads them over the whole game, never puts two close together, and works the same for a
    first pick and for adding more later. `already` are the frame numbers picked before. Returns
    only the new numbers, in time order. (Kept from the v2 frame picker.)
    """
    spin = random.Random(seed)          # the same game always gets the same frames
    min_gap = max(int(round(fps * MIN_SECONDS_APART)), config.FRAME_STRIDE)
    chosen = sorted(already)
    new = []
    while len(chosen) < how_many:
        edges = [-min_gap] + chosen + [frame_count - 1 + min_gap]
        gap, start = max((after - before, before) for before, after in zip(edges, edges[1:]))
        if gap < 2 * min_gap:
            break                       # the video is too short for more pictures this far apart
        low = start + max(min_gap, gap / 4)
        high = start + gap - max(min_gap, gap / 4)
        # The pipeline looks at every 3rd frame; landing on those lets a picture be compared with its tracks.
        frame = int(spin.uniform(low, high)) // config.FRAME_STRIDE * config.FRAME_STRIDE
        frame = min(max(frame, 0), frame_count - 1)
        if frame in chosen:
            break
        chosen = sorted(chosen + [frame])
        new.append(frame)
    return sorted(new)


def save_picture(path, picture, quality=JPEG_QUALITY):
    """Write a JPEG and check it by reading it back (the SD card has damaged files before)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), picture, [cv2.IMWRITE_JPEG_QUALITY, quality])
    back = cv2.imread(str(path))
    if back is None or back.shape != picture.shape:
        raise IOError(f"{path} did not read back correctly. The disk may be failing; try again.")


def make_thumb(game, frame, picture):
    small = cv2.resize(picture, (THUMB_WIDTH, max(1, round(THUMB_WIDTH * picture.shape[0] / picture.shape[1]))),
                       interpolation=cv2.INTER_AREA)
    save_picture(thumb_path(game, frame), small, quality=80)


def pick_frames(video, labels, how_many=DEFAULT_FRAMES):
    """Pick pictures from a game by jumping through the video, save them and their first records.

    The video is never played through: each frame is jumped to, so a full game takes a few minutes and
    does not have to be processed first. how_many is the total wanted for this game; pictures picked
    earlier are kept and count toward it. The records (conditions from the picture itself and, when the
    game was processed, the zoom from tracks.json) go into `labels`, which the caller saves.

    A generator, so the caller can show progress. It yields after every picture:
        {"done": new pictures saved so far, "total": new pictures to save, "seconds_left": estimate or None,
         "unreadable": frames the video would not give, "new": [frame numbers saved]}
    """
    path = config.video_path(video)
    if not path.is_file():
        raise Problem(f"Cannot find the video {path}. Is it still in the clips folder?")
    game = config.video_stem(video)
    capture = cv2.VideoCapture(str(path))
    try:
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
        if frame_count <= 0:
            raise Problem(f"The video {path} cannot be read. Copy it to the clips folder again.")
        already = [frame for frame, _ in frame_files(game)]
        numbers = choose_frame_numbers(frame_count, fps, int(how_many), already, seed=game)
        heights = tracked_player_heights(game)
        progress = {"done": 0, "total": len(numbers), "seconds_left": None, "unreadable": 0, "new": []}
        yield dict(progress)
        started = time.time()
        for index, frame in enumerate(numbers, start=1):
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame)
            ok, picture = capture.read()
            if ok:
                save_picture(frame_path(game, frame), picture)
                make_thumb(game, frame, picture)
                grey = cv2.cvtColor(picture, cv2.COLOR_BGR2GRAY)
                record = new_record(game, frame, (picture.shape[1], picture.shape[0]), frame / fps)
                record["conditions"] = {"light": light_of(grey), "blur": blur_of(grey),
                                        "zoom": zoom_of(heights.get(frame), picture.shape[0])}
                record["queue_reason"] = queue_reason(record)
                labels["frames"][frame_key(game, frame)] = record
                progress["done"] += 1
                progress["new"].append(frame)
            else:
                progress["unreadable"] += 1
            progress["seconds_left"] = (time.time() - started) / index * (len(numbers) - index)
            yield dict(progress)
    finally:
        capture.release()


# ---------------------------------------------------------------- label assist: the detector's first boxes

def heavy_lock():
    """Hold /tmp/hoops-heavy.lock while a model is loaded: one such program at a time on this laptop.

    The same lock the `flock` command uses, so a job started any other way waits for it too. Linux lets
    go of it when the program ends, however it ends.
    """
    handle = open(HEAVY_LOCK, "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("  waiting for the laptop: another program is using a model right now...", flush=True)
        fcntl.flock(handle, fcntl.LOCK_EX)
    return handle


class Detector:
    """The detector in use, loaded once; boxes(picture) gives the project's boxes for one picture."""

    def __init__(self, name):
        from ultralytics import YOLO             # slow import: only when assist actually runs
        self.name = name
        self.path = config.MODELS_DIR / name
        if not self.path.is_file():
            raise Problem(f"The detector {name} is not in the models folder. Choose one on the Settings screen.")
        self.device = config.device()
        self.model = YOLO(str(self.path))
        self.names = {int(number): str(label) for number, label in self.model.names.items()}
        # a first run on a blank picture, so the first real picture is not charged the warm-up time
        self.model(np.zeros((360, 640, 3), np.uint8), imgsz=640, verbose=False, device=self.device)

    def boxes(self, picture):
        # agnostic_nms: one box per person, so nobody is both Player and Referee (as the pipeline does it).
        result = self.model(picture, imgsz=640, conf=ASSIST_CONF, agnostic_nms=True, verbose=False, device=self.device)[0]
        found = []
        for xyxy, conf, number in zip(result.boxes.xyxy.cpu().numpy(), result.boxes.conf.cpu().numpy(),
                                      result.boxes.cls.cpu().numpy().astype(int)):
            cls = DETECTOR_NAMES.get(self.names.get(int(number), ""))
            if not cls:
                continue
            x1, y1, x2, y2 = (float(value) for value in xyxy)
            found.append({"cls": cls, "cx": round((x1 + x2) / 2, 1), "cy": round((y1 + y2) / 2, 1),
                          "w": round(x2 - x1, 1), "h": round(y2 - y1, 1), "source": "model", "conf": round(float(conf), 2)})
        return found


def detector_name():
    """The detector the Settings screen chose (settings.json "detector_model"), else the pipeline's player model."""
    import dash_common
    return dash_common.load_settings().get("detector_model") or config.PLAYER_MODEL.name


def frames_without_assist(labels, game):
    """Frame numbers of a game's unchecked pictures that have no model boxes yet."""
    return [record["frame"] for key, record in labels["frames"].items()
            if record["game"] == game and record["status"] == "unchecked"
            and not any(box.get("source") == "model" for box in record["boxes"])]


def label_assist(detector, labels, game, frames):
    """Run the detector on the given pictures of a game and keep its boxes as "model" boxes.

    Model boxes already on a picture are replaced; the coach's own boxes stay. When the game was not
    processed, the zoom condition is worked out here from the detector's Player boxes. A generator:
    yields {"done", "total", "seconds_left"} after every picture. The caller saves `labels`.
    """
    progress = {"done": 0, "total": len(frames), "seconds_left": None}
    yield dict(progress)
    started = time.time()
    tracked = tracked_player_heights(game)
    for index, frame in enumerate(frames, start=1):
        record = labels["frames"].get(frame_key(game, frame))
        picture = cv2.imread(str(frame_path(game, frame)))
        if record is not None and picture is not None:
            found = detector.boxes(picture)
            record["boxes"] = [box for box in record["boxes"] if box.get("source") != "model"] + found
            if not tracked:
                heights = [box["h"] for box in found if box["cls"] == "Player"]
                record["conditions"]["zoom"] = zoom_of(heights, picture.shape[0])
            record["queue_reason"] = queue_reason(record)
        progress["done"] = index
        progress["seconds_left"] = (time.time() - started) / index * (len(frames) - index)
        yield dict(progress)


# ---------------------------------------------------------------- the numbers the screens show

def records_of(labels, game=None):
    """The records, in game and time order (one game's when `game` is given)."""
    found = [record for record in labels["frames"].values() if game is None or record["game"] == game]
    return sorted(found, key=lambda record: (record["game"], record["frame"]))


def status_counts(records):
    counts = {"pictures": len(records), "checked": 0, "unchecked": 0, "skipped": 0}
    for record in records:
        counts[record["status"]] += 1
    return counts


def condition_counts(records):
    """Pictures by condition: {"good": n, "dim": n, "wide": n, "blur": n} (a picture can be in several)."""
    counts = {"good": 0, "dim": 0, "wide": 0, "blur": 0}
    for record in records:
        counts["dim" if record["conditions"].get("light") == "dim" else "good"] += 1
        if record["conditions"].get("zoom") == "wide":
            counts["wide"] += 1
        if record["conditions"].get("blur"):
            counts["blur"] += 1
    return counts


def class_counts(records, classes, checked_only=True):
    """Boxes per class, on the checked pictures (the labels a model trains on), or on all of them."""
    counts = {name: 0 for name in classes}
    for record in records:
        if checked_only and record["status"] != "checked":
            continue
        for box in record["boxes"]:
            if box["cls"] in counts:
                counts[box["cls"]] += 1
    return counts


def assisted_count(records):
    """How many pictures have model boxes on them."""
    return sum(1 for record in records if any(box.get("source") == "model" for box in record["boxes"]))


def queue(labels, mode="hard", game=None):
    """The unchecked pictures in the order to check them.

    mode "hard": the hard ones first (model unsure, then dim, blur, small ball), then the rest in order;
    "order": game by game, in time order; "unsure": only the pictures the model is unsure about.
    Returns a list of (key, record).
    """
    todo = [record for record in records_of(labels, game) if record["status"] == "unchecked"]
    if mode == "unsure":
        todo = [record for record in todo if record["queue_reason"] == "model unsure"]
    elif mode == "hard":
        rank = {reason: index for index, reason in enumerate(QUEUE_REASONS)}
        todo.sort(key=lambda record: (rank.get(record["queue_reason"], len(QUEUE_REASONS)), record["game"], record["frame"]))
    return [(frame_key(record["game"], record["frame"]), record) for record in todo]


def yolo_lines(record, classes):
    """The picture's boxes as YOLO label lines ("<class number> cx cy w h", shares of the picture), for the dataset step."""
    width, height = record["size"]
    lines = []
    for box in record["boxes"]:
        if box["cls"] in classes:
            lines.append(f"{classes.index(box['cls'])} {box['cx'] / width:.6f} {box['cy'] / height:.6f} "
                         f"{box['w'] / width:.6f} {box['h'] / height:.6f}")
    return lines


# ---------------------------------------------------------------- the command line (the Upload screen's background job)

def nice_time(seconds):
    """"about 40 sec left", "about 3 min left": the dashboard reads these words for its progress bar."""
    if seconds is None:
        return "working out how long"
    if seconds < 60:
        return f"about {max(5, round(seconds / 5) * 5)} sec left"
    return f"about {seconds / 60:.0f} min left"


def print_status():
    labels = load_labels()
    project = load_project()
    games = games_with_frames()
    if not games:
        print("No pictures yet. Get some with:  python src/training_data.py --video clips/<game>.mp4")
        return
    for game in games:
        counts = status_counts(records_of(labels, game))
        kept = "   (kept for testing)" if project["test_game"] == game else ""
        print(f"  {game}: {counts['pictures']} pictures, {counts['checked']} checked, {counts['skipped']} skipped, "
              f"{counts['unchecked']} to do{kept}")
    boxes = class_counts(records_of(labels), project["classes"])
    print("  labels on the checked pictures: " + ", ".join(f"{name} {count}" for name, count in boxes.items()))


def run_job(videos, how_many, assist, assist_only):
    """Pictures from each video, then label assist on the new ones. Prints progress lines the dashboard reads."""
    for video in videos:
        config.require_video(video)
    labels = load_labels()
    if not (training_dir() / "project.json").exists():
        save_project(load_project())            # the project file exists from the first pictures on
    stage, stages = 0, len(videos) * ((0 if assist_only else 1) + (1 if assist else 0))
    for video in videos:
        game = config.video_stem(video)
        if not assist_only:
            stage += 1
            print(f"=== Stage {stage} of {stages}: pictures from {game}", flush=True)
            for progress in pick_frames(video, labels, how_many):
                if progress["total"] == 0:
                    # Printed as a NOTE so the Upload screen shows it instead of "the pictures are in".
                    print(f"  NOTE: no new pictures: {game} already has {len(frame_files(game))}. "
                          f"Ask for more than {len(frame_files(game))} per game to add some.", flush=True)
                    break
                print(f"  {progress['done']} of {progress['total']} pictures from {game} | {nice_time(progress['seconds_left'])}", flush=True)
            save_job_labels(labels)
            if progress["unreadable"]:
                print(f"  {progress['unreadable']} frames could not be read from the video and were left out.")
            if progress["done"] and len(frame_files(game)) < how_many:
                print(f"  NOTE: the video is too short for {how_many} pictures this far apart; {game} has {len(frame_files(game))}.")
        if assist:
            stage += 1
            print(f"=== Stage {stage} of {stages}: assist on {game}", flush=True)
            frames = frames_without_assist(labels, game)
            if not frames:
                print("  every picture has its first boxes already - skipped")
                continue
            name = detector_name()
            print(f"  loading the detector {name} (one model at a time on this laptop)", flush=True)
            lock = heavy_lock()
            detector = Detector(name)
            for progress in label_assist(detector, labels, game, frames):
                print(f"  {progress['done']} of {progress['total']} pictures looked at | {nice_time(progress['seconds_left'])}", flush=True)
                if progress["done"] % 25 == 0:
                    save_job_labels(labels)     # a stopped job keeps the boxes drawn so far
            save_job_labels(labels)
            del detector
            lock.close()
    print("=== All stages are done")


def parse_args():
    parser = argparse.ArgumentParser(description="Pictures, conditions and first boxes for training a detector.")
    parser.add_argument("--video", help="a game video, for example clips/game1.mp4")
    parser.add_argument("--more", action="append", default=[], help="another game video (repeatable)")
    parser.add_argument("--frames", type=int, default=DEFAULT_FRAMES,
                        help=f"how many pictures to have per game in total (default {DEFAULT_FRAMES})")
    parser.add_argument("--no-assist", action="store_true", help="do not run the detector on the new pictures")
    parser.add_argument("--assist-only", action="store_true", help="only run the detector on pictures that have no model boxes yet")
    parser.add_argument("--status", action="store_true", help="how many pictures and labels there are")
    parser.add_argument("--migrate", action="store_true", help="turn a v2 labels.json into the v3 shape")
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        if args.status:
            print_status()
        elif args.migrate:
            load_labels()               # reading a v2 file converts and saves it
            print_status()
        elif args.video:
            run_job([args.video] + args.more, args.frames, assist=not args.no_assist, assist_only=args.assist_only)
        else:
            sys.exit("Say what to do: --video clips/<game>.mp4, --status or --migrate.")
    except Problem as problem:
        sys.exit(str(problem))
    except KeyboardInterrupt:
        print("Stopped by you. The pictures and boxes saved so far are kept.", flush=True)


if __name__ == "__main__":
    main()
