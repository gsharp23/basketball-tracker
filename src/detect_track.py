"""Stage 1, step 1: find the players, referees, hoop and ball, and follow each person.

    python src/detect_track.py --video clips/test.mp4

What it does
    1. Reads every 3rd frame of the video (--stride).
    2. Runs the basketball model on it and keeps Player, Ref, Ball and Hoop boxes.
    3. Follows players and referees from frame to frame so each keeps an ID number.
    4. Writes data/<video>/tracks.json and an annotated video outputs/<video>/tracked.mp4.

Long runs
    A full game takes hours, so the work is saved in parts every few minutes
    (data/<video>/tracks_parts/). If the run stops for any reason, run the same
    command with --resume and it continues after the last saved part.
    Ctrl+C stops cleanly: it finishes the current frame, saves, and exits.
    tracks.json and tracked.mp4 are only written when the end of the video is
    reached, so a run that is stopped early never replaces finished results.
    A finished video is not done again unless you add --start-again.

Known limits
    One real player usually gets several ID numbers over a game (the ID changes
    when the player is hidden or leaves the picture). Ball boxes are recorded
    but are mostly wrong with the current model: do not build stats on them.
"""
import argparse
import os
import shutil
import signal
import statistics
import subprocess
import sys
import time
import warnings

import cv2
import numpy as np

import config

# Save a part after this many processed frames (a few minutes of work).
# If the laptop dies, at most this much work is redone.
SAVE_EVERY = 200

# The model reports boxes down to this confidence. The low ones are only used
# to keep following a player we are already tracking, never to start a new one.
DETECT_CONF = 0.1
# A new ID is only started for a box the model is at least this sure about.
# (Lower lets more spectators in; higher misses players who are partly hidden.)
TRACK_START_CONF = 0.5
# A box needs this confidence to count as a hoop or ball at all.
OBJECT_CONF = 0.25
# A lost player keeps their ID if they are found again within this many seconds.
# Longer than this and the tracker starts picking up the wrong person.
LOST_SECONDS = 2
# The model often draws a second, smaller box on part of a player it already
# boxed. A box with this much of its area inside a surer box is thrown away.
INNER_FRACTION = 0.9
# A video file often claims a few more frames than can be read (the test clip: 12).
# If reading stops more than this many seconds before the claimed end, the file is
# probably damaged and the coach is warned.
SHORT_READ_SECONDS = 2

# A note left in the video parts folder when joining them into tracked.mp4 failed.
JOIN_FAILED = "join_failed.txt"

VIDEO_WIDTH = 1280      # width of the annotated video; the height follows the shape of the game video
# Colours are (blue, green, red).
PLAYER_COLOUR = (0, 220, 0)
REF_COLOUR = (255, 0, 255)
HOOP_COLOUR = (0, 165, 255)
BALL_COLOUR = (255, 255, 255)

# Set by Ctrl+C so the main loop can stop between frames instead of mid-frame.
stop_requested = False


def request_stop(signum, frame):
    global stop_requested
    stop_requested = True
    print("\nStopping after this frame and saving...", flush=True)


def parse_args():
    parser = argparse.ArgumentParser(description="Detect and track players, refs, hoop and ball.")
    parser.add_argument("--video", default=config.DEFAULT_VIDEO, help="video file (default clips/test.mp4)")
    parser.add_argument("--stride", type=int, default=config.FRAME_STRIDE,
                        help="look at every Nth frame (default %(default)s)")
    parser.add_argument("--max-frames", type=int, default=None,
                        help="stop after this many frames in this run (a quick speed test; "
                             "what was done is kept as unfinished work)")
    parser.add_argument("--no-video", action="store_true", help="do not write the annotated video")
    parser.add_argument("--resume", action="store_true", help="continue a run that was stopped")
    parser.add_argument("--start-again", action="store_true",
                        help="do a finished video again from the first frame (every player gets new ID numbers)")
    return parser.parse_args()


# ---------------------------------------------------------------- detection

def detect(model, frame):
    """Run the model on one frame. Returns {class name: (boxes, confidences)}."""
    # agnostic_nms: one box per person, so nobody is reported as both Player and Ref.
    result = model(frame, imgsz=640, conf=DETECT_CONF, agnostic_nms=True, verbose=False)[0]
    boxes = result.boxes.xyxy.cpu().numpy()
    confs = result.boxes.conf.cpu().numpy()
    classes = result.boxes.cls.cpu().numpy().astype(int)
    found = {}
    for number, name in config.CLASS_NAMES.items():
        keep = classes == number
        found[name] = (boxes[keep], confs[keep])
    return found


