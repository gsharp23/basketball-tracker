"""Dataset versions for training the detector, and the laptop speed test of a model. No web code in here.

The coach checks pictures on the Annotate screen (labels.json). This file turns the checked ones into a
numbered dataset version that the training notebook (colab/train_detector.ipynb) trains on, so a model's
score always points to the exact pictures behind it. The Dataset and Models screens (api_models.py) call
these functions; they also work from the command line:

    python src/training_versions.py --status                          what a new version would hold
    python src/training_versions.py --build [--variation light blur]  build the next version (default: the usual four)
    python src/training_versions.py --try-model detector_v1.pt [--video clips/test.mp4]
                                                                      run a model on a clip and measure frames per second

Files, all under data/_training/ (never in git; the shape is shared with the Upload and Annotate screens)
    project.json            {"classes": ["Ball", "Referee", "Player", "Hoop"], "test_game": "<game>" or null}
    frames/<game>/...jpg    the pictures picked from the games
    labels.json             one entry per picture: {"game", "frame", "size": [w, h], "status": "unchecked" |
                            "checked" | "skipped", "boxes": [{"cls", "cx", "cy", "w", "h" (pixels), "source", "conf"}],
                            "conditions": {"light": "good" | "dim", "blur": true | false, "zoom": "wide" | "normal"},
                            "queue_reason"}. The v2 ball-only file is understood too (its "ball" answers become
                            boxes of class "Ball" on unchecked pictures, as training_data.py converts them).
    versions/v<n>/dataset.zip    the Ultralytics layout: data.yaml (the four classes), images/ and labels/ each with
                            train, val and test, conditions.csv (one line per picture: split, game, frame, light,
                            blur, zoom) and a copy of version.json
    versions/v<n>/version.json   {"built", "counts", "split", "augmentations", "test_game", "games", "gyms", "megabytes"}
models/<name>.json          a model's score card, written by the notebook; try-model adds "speed_fps" and "tried_on"

Only CHECKED pictures go into a version: a picture the coach looked at and agreed with (a checked picture with
no boxes is a useful example of what is not there). Unchecked and skipped pictures stay out.

Privacy: the pictures show the players. A version's zip stays on this laptop until the coach sends it to his own
Google Drive from the Models screen; nothing here uploads anything.
"""
import argparse
import csv
import io
import json
import os
import re
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

import config

CLASSES = ["Ball", "Referee", "Player", "Hoop"]

# The variations the coach can tick on the Dataset screen. The notebook turns each key into Ultralytics'
# own training settings; "on" is how the box starts. The words are the mockup's.
VARIATIONS = [
    {"key": "light", "label": "Darker and brighter", "on": True},
    {"key": "blur", "label": "Motion blur", "on": True},
    {"key": "zoom", "label": "Zoomed out (smaller ball)", "on": True},
    {"key": "tilt", "label": "Slight tilt", "on": True},
    {"key": "mirror", "label": "Mirror left-right", "on": False},
    {"key": "colour", "label": "Colour shift (other kits)", "on": False},
]
SHORT_WORDS = {"light": "Dark", "blur": "blur", "zoom": "zoom", "tilt": "tilt", "mirror": "mirror", "colour": "colour"}

# Train 70%, check ("val") 15%, test 15%. A game is cut into about BLOCKS stretches of neighbouring pictures and
# whole stretches go to one part, so near-identical neighbours never sit on both sides. The pattern spreads the
# three parts over the game: 14 T, 3 V and 3 E in every 20 stretches.
BLOCKS = 20
PATTERN = "TTTVTTTETTTVTTTETTVE"
MIN_PICTURES = 10

# The speed test looks at this many frames, the pipeline's way (every 3rd frame).
TRY_FRAMES = 60
TRY_CONF = 0.25


# ---------------------------------------------------------------- folders and files

def training_dir():
    """data/_training/ (HOOPS_TRAINING_DIR points tests at a scratch copy, so made-up labels never mix with the coach's)."""
    return Path(os.environ.get("HOOPS_TRAINING_DIR") or config.DATA_DIR / "_training")


def models_dir():
    """models/ (HOOPS_MODELS_DIR points tests at a scratch folder)."""
    return Path(os.environ.get("HOOPS_MODELS_DIR") or config.MODELS_DIR)


def versions_dir():
    return training_dir() / "versions"


