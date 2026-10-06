"""Ball labels: the first step toward a ball detector trained on the coach's own games.

The models this project has cannot find the ball reliably. The fix is to show a small
model a few hundred frames from our own games with the ball marked. This file does
the parts that need no browser:

    1. pick frames spread over a whole game video              (pick_frames)
    2. keep what the coach answered for each frame             (labels.json)
    3. pack the answered frames into a zip for training        (export_dataset)

The clicking itself is on the dashboard's "Train the model" screen (web/train.html through api_train.py). The
training is done on Google Colab with colab/train_ball_model.ipynb.

From the command line (the dashboard does the same things with buttons):
    python src/ball_labels.py pick --video clips/game1.mp4 [--frames 150]
    python src/ball_labels.py status
    python src/ball_labels.py test-game game3        (or: test-game none)
    python src/ball_labels.py export

What it writes, all in the data folder under data/_training/
    frames/<game>/<game>_<frame number>.jpg     the frames that were picked, full size
    labels.json                                 the coach's answers (below)
    labels_previous.json                        the answers as they were one save earlier
    export/ball_dataset_<n>.zip                 the dataset for training

labels.json
    {"test_game": "game3" or null,              the game kept aside for honest testing
     "frames": {"game1/1234": {"game": "game1", "frame": 1234, "width": 1920, "height": 1080,
                               "answer": "ball",        or "no ball" or "skipped"
                               "box": {"cx": 960.0, "cy": 540.0, "w": 28, "h": 28}}}}
    "box" is only there for "ball": its centre and size in pixels of the full picture.
    A "no ball" frame is a useful training example (it teaches what is NOT a ball).
    A "skipped" frame (the coach could not tell) is never exported.

The zip (the standard Ultralytics YOLO detection layout)
    data.yaml                    one class, "ball"
    images/train|val|test/       the pictures
    labels/train|val|test/       one text file per picture: "0 cx cy w h" as shares of the
                                 picture's width and height; an empty file means "no ball"

Privacy: the frames show the players. They stay on this laptop. Only the coach decides
whether a zip is uploaded anywhere.
"""
import argparse
import json
import os
import random
import shutil
import sys
import time
import zipfile
from datetime import date
from pathlib import Path

import cv2

import config

# Everything this file writes. (The environment variable is for testing with made-up
# labels, so that they can never be mixed up with the coach's own.)
TRAINING_DIR = Path(os.environ.get("HOOPS_TRAINING_DIR") or config.DATA_DIR / "_training")
NOTEBOOK = config.PROJECT_ROOT / "colab" / "train_ball_model.ipynb"

DEFAULT_FRAMES = 150            # frames to pick per game
MIN_SECONDS_APART = 1.0         # two picked frames are never closer in time than this
JPEG_QUALITY = 92

# The ball's size when nothing better is known: this share of the picture's width
# (31 pixels in a 1920-wide picture; on the test clip the ball measured 27 to 34).
# The coach adjusts it with two buttons.
DEFAULT_BALL_SHARE = 0.016
MIN_BALL_PX = 6
# A ball is about this share of the height of a standing player's box. (On the test clip
# this put the first idea within 4 pixels of the real size for 8 of 9 balls.)
BALL_SHARE_OF_PLAYER = 0.15

# How the answered frames of one game are shared out, in time order: the game is cut
# into about this many blocks of neighbouring frames, and whole blocks go to one part.
BLOCKS_PER_GAME = 15
MIN_FRAMES_TO_EXPORT = 10


# ---------------------------------------------------------------- folders and files

def training_dir():
    """data/_training/, created if missing. Raises ValueError with a plain message if the data folder cannot be reached."""
    if not TRAINING_DIR.parent.is_dir():
        raise ValueError(f"Cannot reach the folder {TRAINING_DIR.parent}\n{config.SD_CARD_HELP}")
    TRAINING_DIR.mkdir(exist_ok=True)
    return TRAINING_DIR


