"""Stage 1: read the jersey number of each tracked player.

    python src/jersey_id.py --video clips/test.mp4

What it does
    1. Reads data/<video>/tracks.json (made by detect_track.py).
    2. For every player ID, looks at the upper body in each frame where the
       player is big enough and not cut off at the side (at most MAX_LOOKS
       frames per ID). A player whose legs are below the bottom of the picture
       is still looked at: those are the players nearest the camera, with the
       biggest numbers.
    3. Reads the digits in each of those pictures with EasyOCR (a text-reading model).
    4. Lets the readings vote. An ID only gets a number when enough readings
       agree (2 for a two-digit number, 3 for a one-digit number); otherwise
       the number is left empty (null). A wrong number is worse than no number.
       Once an ID has twice the votes it needs, the script stops looking at it.
    5. Writes data/<video>/jerseys.json, and one picture per ID under
       crops/<video>/ showing what was looked at and what was read.

Long runs
    Progress is saved every 50 finished IDs. If the run stops (or you press
    Ctrl+C), run the same command with --resume to continue.

Known limits
    A number can only be read when the player is big enough in the picture and
    has their back or chest to the camera. Many short-lived IDs never are.
    A lone "1" is the hardest: the reader notices it in only about 1 clear
    view out of 4.
    One real player has several IDs (see detect_track.py), so the same number
    can show up on several IDs, and both teams can have the same number.
    When another player stands in front, it is that player's number that is
    read (1 of 50 readings on the test clip; it was outvoted).
    Slow: 0.3 to 0.4 seconds per picture at best and 0.8 to 1.9 on a machine
    short of memory, so 2 to 6 minutes for the 20-second test clip and an
    estimated 4 to 20 hours for a full game.
    The settings were tuned on the one test clip only.
"""
import argparse
import json
import sys
import time
import warnings

import cv2
import numpy as np

import config

# EasyOCR keeps its model files here (in the models folder, not in the home folder).
EASYOCR_DIR = config.MODELS_DIR / "easyocr"

# --- which boxes are worth looking at
# Smaller than this (pixels) and the number is too small to read.
MIN_BOX_HEIGHT = 160
# A whole standing or running player is clearly taller than wide. Boxes that are
# not are usually just a head and shoulders at the bottom of the picture.
MIN_TALL_RATIO = 1.3
# A box this close (pixels) to the left or right edge is a player cut in half by it.
# (The bottom edge is fine: it only cuts off the legs. MIN_TALL_RATIO above keeps out
# the boxes there that show no more than head and shoulders.)
EDGE_MARGIN = 5
# The part of the player's box we read: from just below the head to the waist.
TORSO_TOP = 0.10
TORSO_BOTTOM = 0.60
# Where the "veo" logo sits, as fractions of the picture (left, top, right, bottom).
# The logo gets read as "12" or "120", so pictures that touch it are skipped.
WATERMARK = (0.87, 0.87, 0.98, 0.95)

# Most pictures read per ID. An ID with more usable frames than this gets them
# spread evenly over its life. More = more numbers found, but slower: on the
# test clip 20 gave 6 IDs a number and 40 gave 9.
MAX_LOOKS = 40

# --- reading and voting
# The upper-body picture is enlarged to this height before reading.
OCR_HEIGHT = 320
# A reading counts as a vote only if EasyOCR is at least this sure (0 to 1).
MIN_READ_CONF = 0.6
# The number must sit in the middle of the picture, between these fractions of
# its width. A number near the edge belongs to somebody standing next to the player.
CENTRE_RANGE = (0.2, 0.8)
# A single digit is taller than it is wide. EasyOCR quite often reports only
# the "1" of a "12"; the text it found is then about as wide as it is tall.
# So a one-digit reading from a patch wider than this (width / height) is
# thrown away. "1" is the thinnest digit and gets its own, tighter limit.
# (Test clip: real single digits were at most 0.90 wide, real "1"s at most 0.61;
# every half-read "1" was 0.69 or wider.)
ONE_DIGIT_MAX_WIDTH = 0.9
DIGIT_1_MAX_WIDTH = 0.65
# Votes a number needs before it is believed. One-digit numbers need one more,
# because half of a two-digit number can still slip through the check above.
MIN_VOTES_TWO_DIGITS = 2
MIN_VOTES_ONE_DIGIT = 3
# The winner must also have at least this share of all the votes.
MIN_SHARE = 0.6
# A number that is not on the roster counts as half a vote (only used when the
# roster lists players for both teams).
OFF_ROSTER_WEIGHT = 0.5