def read_json(path, default):
    """A JSON file, or `default` when it is missing or damaged. Never stops the program."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def load_project():
    """project.json with both keys present: {"classes": [...], "test_game": stem or None}."""
    saved = read_json(training_dir() / "project.json", {})
    saved = saved if isinstance(saved, dict) else {}
    classes = saved.get("classes")
    if not isinstance(classes, list) or not all(isinstance(name, str) and name for name in classes):
        classes = list(CLASSES)
    test_game = saved.get("test_game")
    return {"classes": classes, "test_game": test_game if isinstance(test_game, str) and test_game else None}


def save_project(changes):
    """Change project.json (only the keys given; other keys another screen keeps are left alone) and return it."""
    folder = training_dir()
    folder.mkdir(parents=True, exist_ok=True)
    saved = read_json(folder / "project.json", {})
    saved = saved if isinstance(saved, dict) else {}
    saved.update(changes)
    config.save_json(folder / "project.json", saved, indent=2)
    return load_project()


# ---------------------------------------------------------------- the labels, as one plain list

def entry_from_v2(key, old):
    """A v2 ball-only answer as a v3 entry, read the way training_data.migrate_v2 converts it.

    "ball" becomes one box of class "Ball"; the picture goes back to "unchecked" either way, because nobody
    has looked at the players, referees and hoop in it yet (a v2 picture must never train the four classes
    as if it were checked). "skipped" stays skipped.
    """
    answer = old.get("answer")
    boxes = []
    if answer == "ball" and isinstance(old.get("box"), dict):
        box = old["box"]
        boxes = [{"cls": "Ball", "cx": box["cx"], "cy": box["cy"], "w": box["w"], "h": box["h"], "source": "coach", "conf": 1.0}]
    game, _, frame = key.partition("/")
    return {"game": old.get("game", game), "frame": int(old.get("frame", frame or 0)),
            "size": [old.get("width"), old.get("height")],
            "status": "skipped" if answer == "skipped" else "unchecked",
            "boxes": boxes, "conditions": {}, "queue_reason": ""}


def load_labels():
    """Every labelled picture as a list of v3 entries (see the top of this file), whatever shape the file has.

    labels.json may be {"frames": {"game/123": entry}}, {"game/123": entry} or a plain list; a v2 file
    (answers "ball" / "no ball" / "skipped") is translated as it is read, nothing is written.
    """
    saved = read_json(training_dir() / "labels.json", {})
    if isinstance(saved, dict) and isinstance(saved.get("frames"), (dict, list)):
        saved = saved["frames"]
    items = saved.items() if isinstance(saved, dict) else enumerate(saved if isinstance(saved, list) else [])
    entries = []
    for key, raw in items:
        if not isinstance(raw, dict):
            continue
        entry = entry_from_v2(str(key), raw) if "answer" in raw and "status" not in raw else dict(raw)
        if "game" not in entry or "frame" not in entry:
            game, _, frame = str(key).partition("/")
            entry.setdefault("game", game)
            entry.setdefault("frame", int(frame) if frame.isdigit() else 0)
        entry["frame"] = int(entry["frame"])
        entry.setdefault("status", "unchecked")
        entry["boxes"] = [box for box in (entry.get("boxes") or []) if isinstance(box, dict)]
        entry["conditions"] = entry.get("conditions") if isinstance(entry.get("conditions"), dict) else {}
        entries.append(entry)
    return entries


def picture_of(entry):
    """The picture file of an entry, or None when it is gone. The Upload screen names them <frame>.jpg; v2 named them <game>_<frame>.jpg."""
    folder = training_dir() / "frames" / str(entry["game"])
    frame = int(entry["frame"])
    for name in (f"{frame:06d}.jpg", f"{entry['game']}_{frame:06d}.jpg", f"{frame}.jpg", f"{frame:06d}.png"):
        if (folder / name).is_file():
            return folder / name
    return None


def picture_size(entry, path):
    """(width, height) of the picture: from the entry when it says, else read from the file."""
    size = entry.get("size") or []
    if len(size) == 2 and all(isinstance(value, (int, float)) and value for value in size):
        return int(size[0]), int(size[1])
    import cv2
    picture = cv2.imread(str(path))
    if picture is None:
        raise ValueError(f"The picture {path} cannot be read. The disk may be failing; pick the frame again.")
    return picture.shape[1], picture.shape[0]


def checked_entries(entries):
    """The checked pictures that are still on disk, each with its "path" filled in."""
    kept = []
    for entry in entries:
        if entry.get("status") != "checked":
            continue
        path = picture_of(entry)
        if path is not None:
            entry = dict(entry, path=path)
            kept.append(entry)
    return kept


def games_of(entries):
    """Per game: {"game", "checked", "unchecked", "skipped", "boxes"}, name order."""
    games = {}
    for entry in entries:
        row = games.setdefault(entry["game"], {"game": entry["game"], "checked": 0, "unchecked": 0, "skipped": 0, "boxes": 0})
        status = entry.get("status") if entry.get("status") in ("checked", "unchecked", "skipped") else "unchecked"
        row[status] += 1
        if status == "checked":
            row["boxes"] += len(entry["boxes"])
    return [games[name] for name in sorted(games)]


# ---------------------------------------------------------------- the split

def split_entries(checked, test_game):
    """Share the checked pictures out: {"train": [...], "val": [...], "test": [...]}, plus how and why.

    Returns (parts, split) with split = {"by_game": bool, "test_game": stem or None, "explanation": words,
    "counts": {"train", "val", "test"}}. The honest test is a whole game the model never sees: that needs
    checked pictures from the test game AND from at least one other game. Otherwise every game is cut into
    stretches (see PATTERN) and the test stretches come from the same games the model trains on, which is
    a weaker test, and the words say so.
    """
    games = {}
    for entry in checked:
        games.setdefault(entry["game"], []).append(entry)
    by_game = bool(test_game) and test_game in games and len(games) >= 2
    parts = {"train": [], "val": [], "test": []}
    for game, entries in games.items():
        if by_game and game == test_game:
            parts["test"] += entries
            continue
        entries.sort(key=lambda entry: entry["frame"])
        block_size = max(1, len(entries) // BLOCKS)
        for index, entry in enumerate(entries):
            letter = PATTERN[(index // block_size) % len(PATTERN)]
            if letter == "V":
                parts["val"].append(entry)
            elif letter == "E" and not by_game:
                parts["test"].append(entry)
            else:
                parts["train"].append(entry)
    if by_game:
        words = (f"The test pictures come from one game kept aside: {test_game}. The model never sees it in training, "
                 "so its score is honest.")
    elif test_game and test_game not in games:
        words = (f"The game kept for testing ({test_game}) has no checked pictures yet, so the test pictures are stretches "
                 "cut out of the same games the model trains on. This is a WEAKER test: the scores will look better than "
                 "on a new game.")
    elif len(games) >= 2:
        words = ("No game is kept for testing, so the test pictures are stretches cut out of the same games the model "
                 "trains on. This is a WEAKER test: the scores will look better than on a new game. Tick \"Keep one whole "
                 "game for testing\".")
    else:
        words = ("Only one game has checked pictures, so the test pictures are stretches cut out of that same game. This "
                 "is a WEAKER test: the scores will look better than on a new game or in another gym. Check pictures from "
                 "a second game and keep one of them for testing.")
    counts = {name: len(entries) for name, entries in parts.items()}
    return parts, {"by_game": by_game, "test_game": test_game if by_game else None, "explanation": words, "counts": counts}


# ---------------------------------------------------------------- the versions on disk

def version_number(folder):
    """versions/v3 -> 3, or None for a folder that is not a version."""
    found = re.fullmatch(r"v(\d+)", folder.name)
    return int(found[1]) if found else None


def list_versions():
    """Every built version, newest first: version.json plus "number", "zip" (relative to data/) and "has_zip"."""
    rows = []
    folder = versions_dir()
    if not folder.is_dir():
        return rows
    for entry in folder.iterdir():
        number = version_number(entry)
        info = read_json(entry / "version.json", None)
        if number is None or not isinstance(info, dict):
            continue
        zip_path = entry / "dataset.zip"
        info.update(number=number, has_zip=zip_path.is_file(), zip_path=str(zip_path),
                    zip_file=str(zip_path.relative_to(config.DATA_DIR)) if zip_path.is_relative_to(config.DATA_DIR) else None)
        rows.append(info)
    return sorted(rows, key=lambda row: -row["number"])


def next_version_number():
    return max((row["number"] for row in list_versions()), default=0) + 1


def gyms_of(games):
    """How many different gyms the games were filmed in, from each game's conditions ("" counts once, as unknown)."""
    gyms = set()
    for game in games:
        info = read_json(config.DATA_DIR / game / "game_info.json", {})
        conditions = info.get("conditions") if isinstance(info, dict) else None
        gym = (conditions or {}).get("gym") if isinstance(conditions, dict) else ""
        gyms.add(str(gym or "").strip().lower())
    return len(gyms)