def frame_files(stem):
    """The frames picked from one game, in time order: a list of (frame number, path of the picture)."""
    folder = training_dir() / "frames" / stem
    if not folder.is_dir():
        return []
    found = []
    for path in folder.glob("*.jpg"):
        number = path.stem.rsplit("_", 1)[-1]       # "<game>_<frame number>.jpg"
        if number.isdigit():
            found.append((int(number), path))
    return sorted(found)


def games_with_frames():
    """Names of the games frames have been picked from, in name order."""
    folder = training_dir() / "frames"
    if not folder.is_dir():
        return []
    return sorted(entry.name for entry in folder.iterdir() if entry.is_dir() and frame_files(entry.name))


def frame_key(stem, frame):
    """The name of one frame in labels.json: "game1/1234"."""
    return f"{stem}/{frame}"


# ---------------------------------------------------------------- the coach's answers

def load_labels():
    """Read labels.json. Returns {"test_game": ..., "frames": {...}} (empty if nothing was saved yet).

    The SD card has damaged files before, and these answers are hours of the coach's
    work. So if labels.json cannot be read, the copy from one save earlier is used.
    Raises ValueError with a plain message if neither can be read.
    """
    folder = training_dir()
    damaged = []
    for path in (folder / "labels.json", folder / "labels_previous.json"):
        if not path.exists():
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                labels = json.load(f)
            labels["frames"], labels["test_game"]       # both must be there
            return labels
        except (ValueError, KeyError, TypeError):
            damaged.append(path.name)
    if damaged:
        raise ValueError(f"The saved ball labels are damaged and cannot be read ({', '.join(damaged)} in "
                         f"{folder}). The SD card has damaged files before. Move those files somewhere "
                         "else, then start labelling again.")
    return {"test_game": None, "frames": {}}


def save_labels(labels):
    """Write labels.json safely, keeping the version before it as labels_previous.json."""
    folder = training_dir()
    path = folder / "labels.json"
    if path.exists():
        os.replace(path, folder / "labels_previous.json")
    config.save_json(path, labels, indent=1)


def set_answer(labels, stem, frame, width, height, answer, box=None):
    """Put one answer into labels (not saved yet). answer is "ball", "no ball" or "skipped".

    box is (centre x, centre y, size) in pixels, for "ball" only. The box is square,
    because the ball is round.
    """
    entry = {"game": stem, "frame": frame, "width": width, "height": height, "answer": answer}
    if answer == "ball":
        cx, cy, size = box
        entry["box"] = {"cx": round(float(cx), 1), "cy": round(float(cy), 1), "w": int(size), "h": int(size)}
    labels["frames"][frame_key(stem, frame)] = entry


def count_answers(labels, stem, frames=None):
    """How far one game is: {"frames": picked, "ball": n, "no ball": n, "skipped": n, "to do": n}.

    frames is the game's list from frame_files(), for a caller that has it already.
    """
    numbers = {"frames": 0, "ball": 0, "no ball": 0, "skipped": 0, "to do": 0}
    for frame, _ in (frame_files(stem) if frames is None else frames):
        entry = labels["frames"].get(frame_key(stem, frame))
        numbers["frames"] += 1
        numbers[entry["answer"] if entry else "to do"] += 1
    return numbers


# ---------------------------------------------------------------- picking frames from a game

def choose_frame_numbers(frame_count, fps, how_many, already, seed):
    """Which frames of a video to take, so that `how_many` are spread over the whole of it.

    The rule: always put the next frame somewhere in the middle of the biggest gap that
    is left. That spreads the frames over the whole game, never puts two close together,
    and works the same for a first pick and for adding more later. `already` are the
    frame numbers picked before. Returns only the new numbers, in time order.
    """
    spin = random.Random(seed)          # the same game always gets the same frames
    min_gap = max(int(round(fps * MIN_SECONDS_APART)), config.FRAME_STRIDE)
    chosen = sorted(already)
    new = []
    while len(chosen) < how_many:
        # the gaps between neighbours; before the first and after the last frame there is room too
        edges = [-min_gap] + chosen + [frame_count - 1 + min_gap]
        gap, start = max((after - before, before) for before, after in zip(edges, edges[1:]))
        if gap < 2 * min_gap:
            break                       # the video is too short for more frames this far apart
        low = start + max(min_gap, gap / 4)
        high = start + gap - max(min_gap, gap / 4)
        # Detection looks at every 3rd frame; landing on those lets a label be compared with its boxes.
        frame = int(spin.uniform(low, high)) // config.FRAME_STRIDE * config.FRAME_STRIDE
        frame = min(max(frame, 0), frame_count - 1)
        if frame in chosen:
            break
        chosen = sorted(chosen + [frame])
        new.append(frame)
    return sorted(new)