def drop_inner_boxes(boxes, confs):
    """Remove the extra boxes the model draws inside another person's box.

    Go through the boxes from most to least confident and keep a box unless
    nearly all of it (INNER_FRACTION) lies inside a box we already kept.
    Without this one player is often counted twice and their ID jumps.
    """
    kept = []
    for i in np.argsort(-confs):
        x1, y1, x2, y2 = boxes[i]
        inner = False
        for k in kept:
            overlap_w = min(x2, boxes[k][2]) - max(x1, boxes[k][0])
            overlap_h = min(y2, boxes[k][3]) - max(y1, boxes[k][1])
            if overlap_w > 0 and overlap_h > 0 and overlap_w * overlap_h > INNER_FRACTION * (x2 - x1) * (y2 - y1):
                inner = True
        if not inner:
            kept.append(i)
    return boxes[kept], confs[kept]


def make_tracker(tracked_fps):
    """A ByteTrack tracker set up for the reduced frame rate (about 10 per second)."""
    import supervision as sv
    # supervision 0.30 prints a notice that ByteTrack will move to another package later.
    warnings.filterwarnings("ignore", category=FutureWarning)
    return sv.ByteTrack(
        frame_rate=tracked_fps,
        # ByteTrack adds 0.1 to this number before starting a new ID.
        track_activation_threshold=TRACK_START_CONF - 0.1,
        # ByteTrack counts this in "frames at 30 per second", so 30 * seconds.
        lost_track_buffer=30 * LOST_SECONDS,
        # We skip frames and the camera pans, so boxes move more between looks
        # than usual. 0.9 accepts a match with less overlap than the default 0.8.
        minimum_matching_threshold=0.9,
    )


def track(tracker, boxes, confs, id_map, state):
    """Give each box an ID that follows the person. Returns a list for tracks.json.

    The tracker counts from 1 every time the program starts. We hand out our own
    numbers from state["next_id"] instead, which is saved with every part, so IDs
    from before and after a --resume can never be the same.
    """
    import supervision as sv
    boxes, confs = drop_inner_boxes(boxes, confs)
    tracked = tracker.update_with_detections(sv.Detections(xyxy=boxes, confidence=confs))
    people = []
    for box, conf, tracker_id in zip(tracked.xyxy, tracked.confidence, tracked.tracker_id):
        tracker_id = int(tracker_id)
        if tracker_id not in id_map:
            id_map[tracker_id] = state["next_id"]
            state["next_id"] += 1
        people.append({"id": id_map[tracker_id], "bbox": round_box(box), "conf": round(float(conf), 3)})
    return people


def untracked(boxes, confs):
    """Hoop and ball boxes: no IDs, just the confident ones."""
    return [{"bbox": round_box(box), "conf": round(float(conf), 3)}
            for box, conf in zip(boxes, confs) if conf >= OBJECT_CONF]


def round_box(box):
    return [round(float(v), 1) for v in box]


# ---------------------------------------------------------------- annotated video