def yolo_lines(entry, classes, width, height):
    """The label file of one picture: "<class number> cx cy w h" per box, as shares of the picture. Unknown classes are left out."""
    lines = []
    for box in entry["boxes"]:
        if box.get("cls") not in classes:
            continue
        try:
            cx, cy, w, h = (float(box[key]) for key in ("cx", "cy", "w", "h"))
        except (KeyError, TypeError, ValueError):
            continue
        # a box at the edge of the picture: keep only the part that is inside
        x1, x2 = max(cx - w / 2, 0), min(cx + w / 2, width)
        y1, y2 = max(cy - h / 2, 0), min(cy + h / 2, height)
        if x2 - x1 < 1 or y2 - y1 < 1:
            continue
        lines.append(f"{classes.index(box['cls'])} {(x1 + x2) / 2 / width:.6f} {(y1 + y2) / 2 / height:.6f} "
                     f"{(x2 - x1) / width:.6f} {(y2 - y1) / height:.6f}")
    return "\n".join(lines) + ("\n" if lines else "")


def file_name(entry):
    return f"{entry['game']}_{int(entry['frame']):06d}"


def plan_version(augmentations=None):
    """What the next version would hold, without building it: the split, the counts, the words for the page.

    Returns {"number", "checked", "games", "gyms", "split", "boxes": {class: n}, "augmentations", "training_pictures"}.
    """
    project = load_project()
    checked = checked_entries(load_labels())
    parts, split = split_entries(checked, project["test_game"])
    wanted = set(augmentations if augmentations is not None else default_augmentations())
    chosen = [item["key"] for item in VARIATIONS if item["key"] in wanted]       # in the checklist's order
    boxes = {name: 0 for name in project["classes"]}
    for entry in checked:
        for box in entry["boxes"]:
            if box.get("cls") in boxes:
                boxes[box["cls"]] += 1
    games = sorted({entry["game"] for entry in checked})
    return {"number": next_version_number(), "checked": len(checked), "games": games, "gyms": gyms_of(games),
            "split": split, "boxes": boxes, "augmentations": chosen, "classes": project["classes"],
            # every training picture is also shown changed, once per variation: a rough size of what the model sees
            "training_pictures": split["counts"]["train"] * (1 + len(chosen))}