def save_picture(path, picture):
    """Write a JPEG and check it by reading it back (the SD card has damaged files before)."""
    cv2.imwrite(str(path), picture, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    back = cv2.imread(str(path))
    if back is None or back.shape != picture.shape:
        raise IOError(f"{path} did not read back correctly. The disk may be failing; try again.")


def pick_frames(video, how_many=DEFAULT_FRAMES):
    """Pick frames from a game and save them as pictures, by jumping through the video.

    The video is never played through: each frame is jumped to, so a full game takes a
    few minutes, and the game does not have to be processed first. how_many is the total
    wanted for this game; frames picked earlier are kept and count toward it.

    This is a generator, so the caller can show progress. It yields after every frame:
        {"done": new frames saved so far, "total": new frames to save,
         "seconds_left": estimate, "unreadable": frames the video would not give}
    Raises ValueError with a plain message if the video cannot be used.
    """
    path = config.video_path(video)
    if not path.is_file():
        raise ValueError(f"Cannot find the video {path}. Is it still in the clips folder?")
    stem = config.video_stem(video)
    capture = cv2.VideoCapture(str(path))
    try:
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
        if frame_count <= 0:
            raise ValueError(f"The video {path} cannot be read. Copy it to the clips folder again.")
        already = [frame for frame, _ in frame_files(stem)]
        numbers = choose_frame_numbers(frame_count, fps, int(how_many), already, seed=stem)
        folder = training_dir() / "frames" / stem
        folder.mkdir(parents=True, exist_ok=True)
        progress = {"done": 0, "total": len(numbers), "seconds_left": None, "unreadable": 0}
        yield dict(progress)
        started = time.time()
        for index, frame in enumerate(numbers, start=1):
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame)
            ok, picture = capture.read()
            if ok:
                save_picture(folder / f"{stem}_{frame:06d}.jpg", picture)
                progress["done"] += 1
            else:
                progress["unreadable"] += 1
            progress["seconds_left"] = (time.time() - started) / index * (len(numbers) - index)
            yield dict(progress)
    finally:
        capture.release()


# ---------------------------------------------------------------- what the detector already knows

_hints = {}     # the hints of the game looked at last, so its big tracks.json is not read again on every visit


def load_hints(stem):
    """What the detection stage found in the picked frames of a game, if the game has been processed.

    Returns {frame number: {"players": [[x1, y1, x2, y2], ...], "guess": (cx, cy, size) or None}}.
    "guess" is the detector's most confident "ball" box. It is WRONG more often than
    right (see the README), so the page only ever shows it as a guess.
    Returns {} when the game has no tracks.json, which is the usual case.
    """
    path = config.DATA_DIR / stem / "tracks.json"       # not config.data_dir(): looking must not create the folder
    if not path.is_file():
        return {}
    wanted = {frame for frame, _ in frame_files(stem)}
    key = (stem, path.stat().st_mtime, len(wanted))     # changes when the game is processed again or frames are added
    if key in _hints:
        return _hints[key]
    try:
        tracks = config.load_json(path)
    except SystemExit:                                  # a damaged file: carry on without hints
        return {}
    hints = {}
    for record in tracks.get("frames", []):
        if record["frame"] in wanted:
            guess = None
            if record.get("ball"):
                x1, y1, x2, y2 = max(record["ball"], key=lambda item: item["conf"])["bbox"]
                guess = ((x1 + x2) / 2, (y1 + y2) / 2, max(MIN_BALL_PX, round((x2 - x1 + y2 - y1) / 2)))
            hints[record["frame"]] = {"players": [player["bbox"] for player in record.get("players", [])],
                                      "guess": guess}
    _hints.clear()
    _hints[key] = hints
    return hints