# Pictures per row in the saved picture for each ID.
SHEET_COLUMNS = 10
SAVE_EVERY = 50


def parse_args():
    parser = argparse.ArgumentParser(description="Read jersey numbers for each tracked player.")
    parser.add_argument("--video", default=config.DEFAULT_VIDEO, help="video file (default clips/test.mp4)")
    parser.add_argument("--no-crops", action="store_true", help="do not save the pictures that were read")
    parser.add_argument("--resume", action="store_true", help="continue a run that was stopped")
    return parser.parse_args()


# ---------------------------------------------------------------- choosing what to look at

def torso_box(bbox, width, height):
    """The upper-body part of a player's box, as whole pixel numbers inside the picture."""
    x1, y1, x2, y2 = bbox
    box_height = y2 - y1
    bottom = y1 + TORSO_BOTTOM * box_height
    if y2 > height - EDGE_MARGIN:
        # The legs are below the picture, so the box is little more than the upper body
        # and the number can sit right at its bottom: read all the way down.
        # (Test clip: reading only the top 60% of such boxes gave 2 IDs no number at
        # all; reading to the bottom gave both the right one.)
        bottom = y2
    return (max(0, int(x1)), max(0, int(y1 + TORSO_TOP * box_height)),
            min(width, int(x2)), min(height, int(bottom)))


def usable(bbox, width, height):
    """True if this box is worth trying to read a number from."""
    x1, y1, x2, y2 = bbox
    if y2 - y1 < MIN_BOX_HEIGHT or y2 - y1 < MIN_TALL_RATIO * (x2 - x1):
        return False
    if x1 < EDGE_MARGIN or x2 > width - EDGE_MARGIN:
        return False
    tx1, ty1, tx2, ty2 = torso_box(bbox, width, height)
    wx1, wy1, wx2, wy2 = WATERMARK
    touches_logo = tx1 < wx2 * width and tx2 > wx1 * width and ty1 < wy2 * height and ty2 > wy1 * height
    return not touches_logo


def plan_looks(tracks, skip_ids):
    """Decide which boxes to look at.

    Returns (by_frame, last_look, player_ids):
      by_frame   {frame: [(ID, bbox), ...]}  the pictures to read in that frame
      last_look  {ID: frame}                 the last frame in which that ID is looked at
    """
    width, height = tracks["width"], tracks["height"]
    boxes = {}          # ID -> [(frame, bbox), ...] usable boxes in time order
    player_ids = set()
    for record in tracks["frames"]:
        for player in record["players"]:
            player_ids.add(player["id"])
            if player["id"] not in skip_ids and usable(player["bbox"], width, height):
                boxes.setdefault(player["id"], []).append((record["frame"], player["bbox"]))

    by_frame = {}
    last_look = {}
    for track_id, items in boxes.items():
        if len(items) > MAX_LOOKS:
            # Too many to read them all: take MAX_LOOKS spread evenly from first to last.
            items = [items[i] for i in np.linspace(0, len(items) - 1, MAX_LOOKS).astype(int)]
        for frame, bbox in items:
            by_frame.setdefault(frame, []).append((track_id, bbox))
        last_look[track_id] = items[-1][0]
    return by_frame, last_look, sorted(player_ids)


# ---------------------------------------------------------------- reading

def load_reader():
    """Start EasyOCR. The first time, it downloads its two model files (about 100 MB)."""
    # torch prints two harmless notices when EasyOCR loads on a computer without a graphics card.
    warnings.filterwarnings("ignore", category=UserWarning)
    import easyocr
    import torch
    # Measured on this 6-core laptop: 4 threads read a picture as fast as 6
    # (about 0.3 s), and leave some room for whatever else is running.
    torch.set_num_threads(4)
    if not config.MODELS_DIR.is_dir():
        sys.exit(f"Cannot reach the folder {config.MODELS_DIR}\n{config.SD_CARD_HELP}")
    EASYOCR_DIR.mkdir(exist_ok=True)
    try:
        return easyocr.Reader(["en"], gpu=False, verbose=False,
                              model_storage_directory=str(EASYOCR_DIR),
                              user_network_directory=str(EASYOCR_DIR / "user_network"))
    except (RuntimeError, MemoryError, OSError) as error:
        sys.exit(f"Could not start the number reader (EasyOCR): {error}\n"
                 "This usually means the computer ran out of memory or the model download failed.\n"
                 "Close other programs and run the command again.")