def default_augmentations():
    return [item["key"] for item in VARIATIONS if item["on"]]


def variation_words(keys):
    """"Dark, blur, zoom" for the versions table; "None" when nothing was ticked."""
    words = [SHORT_WORDS[key] for key in keys if key in SHORT_WORDS]
    return ", ".join(words) if words else "None"


def build_version(augmentations=None):
    """Build the next version: data/_training/versions/v<n>/dataset.zip and version.json.

    A generator, so the caller can show progress. It yields {"done", "total", "seconds_left"} while it packs,
    {"checking": True} while it reads the zip back, and last of all {"result": version.json's contents}.
    Raises ValueError with a plain message when there is too little to build from.
    """
    plan = plan_version(augmentations)
    project = load_project()
    classes = project["classes"]
    checked = checked_entries(load_labels())
    parts, split = split_entries(checked, project["test_game"])
    if plan["checked"] < MIN_PICTURES:
        raise ValueError(f"Only {plan['checked']} pictures are checked so far. Check at least {MIN_PICTURES} on the "
                         "Annotate screen before building a version (a useful model needs a few hundred).")
    empty = [name for name in ("train", "val", "test") if not parts[name]]
    if empty:
        raise ValueError(f"Too few checked pictures to fill the \"{empty[0]}\" part. Check more pictures first.")
    if not any(box.get("cls") in classes for entry in parts["train"] for box in entry["boxes"]):
        raise ValueError("None of the training pictures has a box in it. Draw boxes on some pictures first.")

    number = plan["number"]
    folder = versions_dir() / f"v{number}"
    folder.mkdir(parents=True, exist_ok=True)
    zip_path = folder / "dataset.zip"
    built = datetime.now().isoformat(timespec="seconds")
    yaml = (f"# Hoops Tracker dataset version {number}, built {built}.\n"
            f"# {split['explanation']}\n"
            + "".join(f"# {name}: {len(parts[name])} pictures\n" for name in parts)
            + "# No \"path\" line: the folders below are looked for next to this file.\n"
            "train: images/train\nval: images/val\ntest: images/test\nnames:\n"
            + "".join(f"  {index}: {name}\n" for index, name in enumerate(classes)))
    conditions = io.StringIO()
    table = csv.writer(conditions)
    table.writerow(["image", "split", "game", "frame", "light", "blur", "zoom"])
    boxes = {name: 0 for name in classes}
    total = sum(len(entries) for entries in parts.values())
    done = 0
    started = time.time()
    temp = zip_path.with_name(zip_path.name + ".tmp")
    try:
        # ZIP_STORED: the JPEG pictures are compressed already, so squeezing them again only costs time.
        with zipfile.ZipFile(temp, "w", zipfile.ZIP_STORED) as archive:
            archive.writestr("data.yaml", yaml)
            for part, entries in parts.items():
                for entry in entries:
                    width, height = picture_size(entry, entry["path"])
                    name = file_name(entry)
                    archive.write(entry["path"], f"images/{part}/{name}.jpg")
                    # an empty label file is how the model is told "nothing of interest is in this picture"
                    archive.writestr(f"labels/{part}/{name}.txt", yolo_lines(entry, classes, width, height))
                    for box in entry["boxes"]:
                        if box.get("cls") in boxes:
                            boxes[box["cls"]] += 1
                    seen = entry["conditions"]
                    table.writerow([f"{name}.jpg", part, entry["game"], entry["frame"], seen.get("light") or "",
                                    "true" if seen.get("blur") else "false", seen.get("zoom") or ""])
                    done += 1
                    if done % 5 == 0 or done == total:
                        yield {"done": done, "total": total, "seconds_left": (time.time() - started) / done * (total - done)}
            archive.writestr("conditions.csv", conditions.getvalue())
            info = {"built": built, "counts": dict(split["counts"], pictures=total, boxes=boxes), "split": split,
                    "augmentations": plan["augmentations"], "test_game": split["test_game"], "games": plan["games"],
                    "gyms": plan["gyms"], "classes": classes}
            archive.writestr("version.json", json.dumps(info, indent=2))
        # Read every file in the zip back and check it (a failing disk has damaged files before). Only a zip that
        # passes gets its real name, so a damaged one is never left to be sent anywhere.
        yield {"checking": True}
        with zipfile.ZipFile(temp) as archive:
            if archive.testzip() is not None or len(archive.namelist()) != 2 * total + 3:
                raise IOError(f"{zip_path.name} did not read back correctly, so it was not kept. The disk may be failing; try again.")
        os.replace(temp, zip_path)
    finally:
        temp.unlink(missing_ok=True)        # left over only when the build failed or was broken off
    info["megabytes"] = round(zip_path.stat().st_size / 1e6, 1)
    config.save_json(folder / "version.json", info, indent=2)
    yield {"result": dict(info, number=number, zip_path=str(zip_path))}