def propose_size(labels, stem, width, height, x, y, players=()):
    """A first idea of the ball's size in pixels at a clicked spot. The coach can change it.

    1. If the detector's player boxes are known for this frame: a share of the height of
       the nearest player whose box is whole (not cut off by the edge of the picture).
    2. Else the size the coach gave the ball most often before in this game (the middle value).
    3. Else a fixed share of the picture's width.
    """
    whole = [box for box in players if box[1] > 2 and box[3] < height - 2]
    if whole:
        def distance(box):
            """From the click to the box: 0 inside it (the ball is usually in a player's hands or at their feet)."""
            x1, y1, x2, y2 = box
            return max(x1 - x, 0, x - x2) ** 2 + max(y1 - y, 0, y - y2) ** 2
        nearest = min(whole, key=distance)
        return max(MIN_BALL_PX, round((nearest[3] - nearest[1]) * BALL_SHARE_OF_PLAYER))
    before = sorted(entry["box"]["w"] for entry in labels["frames"].values()
                    if entry["game"] == stem and entry["answer"] == "ball")
    if before:
        return before[len(before) // 2]
    return max(MIN_BALL_PX, round(width * DEFAULT_BALL_SHARE))


# ---------------------------------------------------------------- sharing the frames out: train, val, test

def split_frames(labels):
    """Decide which answered frame goes to training, to checking during training ("val"), or to the final test.

    Returns (parts, by_game, explanation):
        parts        {"train": [entries], "val": [entries], "test": [entries]}
        by_game      True when one whole game is kept for testing (the honest way)
        explanation  plain words about how it was done, for the coach

    The honest test is a whole game the model has never seen. That needs answered frames
    from at least two games, one of them marked as kept for testing. Otherwise every
    game is cut into blocks of neighbouring frames and whole blocks go to each part:
    the test frames then come from the same games as the training frames, so the test
    scores look better than they would on a new game.
    """
    games = {}
    for entry in labels["frames"].values():
        if entry["answer"] != "skipped":
            games.setdefault(entry["game"], []).append(entry)
    test_game = labels.get("test_game")
    by_game = test_game in games and len(games) >= 2

    parts = {"train": [], "val": [], "test": []}
    for stem, entries in games.items():
        if by_game and stem == test_game:
            parts["test"] += entries
            continue
        entries.sort(key=lambda entry: entry["frame"])
        block_size = max(1, len(entries) // BLOCKS_PER_GAME)
        for index, entry in enumerate(entries):
            block = (index // block_size) % 5
            if block == 3:
                parts["val"].append(entry)
            elif block == 4 and not by_game:
                parts["test"].append(entry)
            else:
                parts["train"].append(entry)

    if by_game:
        explanation = (f"The game \"{test_game}\" is kept for testing: none of its frames are used for training, "
                       "so the test scores are an honest measure on a game the model has never seen.")
    elif len(games) >= 2:
        explanation = ("No game is kept for testing (or the one chosen has no answered frames), so the test "
                       "frames are stretches cut out of the same games the model trains on. This is a WEAKER "
                       "test: the scores will look better than they would on a new game. Choose a game to keep "
                       "for testing above.")
    else:
        explanation = ("Only one game has answered frames, so the test frames are stretches cut out of that same "
                       "game. This is a WEAKER test: the scores will look better than they would on a new game "
                       "or in another gym. Label a second game and keep one of them for testing.")
    return parts, by_game, explanation


def yolo_line(entry):
    """One label line in the YOLO form: "0 cx cy w h", each as a share of the picture's width or height."""
    box, width, height = entry["box"], entry["width"], entry["height"]
    # a ball at the edge of the picture: keep only the part of the box that is inside
    x1, x2 = max(box["cx"] - box["w"] / 2, 0), min(box["cx"] + box["w"] / 2, width)
    y1, y2 = max(box["cy"] - box["h"] / 2, 0), min(box["cy"] + box["h"] / 2, height)
    return (f"0 {(x1 + x2) / 2 / width:.6f} {(y1 + y2) / 2 / height:.6f} "
            f"{(x2 - x1) / width:.6f} {(y2 - y1) / height:.6f}\n")


def export_dataset():
    """Pack the answered frames into data/_training/export/ball_dataset_<n>.zip.

    A generator, so the caller can show progress. It yields {"done": frames, "total": frames}
    while it packs, {"checking": True} while it reads the zip back, and last of all
    {"result": {...}} with
        "path", "megabytes", "number" (the n in the name), "by_game", "explanation",
        "counts": {"train": {"frames": n, "ball": n}, "val": ..., "test": ...}, "notebook": path or None
    Raises ValueError with a plain message if there is too little to export.
    """
    labels = load_labels()
    parts, by_game, explanation = split_frames(labels)
    pictures = {}                       # "game1/1234" -> the picture file; an answer whose picture is gone is left out
    for stem in games_with_frames():
        for frame, picture_path in frame_files(stem):
            pictures[frame_key(stem, frame)] = picture_path
    for name in parts:
        parts[name] = [entry for entry in parts[name] if frame_key(entry["game"], entry["frame"]) in pictures]
    counts = {name: {"frames": len(entries), "ball": sum(entry["answer"] == "ball" for entry in entries)}
              for name, entries in parts.items()}
    answered = sum(part["frames"] for part in counts.values())
    if answered < MIN_FRAMES_TO_EXPORT:
        raise ValueError(f"Only {answered} frames have an answer so far. Label at least {MIN_FRAMES_TO_EXPORT} "
                         "before making the zip (a useful model needs a few hundred).")
    if counts["train"]["ball"] == 0:
        raise ValueError("None of the training frames has the ball marked. Click the ball in some frames first.")
    empty = [name for name in parts if not parts[name]]
    if empty:
        raise ValueError(f"There are too few answered frames to fill the \"{empty[0]}\" part. Label more frames.")

    folder = training_dir() / "export"
    folder.mkdir(exist_ok=True)
    taken = [int(path.stem.rsplit("_", 1)[-1]) for path in folder.glob("ball_dataset_*.zip")
             if path.stem.rsplit("_", 1)[-1].isdigit()]
    number = max(taken, default=0) + 1
    path = folder / f"ball_dataset_{number}.zip"

    yaml = (f"# Ball dataset {number}, made by Hoops Tracker on {date.today().isoformat()}.\n"
            f"# Test part: {'one whole game kept aside' if by_game else 'time blocks of the training games (weaker)'}.\n"
            + "".join(f"# {name}: {counts[name]['frames']} frames, {counts[name]['ball']} with the ball\n" for name in parts)
            + "# No \"path\" line: the folders below are looked for next to this file.\n"
            "train: images/train\nval: images/val\ntest: images/test\nnames:\n  0: ball\n")

    total = answered
    done = 0
    temp = path.with_name(path.name + ".tmp")
    # ZIP_STORED: JPEG pictures are compressed already, so squeezing them again only costs time.
    try:
        with zipfile.ZipFile(temp, "w", zipfile.ZIP_STORED) as archive:
            archive.writestr("data.yaml", yaml)
            for name, entries in parts.items():
                for entry in entries:
                    file_name = f"{entry['game']}_{entry['frame']:06d}"
                    archive.write(pictures[frame_key(entry["game"], entry["frame"])], f"images/{name}/{file_name}.jpg")
                    # an empty label file is how YOLO is told "there is no ball in this picture"
                    archive.writestr(f"labels/{name}/{file_name}.txt", yolo_line(entry) if entry["answer"] == "ball" else "")
                    done += 1
                    if done % 10 == 0:
                        yield {"done": done, "total": total}
        # Read every file in the zip back and check it (the SD card has damaged files before).
        # Only a zip that passes gets its real name, so a damaged one is never left to be uploaded.
        yield {"checking": True}
        with zipfile.ZipFile(temp) as archive:
            if archive.testzip() is not None:
                raise IOError(f"{path.name} did not read back correctly, so it was not kept. "
                              "The disk may be failing; try again.")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)        # left over only when the zip failed or the work was broken off

    # The Colab notebook next to the zip, so both can be uploaded from the same Windows folder.
    notebook = None
    if NOTEBOOK.is_file():
        notebook = folder / NOTEBOOK.name
        shutil.copyfile(NOTEBOOK, notebook)
    yield {"result": {"path": path, "megabytes": path.stat().st_size / 1e6, "number": number, "by_game": by_game,
                      "explanation": explanation, "counts": counts, "notebook": notebook}}


# ---------------------------------------------------------------- the command line

def nice_time(seconds):
    """Seconds as plain words: "about 2 min", "under a minute"."""
    if seconds is None:
        return "working out how long"
    if seconds < 60:
        return "under a minute left"
    return f"about {seconds / 60:.0f} min left"


def print_status():
    labels = load_labels()
    games = games_with_frames()
    if not games:
        print("No frames have been picked yet. Pick some with:  python src/ball_labels.py pick --video clips/<game>.mp4")
        return
    for stem in games:
        n = count_answers(labels, stem)
        kept = "   (kept for testing)" if labels["test_game"] == stem else ""
        print(f"  {stem}: {n['frames']} frames picked, {n['ball']} with the ball marked, {n['no ball']} with no ball, "
              f"{n['skipped']} skipped, {n['to do']} still to do{kept}")
    print(split_frames(labels)[2])


def main():
    parser = argparse.ArgumentParser(description="Frames and labels for training a ball detector.")
    commands = parser.add_subparsers(dest="command", required=True)
    pick = commands.add_parser("pick", help="pick frames spread over a game video")
    pick.add_argument("--video", required=True, help="video file, for example clips/game1.mp4")
    pick.add_argument("--frames", type=int, default=DEFAULT_FRAMES,
                      help=f"how many frames to have for this game in total (default {DEFAULT_FRAMES})")
    commands.add_parser("status", help="how many frames are picked and labelled for each game")
    test_game = commands.add_parser("test-game", help="keep one game aside for testing")
    test_game.add_argument("game", help="name of the game, for example game3, or none")
    commands.add_parser("export", help="make the zip of labelled frames for Google Colab")
    args = parser.parse_args()

    try:
        if args.command == "pick":
            config.require_video(args.video)
            for progress in pick_frames(args.video, args.frames):
                print(f"  frame {progress['done']} of {progress['total']}, {nice_time(progress['seconds_left'])}", flush=True)
            if progress["unreadable"]:
                print(f"  {progress['unreadable']} frames could not be read from the video and were left out.")
            stem = config.video_stem(args.video)
            print(f"{stem} now has {len(frame_files(stem))} frames in {training_dir() / 'frames' / stem}")
            print("Label them on the dashboard:  python src/dashboard.py  ->  Train the model")
        elif args.command == "status":
            print_status()
        elif args.command == "test-game":
            labels = load_labels()
            if args.game != "none" and args.game not in games_with_frames():
                sys.exit(f"No frames have been picked from \"{args.game}\". Games with frames: "
                         f"{', '.join(games_with_frames()) or 'none yet'}")
            labels["test_game"] = None if args.game == "none" else args.game
            save_labels(labels)
            print(split_frames(labels)[2])
        elif args.command == "export":
            for step in export_dataset():
                if "done" in step:
                    print(f"  {step['done']} of {step['total']} frames packed", flush=True)
            result = step["result"]
            print(f"Wrote {result['path']}  ({result['megabytes']:.0f} MB)")
            for name, part in result["counts"].items():
                print(f"  {name}: {part['frames']} frames, {part['ball']} with the ball")
            print(result["explanation"])
            print("Uploading this zip anywhere means those frames, which show the players, leave this laptop.")
    except ValueError as problem:
        sys.exit(str(problem))


if __name__ == "__main__":
    main()