def open_video_writer(path, fps, size):
    """Start ffmpeg; we feed it raw pictures (size = width, height) and it writes an H.264 mp4."""
    command = [config.ffmpeg_path(), "-loglevel", "error", "-y",
               "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", "%dx%d" % size, "-r", f"{fps:.3f}", "-i", "-",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", str(path)]
    # start_new_session keeps Ctrl+C away from ffmpeg so it can finish its file.
    return subprocess.Popen(command, stdin=subprocess.PIPE, start_new_session=True)


def close_video_writer(writer):
    writer.stdin.close()
    if writer.wait() != 0:
        sys.exit("ffmpeg could not write the annotated video part. Run again with --resume.")


def draw(frame, record):
    """Shrink the frame to 1280 wide and draw the boxes and ID numbers on it."""
    scale = VIDEO_WIDTH / frame.shape[1]
    # Same shape as the game video, with an even number of rows (H.264 needs that).
    height = 2 * round(scale * frame.shape[0] / 2)
    picture = cv2.resize(frame, (VIDEO_WIDTH, height), interpolation=cv2.INTER_AREA)

    def box(item, colour, label, thickness=2):
        x1, y1, x2, y2 = [int(v * scale) for v in item["bbox"]]
        cv2.rectangle(picture, (x1, y1), (x2, y2), colour, thickness)
        if label:
            (w, h), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(picture, (x1, y1 - h - 6), (x1 + w + 4, y1), colour, -1)
            cv2.putText(picture, label, (x1 + 2, y1 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)

    for item in record["hoops"]:
        box(item, HOOP_COLOUR, "hoop")
    for item in record["ball"]:
        box(item, BALL_COLOUR, "ball?", thickness=1)
    for item in record["refs"]:
        box(item, REF_COLOUR, "ref %d" % item["id"])
    for item in record["players"]:
        box(item, PLAYER_COLOUR, str(item["id"]))
    label = "frame %d" % record["frame"]
    (w, h), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)
    cv2.rectangle(picture, (6, 6), (14 + w, 18 + h), (0, 0, 0), -1)
    cv2.putText(picture, label, (10, 12 + h), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
    return picture


# ---------------------------------------------------------------- saved parts

def parts_dir(video):
    return config.data_dir(video) / "tracks_parts"


def video_parts_dir(video):
    return config.output_dir(video) / "tracked_parts"


def load_parts(video):
    """All saved parts, in order. Empty list if there are none."""
    folder = parts_dir(video)
    if not folder.exists():
        return []
    return [config.load_json(path) for path in sorted(folder.glob("part_*.json"))]


def save_part(video, part_number, frames, stride, next_id):
    parts_dir(video).mkdir(exist_ok=True)
    config.save_json(parts_dir(video) / ("part_%05d.json" % part_number),
                     {"stride": stride, "next_id": next_id, "frames": frames})


# ---------------------------------------------------------------- the main loop

def process_video(video, stride, max_frames, make_video, start_frame, next_id, part_number):
    """Detect and track from start_frame on, saving a part every SAVE_EVERY frames.

    Returns True if the end of the video was reached.
    Only one frame is held in memory at a time.
    """
    from ultralytics import YOLO

    cap = cv2.VideoCapture(str(config.video_path(video)))
    fps = cap.get(cv2.CAP_PROP_FPS)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    tracked_fps = fps / stride
    if start_frame > 0:
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

    print("Loading the model...", flush=True)
    model = YOLO(str(config.PLAYER_MODEL))
    # The model's first run is slow. Do it once on a black picture so the speed shown is honest.
    model(np.zeros((360, 640, 3), np.uint8), imgsz=640, verbose=False)
    player_tracker = make_tracker(tracked_fps)
    ref_tracker = make_tracker(tracked_fps)
    player_ids, ref_ids = {}, {}
    state = {"next_id": next_id}

    frames = []          # the part being built (at most SAVE_EVERY small records)
    writer = None
    done = 0
    frame_index = start_frame
    finished = False
    started = last_print = time.time()

    def close_part():
        nonlocal frames, writer, part_number
        if writer is not None:
            close_video_writer(writer)      # the video part is complete before the data part is saved
            writer = None
        if frames:
            save_part(video, part_number, frames, stride, state["next_id"])
            part_number += 1
            frames = []

    while not stop_requested and (max_frames is None or done < max_frames):
        ok, frame = cap.read()
        if not ok:
            finished = True
            break

        found = detect(model, frame)
        record = {
            "frame": frame_index,
            "players": track(player_tracker, *found["Player"], player_ids, state),
            "refs": track(ref_tracker, *found["Ref"], ref_ids, state),
            "ball": untracked(*found["Ball"]),
            "hoops": untracked(*found["Hoop"]),
        }
        frames.append(record)

        if make_video:
            picture = draw(frame, record)
            if writer is None:
                video_parts_dir(video).mkdir(exist_ok=True)
                writer = open_video_writer(video_parts_dir(video) / ("part_%05d.mp4" % part_number), tracked_fps,
                                           (picture.shape[1], picture.shape[0]))
            writer.stdin.write(picture.tobytes())

        done += 1
        if len(frames) >= SAVE_EVERY:
            close_part()

        now = time.time()
        if now - last_print >= 30:
            last_print = now
            speed = done / (now - started)
            left = max(frame_count - frame_index, 0) / stride / speed
            print(f"  frame {frame_index} of {frame_count} ({100 * frame_index / frame_count:.0f}%)"
                  f" | {speed:.2f} frames/sec | about {nice_time(left)} left", flush=True)

        # Skip the frames in between without decoding them fully.
        for _ in range(stride - 1):
            cap.grab()
        frame_index += stride

    close_part()
    cap.release()
    seconds = time.time() - started
    if done:
        print(f"Processed {done} frames in {nice_time(seconds)} ({done / seconds:.2f} frames/sec).")
    return finished


def nice_time(seconds):
    if seconds < 90:
        return "%d sec" % seconds
    if seconds < 5400:
        return "%d min" % round(seconds / 60)
    return "%.1f hours" % (seconds / 3600)


# ---------------------------------------------------------------- final files

def check_video_opens(source):
    """Stop with a plain message if the file cannot be read as a video (a failed copy looks like this)."""
    cap = cv2.VideoCapture(str(source))
    ok = cap.isOpened() and cap.get(cv2.CAP_PROP_FPS) > 0 and cap.read()[0]
    cap.release()
    if not ok:
        sys.exit(f"{source} could not be opened as a video.\n"
                 "The file may be damaged: copy it to the clips folder again.")


def join_parts(video, stride, parts):
    """The saved parts as one dictionary, in the form of tracks.json."""
    cap = cv2.VideoCapture(str(config.video_path(video)))
    tracks = {
        "video": str(video),
        "fps": round(cap.get(cv2.CAP_PROP_FPS), 3),
        "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        "frame_count": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
        "stride": stride,
        "model": config.PLAYER_MODEL.name,
        "frames": [record for part in parts for record in part["frames"]],
    }
    cap.release()
    return tracks


def clock(seconds):
    """75.3 -> "01:15"."""
    return "%02d:%02d" % (int(seconds) // 60, int(seconds) % 60)


def short_read_warning(tracks):
    """A warning text if the video stopped being readable well before its end, else None.

    Reading stops at the first frame that cannot be read. On a damaged file (the SD
    card has damaged files before) that can be in the middle of the game, and every
    later step would then quietly work on a shortened game.
    """
    reached = tracks["frames"][-1]["frame"] + tracks["stride"]
    missing_seconds = (tracks["frame_count"] - reached) / tracks["fps"]
    if missing_seconds <= SHORT_READ_SECONDS:
        return None
    return (f"The video could only be read up to {clock(reached / tracks['fps'])} of "
            f"{clock(tracks['frame_count'] / tracks['fps'])}, so everything after that is missing from the results. "
            f"The file may be damaged: copy it to the clips folder again, then run  "
            f"python src/main.py --video {tracks['video']} --force")


def write_video(video, part_files):
    """Join the video parts into outputs/<video>/tracked.mp4.

    Returns False only when the join itself failed; the parts must then be kept.
    """
    if not part_files or not all(path.exists() for path in part_files):
        print("No annotated video: some of this run was done with --no-video.")
        return True
    list_file = video_parts_dir(video) / "parts.txt"
    list_file.write_text("".join("file '%s'\n" % path.name for path in part_files))
    target = config.output_dir(video) / "tracked.mp4"
    # Joined under another name first, so a failed join cannot spoil an earlier tracked.mp4.
    unfinished = config.output_dir(video) / "tracked.part.mp4"
    command = [config.ffmpeg_path(), "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
               "-i", str(list_file), "-c", "copy", "-movflags", "+faststart", str(unfinished)]
    if subprocess.run(command).returncode != 0:
        unfinished.unlink(missing_ok=True)
        (video_parts_dir(video) / JOIN_FAILED).write_text("joining these parts into tracked.mp4 failed\n")
        print("Could not join the video parts into tracked.mp4 (is the disk full?).")
        print("They are kept in", video_parts_dir(video))
        print(f"To try the join again:  python src/detect_track.py --video {video} --resume")
        return False
    os.replace(unfinished, target)
    print(f"Wrote {target}")
    return True


def print_summary(tracks):
    frames = tracks["frames"]
    if not frames:
        print("No frames were processed.")
        return
    tracked_fps = tracks["fps"] / tracks["stride"]
    seen = {}       # player ID -> number of frames it appears in
    for record in frames:
        for player in record["players"]:
            seen[player["id"]] = seen.get(player["id"], 0) + 1
    ref_ids = {ref["id"] for record in frames for ref in record["refs"]}
    players_per_frame = sum(len(record["players"]) for record in frames) / len(frames)
    with_hoop = sum(1 for record in frames if record["hoops"])
    with_ball = sum(1 for record in frames if record["ball"])
    short = sum(1 for count in seen.values() if count < tracked_fps)

    print()
    print("Summary")
    print(f"  Frames looked at:     {len(frames)} (every {tracks['stride']} frames, "
          f"{len(frames) / tracked_fps:.0f} seconds of video)")
    print(f"  Players per frame:    {players_per_frame:.1f} on average")
    print(f"  Player ID numbers:    {len(seen)} (a real player gets a new number each time the tracker loses them)")
    if seen:
        print(f"  Typical ID lasts:     {statistics.median(seen.values()) / tracked_fps:.1f} seconds; "
              f"{short} of {len(seen)} last under 1 second")
    print(f"  Referee ID numbers:   {len(ref_ids)}")
    print(f"  Hoop found in:        {100 * with_hoop / len(frames):.0f}% of frames")
    print(f"  Ball box reported in: {100 * with_ball / len(frames):.0f}% of frames (often wrong - do not trust)")


def run(video, stride=config.FRAME_STRIDE, max_frames=None, make_video=True, resume=False, start_again=False):
    """Do the whole step for one video. Returns the tracks (as in tracks.json).

    resume=True continues unfinished work. start_again=True allows a video whose
    detection is already finished to be done again from the first frame.
    """
    source = config.require_video(video)
    if not config.PLAYER_MODEL.exists():
        sys.exit(f"Cannot find the model {config.PLAYER_MODEL}\nIs the models folder in place? (See README.md.)")
    check_video_opens(source)       # before the slow model load, not after it
    config.lock_video(video)

    parts = load_parts(video)
    tracks_file = config.data_dir(video) / "tracks.json"
    if parts and not resume:
        sys.exit(f"There is unfinished work saved for this video ({sum(len(p['frames']) for p in parts)} frames).\n"
                 f"Add --resume to continue it, or delete the folder {parts_dir(video)} to start again.")
    if tracks_file.exists() and not parts and resume:
        if (video_parts_dir(video) / JOIN_FAILED).exists():     # only the annotated video is still missing
            if write_video(video, sorted(video_parts_dir(video).glob("part_*.mp4"))):
                shutil.rmtree(video_parts_dir(video), ignore_errors=True)
        print(f"{tracks_file} is already finished. Nothing to resume.")
        return config.load_json(tracks_file)
    if tracks_file.exists() and not parts and not start_again:
        # Doing it again takes hours and gives every player new ID numbers, so it must be asked for.
        sys.exit(f"The detection of {video} is already finished ({tracks_file}). Nothing was done.\n"
                 f"To do it again from the first frame:  python src/main.py --video {video} --from detect\n"
                 "(every player then gets new ID numbers, so the later stages are done again too)")
    if parts and parts[0]["stride"] != stride:
        sys.exit(f"The saved work used --stride {parts[0]['stride']}. Resume with the same stride.")

    start_frame, next_id = 0, 1
    if parts:
        start_frame = parts[-1]["frames"][-1]["frame"] + stride
        next_id = parts[-1]["next_id"]
        print(f"Resuming {video} at frame {start_frame}. Players get new ID numbers from {next_id} on.")
    else:
        print(f"Tracking {video}, every {stride} frames.")
    part_count = len(parts)
    del parts       # keep memory free while the model runs

    # While the video is being processed, Ctrl+C means "finish this frame, save, stop".
    # Afterwards Ctrl+C goes back to normal so it still works in later steps.
    global stop_requested
    stop_requested = False
    old_int = signal.signal(signal.SIGINT, request_stop)
    old_term = signal.signal(signal.SIGTERM, request_stop)
    try:
        finished = process_video(video, stride, max_frames, make_video, start_frame, next_id, part_count)
    finally:
        signal.signal(signal.SIGINT, old_int)
        signal.signal(signal.SIGTERM, old_term)

    parts = load_parts(video)
    if not parts:
        # Nothing to write, and whatever was there before (a finished tracks.json) is left alone.
        if finished:
            sys.exit(f"No frame of {source} could be read. The file may be damaged: copy it to the clips folder again.")
        sys.exit("Stopped before any frame was processed. Nothing was changed. Run the same command again.")
    tracks = join_parts(video, stride, parts)

    if not finished:
        # tracks.json and tracked.mp4 are only written at the end of the video, so an
        # earlier finished result is never replaced by half a one.
        print(f"\nStopped before the end of the video. The {len(tracks['frames'])} frames done so far are saved")
        print("as unfinished work; tracks.json and tracked.mp4 are written when the end is reached.")
        print(f"To continue:  python src/detect_track.py --video {video} --stride {stride} --resume")
        print_summary(tracks)
        if stop_requested:
            sys.exit("Stopped by Ctrl+C. Progress is saved.")
        return tracks

    warning = short_read_warning(tracks)
    if warning:
        tracks["warning"] = warning     # stats.py repeats it under the stats table
    config.save_json(tracks_file, tracks)
    print(f"Wrote {tracks_file}")
    part_files = [video_parts_dir(video) / ("part_%05d.mp4" % number) for number in range(len(parts))]
    joined = write_video(video, part_files) if make_video else True
    # Everything is in the final files now, so the parts are no longer needed
    # (the video parts are kept if joining them failed).
    shutil.rmtree(parts_dir(video), ignore_errors=True)
    if joined:
        shutil.rmtree(video_parts_dir(video), ignore_errors=True)
    print_summary(tracks)
    if warning:
        print(f"\nWARNING: {warning}")
    return tracks


if __name__ == "__main__":
    args = parse_args()
    run(args.video, args.stride, args.max_frames, not args.no_video, args.resume, args.start_again)