# ---------------------------------------------------------------- the models and their score cards

def model_cards():
    """Every .pt file in models/ with its score card: [{"name", "megabytes", "card": {...} or {}}], name order."""
    folder = models_dir()
    if not folder.is_dir():
        return []
    rows = []
    for path in sorted(folder.glob("*.pt"), key=lambda path: path.name.lower()):
        card = read_json(path.with_suffix(".json"), {})
        rows.append({"name": path.name, "megabytes": round(path.stat().st_size / 1e6), "card": card if isinstance(card, dict) else {}})
    return rows


def try_model(video, model_name, frames=TRY_FRAMES):
    """Run a model over the first `frames` looked-at frames of a clip the pipeline's way and time it.

    Waits for the heavy lock first (one model at a time on this laptop). Prints progress lines the dashboard
    reads ("  frame 10 of 60 | about 20 sec left"). Writes "speed_fps" and "tried_on" into models/<name>.json
    (the card the notebook wrote, or a new one) and returns the card.
    """
    import fcntl
    import cv2
    path = models_dir() / model_name
    if "/" in model_name or not path.is_file():
        sys.exit(f"There is no model file called {model_name} in the models folder.")
    source = config.require_video(video)
    print("Waiting for the laptop to be free (one model at a time)...", flush=True)
    lock = open("/tmp/hoops-heavy.lock", "w")
    fcntl.flock(lock, fcntl.LOCK_EX)
    import detect_track
    from ultralytics import YOLO
    size = detect_track.picture_size_of(path)
    print(f"Loading {model_name} (pictures {size} wide, like the pipeline would run it)...", flush=True)
    model = YOLO(str(path))
    numbers = detect_track.class_numbers(model.names)
    cap = cv2.VideoCapture(str(source))
    ok, frame = cap.read()
    if not ok:
        sys.exit(f"The clip {video} cannot be read.")
    model(frame, imgsz=size, verbose=False, device="cpu")          # the first run is slow: not timed
    found = {kind: 0 for kind in numbers}
    done = 0
    started = last = time.time()
    while done < frames and ok:
        seen = detect_track.detect(model, frame, numbers, "cpu", size)
        for kind, (boxes, confs) in seen.items():
            if any(conf >= TRY_CONF for conf in confs):
                found[kind] += 1
        done += 1
        if done % 10 == 0 or done == frames:
            speed = done / (time.time() - started)
            print(f"  frame {done} of {frames} | {speed:.2f} frames/sec | about {max(1, round((frames - done) / speed))} sec left", flush=True)
        for _ in range(config.FRAME_STRIDE - 1):
            cap.grab()
        ok, frame = cap.read()
    cap.release()
    seconds = time.time() - started
    speed = round(done / seconds, 2) if seconds else None
    card = read_json(path.with_suffix(".json"), {})
    card = card if isinstance(card, dict) else {}
    card["speed_fps"] = speed
    card["tried_on"] = {"clip": config.video_stem(video), "frames": done, "picture_size": size, "found_in_frames": found,
                        "when": datetime.now().isoformat(timespec="seconds")}
    config.save_json(path.with_suffix(".json"), card, indent=2)
    print(f"Speed on this laptop: {speed} frames/sec at {size} wide "
          f"(a 52-minute game at 10 looks a second would take about {nice_hours(31000 / speed) if speed else '?'}).")
    print("Found in frames: " + ", ".join(f"{kind} {count} of {done}" for kind, count in found.items()))
    print(f"Wrote {path.with_suffix('.json')}")
    return card