def read_number(reader, picture):
    """Read one upper-body picture. Returns (number as text, confidence) or (None, 0.0)."""
    scale = OCR_HEIGHT / picture.shape[0]
    big = cv2.resize(picture, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    best = (None, 0.0)
    # The two thresholds are lower than EasyOCR's defaults (0.7 and 0.4) so it
    # notices more numbers. On the test clip that found more and misread none.
    for corners, text, conf in reader.readtext(big, allowlist="0123456789", text_threshold=0.5, low_text=0.3):
        # Jersey numbers have one or two digits. Longer "numbers" are the team
        # name or other lettering forced into digits.
        if len(text) not in (1, 2) or (len(text) == 2 and text[0] == "0" and text != "00"):
            continue
        xs = [point[0] for point in corners]
        ys = [point[1] for point in corners]
        centre = (min(xs) + max(xs)) / 2 / big.shape[1]
        if not CENTRE_RANGE[0] <= centre <= CENTRE_RANGE[1]:
            continue
        if len(text) == 1:
            # Too wide for one digit: a second digit was there and got missed.
            wideness = (max(xs) - min(xs)) / (max(ys) - min(ys))
            if wideness > (DIGIT_1_MAX_WIDTH if text == "1" else ONE_DIGIT_MAX_WIDTH):
                continue
        if conf > best[1]:
            best = (text, float(conf))
    return best


def roster_numbers():
    """All jersey numbers on the roster, or an empty set if it is not filled in for both teams."""
    if not config.ROSTER_PATH.exists():
        return set()
    try:
        roster = json.loads(config.ROSTER_PATH.read_text(encoding="utf-8"))
        teams = [set(team.get("players", {})) for team in roster.values()]
    except (ValueError, AttributeError):
        print(f"Could not understand {config.ROSTER_PATH.name}; carrying on without it.")
        return set()
    # With only one team filled in, a number missing from it could simply be
    # the other team's player, so the roster cannot be used to judge readings.
    if len(teams) < 2 or not all(teams):
        return set()
    return set().union(*teams)


def votes_needed(number):
    return MIN_VOTES_TWO_DIGITS if len(number) == 2 else MIN_VOTES_ONE_DIGIT


def vote(readings, on_roster):
    """Combine one ID's readings into the jerseys.json entry for that ID.

    readings is a list of (number, confidence). Only confident readings vote.
    """
    votes = {}
    confs = {}
    for number, conf in readings:
        if number is not None and conf >= MIN_READ_CONF:
            votes[number] = votes.get(number, 0) + 1
            confs.setdefault(number, []).append(conf)
    result = {"number": None, "confidence": 0.0, "votes": votes}

    weights = {number: float(count) for number, count in votes.items()}
    # A "12" is sometimes read as just "1" or "2", but a digit is never added.
    # So when a two-digit number has enough votes of its own, one-digit
    # readings that are part of it are not counted against it.
    for number, count in votes.items():
        if len(number) == 2 and count >= MIN_VOTES_TWO_DIGITS:
            for digit in number:
                weights.pop(digit, None)
    if on_roster:
        for number in weights:
            if number not in on_roster:
                weights[number] *= OFF_ROSTER_WEIGHT
    if not weights:
        return result

    winner = max(weights, key=weights.get)
    share = weights[winner] / sum(weights.values())
    if votes[winner] >= votes_needed(winner) and share >= MIN_SHARE:
        result["number"] = winner
        # Confidence = share of the votes x how sure the reader was on average.
        result["confidence"] = round(share * sum(confs[winner]) / len(confs[winner]), 2)
    return result


def settled(result):
    """True when an ID's number is so well supported that more looks are a waste of time."""
    number = result["number"]
    return number is not None and result["votes"][number] >= 2 * votes_needed(number)


# ---------------------------------------------------------------- pictures for the coach

def save_sheet(folder, track_id, result, seen):
    """One picture per ID: every upper-body picture that was read, with the reading under it.

    seen is a list of (frame, picture, number, confidence).
    """
    cell_w, cell_h, text_h, title_h = 110, 160, 40, 26
    rows = (len(seen) + SHEET_COLUMNS - 1) // SHEET_COLUMNS
    sheet = np.full((title_h + rows * (cell_h + text_h), SHEET_COLUMNS * cell_w, 3), 40, np.uint8)
    answer = f"number {result['number']} (confidence {result['confidence']})" if result["number"] else "no number"
    cv2.putText(sheet, f"ID {track_id}: {answer}   votes {result['votes']}", (3, 18),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1, cv2.LINE_AA)
    for i, (frame, picture, number, conf) in enumerate(seen):
        x = (i % SHEET_COLUMNS) * cell_w
        y = title_h + (i // SHEET_COLUMNS) * (cell_h + text_h)
        scale = min((cell_w - 4) / picture.shape[1], cell_h / picture.shape[0])
        small = cv2.resize(picture, None, fx=scale, fy=scale)
        sheet[y:y + small.shape[0], x:x + small.shape[1]] = small
        counted = number is not None and conf >= MIN_READ_CONF
        label = f"{number} ({conf:.2f})" if number is not None else "-"
        colour = (0, 255, 0) if counted else (160, 160, 160)   # green = counted as a vote
        cv2.putText(sheet, label, (x + 3, y + cell_h + 15), cv2.FONT_HERSHEY_SIMPLEX, 0.45, colour, 1, cv2.LINE_AA)
        cv2.putText(sheet, f"frame {frame}", (x + 3, y + cell_h + 32), cv2.FONT_HERSHEY_SIMPLEX, 0.38,
                    (200, 200, 200), 1, cv2.LINE_AA)
    if not cv2.imwrite(str(folder / f"track_{track_id:05d}.jpg"), sheet, [cv2.IMWRITE_JPEG_QUALITY, 85]):
        print(f"  (could not save the picture for ID {track_id})")


# ---------------------------------------------------------------- the main loop

def read_video(video, by_frame, last_look, done, reader, on_roster, crops_folder, partial_file):
    """Go through the video once, read the planned pictures, and vote per ID.

    done is the {ID: result} dictionary; each ID is added to it when it is
    settled or when its last planned picture has been read.
    Returns the number of pictures actually read.
    """
    capture = cv2.VideoCapture(str(config.video_path(video)))
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    planned = sum(len(items) for items in by_frame.values())
    seen = {}           # ID -> [(frame, picture, number, confidence), ...] for IDs still being read
    passed = 0          # planned pictures dealt with (read, or skipped because the ID was settled)
    looks_read = 0
    unsaved = 0
    started = time.time()
    # Frames are stepped through in order (grab is fast); only the needed ones are decoded fully.
    for frame in range(max(by_frame) + 1):
        if not capture.grab():
            break
        wanted = by_frame.get(frame, [])
        passed += len(wanted)
        wanted = [(track_id, bbox) for track_id, bbox in wanted if track_id not in done]
        if not wanted:
            continue
        ok, image = capture.retrieve()
        if not ok:
            break
        for track_id, bbox in wanted:
            x1, y1, x2, y2 = torso_box(bbox, width, height)
            picture = image[y1:y2, x1:x2].copy()
            number, conf = read_number(reader, picture)
            seen.setdefault(track_id, []).append((frame, picture, number, conf))
            looks_read += 1
            result = vote([(number, conf) for _, _, number, conf in seen[track_id]], on_roster)
            if settled(result) or frame == last_look[track_id]:
                done[track_id] = result
                if crops_folder:
                    save_sheet(crops_folder, track_id, result, seen[track_id])
                del seen[track_id]
                unsaved += 1
                if unsaved >= SAVE_EVERY:
                    save_results(partial_file, done)
                    unsaved = 0
            if looks_read % 50 == 0:
                seconds_each = (time.time() - started) / looks_read
                print(f"  {looks_read} pictures read ({seconds_each:.2f} s each), "
                      f"at most {(planned - passed) * seconds_each / 60:.0f} min to go", flush=True)
    capture.release()
    # Only happens if the video ended before the last planned frame: use what was read.
    for track_id, readings in seen.items():
        done[track_id] = vote([(number, conf) for _, _, number, conf in readings], on_roster)
        if crops_folder:
            save_sheet(crops_folder, track_id, done[track_id], readings)
    return looks_read


def save_results(path, done):
    config.save_json(path, {"tracks": {str(track_id): done[track_id] for track_id in sorted(done)}}, indent=1)


def print_summary(done, seconds):
    numbered = {track_id: result for track_id, result in done.items() if result["number"]}
    unsure = [track_id for track_id, result in done.items() if not result["number"] and result["votes"]]
    print(f"\nPlayer IDs: {len(done)}.  With a number: {len(numbered)}.  "
          f"Something was read but not convincing: {len(unsure)}.  Nothing readable: {len(done) - len(numbered) - len(unsure)}.")
    by_number = {}
    for track_id in sorted(numbered):
        by_number.setdefault(numbered[track_id]["number"], []).append(track_id)
    in_order = sorted(by_number.items(), key=lambda item: int(item[0]))
    if len(done) <= 40:
        for track_id in sorted(numbered):
            result = numbered[track_id]
            print(f"  ID {track_id:>4}: number {result['number']:>2}   confidence {result['confidence']:.2f}   votes {result['votes']}")
        for track_id in sorted(unsure):
            print(f"  ID {track_id:>4}: no number         votes {done[track_id]['votes']}")
        if by_number:
            print("Numbers found (the two teams can share a number): " +
                  ",  ".join(f"{number}: IDs {ids}" for number, ids in in_order))
    elif by_number:
        # A full game has far too many IDs to list one by one (they are all in jerseys.json).
        print("Numbers found (the two teams can share a number): " +
              ",  ".join(f"{number} on {len(ids)} IDs" for number, ids in in_order))
    print(f"Took {seconds / 60:.1f} minutes.")


def run(video=config.DEFAULT_VIDEO, save_crops=True, resume=False):
    config.require_video(video)
    tracks_file = config.data_dir(video) / "tracks.json"
    config.require_file(tracks_file, f"python src/main.py --video {video}")
    config.lock_video(video)
    jerseys_file = config.data_dir(video) / "jerseys.json"
    partial_file = config.data_dir(video) / "jerseys_partial.json"
    started = time.time()

    done = {}
    if resume and partial_file.exists() and partial_file.stat().st_mtime < tracks_file.stat().st_mtime:
        # The ID numbers change with every detection, so the saved readings belong to other players.
        print("The detection was done again since the stopped run, so the reading starts from the beginning.")
        resume = False
    if resume and partial_file.exists():
        done = {int(track_id): result for track_id, result in config.load_json(partial_file)["tracks"].items()}
        print(f"Continuing: {len(done)} IDs were already finished.")
    else:
        partial_file.unlink(missing_ok=True)    # starting from scratch

    tracks = config.load_json(tracks_file)
    by_frame, last_look, player_ids = plan_looks(tracks, skip_ids=set(done))
    planned = sum(len(items) for items in by_frame.values())
    del tracks
    on_roster = roster_numbers()
    crops_folder = None
    if save_crops:
        crops_folder = config.crops_dir(video)
        if not done:
            # Starting from scratch: clear out the pictures of an earlier run. After a new
            # detection the same ID number is a different player.
            for old in crops_folder.glob("track_*.jpg"):
                old.unlink()

    print(f"Reading jersey numbers in {video}: {len(player_ids)} player IDs, at most {planned} pictures to read.")
    print("Roster: " + (f"{len(on_roster)} numbers listed, others count as half a vote." if on_roster
                        else "not filled in for both teams, so not used."))
    stopped = False
    looks_read = 0
    if by_frame:
        first_time = not any(EASYOCR_DIR.glob("*.pth"))
        print("Starting the number reader" + (" (the first run downloads its model files)" if first_time else "")
              + "...", flush=True)
        try:
            reader = load_reader()
            looks_read = read_video(video, by_frame, last_look, done, reader, on_roster, crops_folder, partial_file)
        except KeyboardInterrupt:
            stopped = True

    if stopped:
        save_results(partial_file, done)
        sys.exit(f"\nStopped by Ctrl+C. {len(done)} IDs are saved.\n"
                 f"To continue:  python src/jersey_id.py --video {video} --resume")

    # IDs that were never big and clear enough to look at still get an (empty) entry.
    for track_id in player_ids:
        done.setdefault(track_id, {"number": None, "confidence": 0.0, "votes": {}})
    save_results(jerseys_file, done)
    partial_file.unlink(missing_ok=True)
    print(f"Wrote {jerseys_file}")
    if crops_folder:
        print(f"Pictures of what was read: {crops_folder}")
    print(f"Pictures read: {looks_read}.")
    print_summary(done, time.time() - started)
    return done


if __name__ == "__main__":
    args = parse_args()
    run(args.video, not args.no_crops, args.resume)