def nice_hours(seconds):
    return f"{seconds / 3600:.1f} hours" if seconds >= 5400 else f"{seconds / 60:.0f} min"


# ---------------------------------------------------------------- the command line

def main():
    # Plain flags, not sub-commands, because the dashboard starts the speed test as
    # "python src/training_versions.py --video <clip> --try-model <name>" (dash_common.JOB_KINDS).
    parser = argparse.ArgumentParser(description="Dataset versions for training the detector, and the model speed test.")
    parser.add_argument("--status", action="store_true", help="what the next version would hold")
    parser.add_argument("--build", action="store_true", help="build the next dataset version")
    parser.add_argument("--variation", nargs="*", default=None,
                        help="with --build: the variation keys to tick: " + " ".join(item["key"] for item in VARIATIONS))
    parser.add_argument("--try-model", metavar="NAME", help="run this model (a .pt file in models/) on a clip and measure its speed")
    parser.add_argument("--video", default=config.DEFAULT_VIDEO, help="with --try-model: the clip (default clips/test.mp4)")
    parser.add_argument("--frames", type=int, default=TRY_FRAMES, help="with --try-model: how many frames to look at")
    args = parser.parse_args()
    if not (args.status or args.build or args.try_model):
        parser.error("say what to do: --status, --build or --try-model NAME")
    try:
        if args.status:
            plan = plan_version()
            print(f"Version {plan['number']} would hold {plan['checked']} checked pictures from {len(plan['games'])} game(s), "
                  f"{plan['gyms']} gym(s): train {plan['split']['counts']['train']}, check {plan['split']['counts']['val']}, "
                  f"test {plan['split']['counts']['test']}.")
            print("Boxes: " + ", ".join(f"{name} {count}" for name, count in plan["boxes"].items()))
            print(plan["split"]["explanation"])
            for row in list_versions():
                print(f"  v{row['number']}: {row['counts']['pictures']} pictures, built {row['built']}, variations {variation_words(row['augmentations'])}")
        if args.build:
            for step in build_version(args.variation):
                if "done" in step:
                    print(f"  {step['done']} of {step['total']} pictures packed", flush=True)
            result = step["result"]
            print(f"Built version {result['number']}: {result['zip_path']} ({result['megabytes']} MB)")
            print(result["split"]["explanation"])
            print("Sending this zip anywhere means those pictures, which show the players, leave this laptop.")
        if args.try_model:
            try_model(args.video, args.try_model, args.frames)
    except (ValueError, OSError) as problem:
        sys.exit(str(problem))


if __name__ == "__main__":
    main()
