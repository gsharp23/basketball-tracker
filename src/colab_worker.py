"""Process the games in a Google Drive folder, one after another, on Google Colab.

    python src/colab_worker.py                       keep looking for games until stopped
    python src/colab_worker.py --once                do what is there now, then exit
    python src/colab_worker.py --drive-root /some/folder     a test on a laptop

The Drive folder (made by the coach, filled by the dashboard or by hand)
    models/player_detector.pt      the player finder, uploaded once
    models/easyocr/                the number reader's model files, kept here so they are not
                                   downloaded again in every Colab session
    inbox/<game>.mp4               a game to process
    inbox/<game>/                  the court set-up for it, if the coach has done it on the laptop:
                                   court_setup.json, court_calibration.json, hoops.json, court_views/
    outbox/<game>/data/            every file of data/<game> (tracks, teams, jerseys, positions, stats)
    outbox/<game>/outputs/         the videos and pictures
    outbox/<game>/crops/           the jersey check pictures
    outbox/<game>/status.json      done | failed | needs_court | running, with what happened
    worker.log                     everything this program and main.py printed

What it does for each game
    Copies the video, the court files and the models to the usual folders of the project,
    runs main.py (which skips what is done and carries on with what was stopped), copies
    the results to outbox/<game>/ every minute and at the end, writes status.json, and
    deletes the local copy of the video. The video in the inbox is never touched: the
    dashboard decides when it goes.

Stopping and starting
    A free Colab session ends after about 12 hours, or sooner when it is left idle.
    Because the results are copied to the outbox every minute, nothing is lost: the
    next session copies them back and main.py carries on where it stopped.
    A game whose status is "done" is left alone. "needs_court" waits for the court files
    to appear in inbox/<game>/. "failed" is tried again once per start of this program.

This file is plain Python with nothing Colab-specific, so it can be tested on the laptop
with --drive-root pointing at any folder laid out as above.
"""
import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import config
import court_mapper                     # which court files are missing
import main                             # the stage names and "is this stage done" checks
from dash_common import read_progress   # reads main.py's printed lines the way the dashboard does

DEFAULT_DRIVE_ROOT = "/content/drive/MyDrive/Hoops Tracker"
VIDEO_TYPES = (".mp4", ".mov", ".m4v", ".avi", ".mkv")
# The court set-up made on the laptop (see "Mark the court" in README.md): two files it needs,
# one optional file and the folder of reference pictures.
COURT_FILES = ["court_setup.json", "court_calibration.json", "hoops.json"]
COURT_NEEDED = ["court_setup.json", "court_calibration.json"]
COURT_VIEWS = "court_views"
# How often status.json and the outbox are brought up to date while main.py runs.
UPDATE_SECONDS = 60


def parse_args():
    parser = argparse.ArgumentParser(description="Process the games in a Google Drive folder.")
    parser.add_argument("--drive-root", default=DEFAULT_DRIVE_ROOT,
                        help="the Drive folder with models/, inbox/ and outbox/ (default: %(default)s)")
    parser.add_argument("--once", action="store_true", help="process what is there now and exit, instead of polling")
    parser.add_argument("--poll-seconds", type=int, default=60, help="how often to look in the inbox (default 60)")
    parser.add_argument("--idle-minutes", type=int, default=15,
                        help="stop by itself after this long with nothing to do (default 15; 0 = keep waiting)")
    parser.add_argument("--no-videos", action="store_true",
                        help="leave the videos (tracked.mp4, tactical.mp4) out of the outbox; they are big")
    return parser.parse_args()


# ---------------------------------------------------------------- the Drive folder

class Drive:
    """The paths inside the Drive folder, and the log file."""

    def __init__(self, root):
        self.root = Path(root)
        self.models = self.root / "models"
        self.inbox = self.root / "inbox"
        self.outbox = self.root / "outbox"
        self.log_file = self.root / "worker.log"

    def make_folders(self):
        if not self.root.is_dir():
            sys.exit(f"Cannot find the Drive folder {self.root}\n"
                     "Is Google Drive mounted, and is the folder name at the top of the notebook right?")
        for folder in (self.models, self.models / "easyocr", self.inbox, self.outbox):
            folder.mkdir(exist_ok=True)
        # On Colab the project is a fresh copy from GitHub, which has no data folders (on the
        # laptop they are links to the data folder; those are left as they are).
        for folder in (config.CLIPS_DIR, config.MODELS_DIR, config.DATA_DIR, config.OUTPUTS_DIR, config.CROPS_DIR):
            folder.mkdir(exist_ok=True)

    def log(self, text):
        """One line from this program: on the screen and in worker.log, with the time."""
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {text}"
        print(line, flush=True)
        with open(self.log_file, "a", encoding="utf-8") as log:
            log.write(line + "\n")

    def videos(self):
        """The game videos in the inbox, in name order."""
        return sorted(path for path in self.inbox.iterdir()
                      if path.is_file() and path.suffix.lower() in VIDEO_TYPES and not path.name.startswith("."))

    def court_files_ready(self, stem):
        return all((self.inbox / stem / name).exists() for name in COURT_NEEDED)


# ---------------------------------------------------------------- copying files

def changed(source, target):
    """True when target is missing, or source is bigger/smaller or newer (by more than a second)."""
    if not target.exists():
        return True
    a, b = source.stat(), target.stat()
    return a.st_size != b.st_size or a.st_mtime > b.st_mtime + 1


def copy_file(source, target):
    """Copy one file, keeping its modification time when the target folder allows it.

    Google Drive's folder on the laptop (a drvfs mount) refuses to set times and permissions, so a plain
    shutil.copy2 would copy the file and then stop with "Operation not permitted". The file matters;
    the time is kept where it can be.
    """
    shutil.copyfile(source, target)
    try:
        shutil.copystat(source, target)
    except OSError:
        pass


def copy_tree(source, target, mirror=False, skip_videos=False):
    """Copy the files under source to target, only the new and changed ones.

    mirror=True also deletes files in target that are no longer under source (the
    pipeline removes its unfinished-work folders when a stage finishes; the outbox must not
    keep them, or the next session would "carry on" with work that is already done).
    The copy keeps the modification times where the folder allows it (Google Drive's folder on the laptop
    refuses to set them: there the copy is still made, and changed() falls back to the size).
    """
    source, target = Path(source), Path(target)
    if not source.is_dir():
        return
    wanted = lambda path: not (skip_videos and path.suffix.lower() == ".mp4")
    for path in source.rglob("*"):
        if path.is_file() and wanted(path):
            copy = target / path.relative_to(source)
            if changed(path, copy):
                copy.parent.mkdir(parents=True, exist_ok=True)
                copy_file(path, copy)
    if mirror and target.is_dir():
        for path in target.rglob("*"):
            if path.is_file() and wanted(path) and not (source / path.relative_to(target)).exists():
                path.unlink()
        for folder in sorted(target.rglob("*"), reverse=True):      # deepest first
            if folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()


def read_status(drive, stem):
    path = drive.outbox / stem / "status.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except ValueError:
        return {}


def write_status(drive, stem, status):
    """Write status.json in one go (a temporary file renamed into place), so a reader never sees half of it."""
    folder = drive.outbox / stem
    folder.mkdir(parents=True, exist_ok=True)
    status["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    # The words above are in this machine's clock (UTC on Colab); the numbers below are the moment itself,
    # so the laptop can show the times in its own clock.
    status["updated_at"] = time.time()
    if status.get("started") and not status.get("started_at"):
        status["started_at"] = time.time()
    if status.get("finished") and not status.get("finished_at"):
        status["finished_at"] = time.time()
    temp = folder / "status.json.tmp"
    temp.write_text(json.dumps(status, indent=2), encoding="utf-8")
    os.replace(temp, folder / "status.json")


# ---------------------------------------------------------------- one game

def local_video(drive, video):
    """Copy the video to clips/ (once; a copy cut short by the session ending is done again)."""
    target = config.CLIPS_DIR / video.name
    if changed(video, target):
        drive.log(f"Copying {video.name} to the clips folder ({video.stat().st_size / 1e6:.0f} MB)...")
        copy_file(video, target)
    return f"clips/{video.name}"


def bring_in(drive, stem, video):
    """Everything main.py needs, into the project's folders: the models, the video, the court files
    and the results so far (from the outbox, after a session ended part-way)."""
    copy_tree(drive.models, config.MODELS_DIR)
    if not config.PLAYER_MODEL.exists():
        return None, f"The model is missing: upload player_detector.pt to {drive.models}"
    local = local_video(drive, video)
    outbox = drive.outbox / stem
    copy_tree(outbox / "data", config.data_dir(local))
    copy_tree(outbox / "outputs", config.output_dir(local))
    copy_tree(outbox / "crops", config.crops_dir(local))
    court = drive.inbox / stem
    for name in COURT_FILES:
        if (court / name).exists() and changed(court / name, config.data_dir(local) / name):
            copy_file(court / name, config.data_dir(local) / name)
    copy_tree(court / COURT_VIEWS, config.data_dir(local) / COURT_VIEWS)
    return local, ""


def send_out(drive, stem, local, no_videos):
    """The results so far, to the outbox."""
    outbox = drive.outbox / stem
    copy_tree(config.data_dir(local), outbox / "data", mirror=True)
    copy_tree(config.output_dir(local), outbox / "outputs", mirror=True, skip_videos=no_videos)
    copy_tree(config.crops_dir(local), outbox / "crops", mirror=True)
    # The number reader's model files, so the next session does not download them again.
    copy_tree(config.MODELS_DIR / "easyocr", drive.models / "easyocr")


def stages_done(local):
    return {stage: main.stage_status(local, stage) for stage in main.STAGES}


def progress_line(lines):
    """One plain line from what main.py printed so far: "Stage 1 of 6: detect - frame 300 of 6000 (5%) ..."."""
    progress = read_progress(lines)
    if not progress["step"]:
        return "starting", None
    line = f"Stage {progress['step_number']} of {progress['step_count']}: {progress['step']}"
    if progress["detail"]:
        line += " - " + progress["detail"]
    return line, progress["how_far"]


def run_pipeline(drive, stem, local, status, no_videos):
    """Run main.py on the video, showing and logging its output, updating the outbox every minute.

    Returns main.py's exit code and the lines it printed.
    """
    command = [sys.executable, str(config.PROJECT_ROOT / "src" / "main.py"), "--video", local]
    drive.log("Running: " + " ".join(command))
    env = dict(os.environ, PYTHONUNBUFFERED="1")      # so every printed line arrives at once
    process = subprocess.Popen(command, cwd=config.PROJECT_ROOT, env=env, text=True, errors="replace",
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    lines = []
    last_update = time.time()
    with open(drive.log_file, "a", encoding="utf-8", buffering=1) as log:       # 1 = save line by line
        try:
            for line in process.stdout:
                print(line, end="", flush=True)
                log.write(line)
                lines.append(line.rstrip("\n"))
                # A stage that prints nothing for a while (the top-down video) delays this a little.
                if time.time() - last_update >= UPDATE_SECONDS:
                    status["progress"], status["how_far"] = progress_line(lines)
                    write_status(drive, stem, status)
                    send_out(drive, stem, local, no_videos)
                    last_update = time.time()
        except KeyboardInterrupt:
            # Ctrl+C, or the notebook cell was interrupted. main.py gets the same signal and
            # saves its progress; give it time to do so.
            drive.log("Stopping... (main.py is saving its progress)")
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=300)
            except subprocess.TimeoutExpired:
                process.kill()
            raise
    return process.wait(), lines


def process_game(drive, video, no_videos):
    """Run one game through the pipeline. Returns the state written to status.json."""
    stem = video.stem
    earlier = read_status(drive, stem)
    status = {"state": "running", "started": earlier.get("started") or time.strftime("%Y-%m-%d %H:%M:%S"),
              "finished": None, "stages": {}, "progress": "preparing", "how_far": None, "message": ""}
    write_status(drive, stem, status)
    drive.log(f"--- {video.name}: starting" + (" (carrying on from an earlier session)" if earlier else ""))

    local, problem = bring_in(drive, stem, video)
    if problem:
        status.update(state="failed", finished=time.strftime("%Y-%m-%d %H:%M:%S"), progress="", message=problem)
        write_status(drive, stem, status)
        drive.log(f"--- {video.name}: failed. {problem}")
        return "failed"

    # Which stages are done already is worked out before and after the run, not every minute:
    # the check reads tracks.json, which is big for a full game.
    status["stages"] = stages_done(local)
    try:
        code, lines = run_pipeline(drive, stem, local, status, no_videos)
    except KeyboardInterrupt:
        send_out(drive, stem, local, no_videos)
        status.update(state="failed", finished=time.strftime("%Y-%m-%d %H:%M:%S"), progress="",
                      stages=stages_done(local),
                      message="Stopped by hand. Run the worker again and it carries on where it stopped.")
        write_status(drive, stem, status)
        drive.log(f"--- {video.name}: stopped by hand; the results so far are in the outbox")
        raise

    send_out(drive, stem, local, no_videos)
    stages = stages_done(local)
    if code == 0 and all(state == "done" for state in stages.values()):
        state, message = "done", "Every stage is done."
    elif court_mapper.setup_missing(local) and all(stages[s] == "done" for s in main.STAGES if s not in main.NEEDS_COURT):
        state = "needs_court"
        message = ("Detection, teams and jersey numbers are done. Mark the court on the laptop and put "
                   f"the court files in inbox/{stem}/; the positions and stats are made then.")
    else:
        state = "failed"
        tail = [line for line in lines if line.strip()][-5:]
        message = f"main.py stopped with exit code {code}. Its last lines:\n" + "\n".join(tail)
    status.update(state=state, finished=time.strftime("%Y-%m-%d %H:%M:%S"), progress="", how_far=None,
                  stages=stages, message=message)
    write_status(drive, stem, status)
    # The local copy of the video is not needed any more (the inbox keeps the original). The
    # results stay local too, but the outbox has them all, and on Colab the local disk is wiped anyway.
    (config.CLIPS_DIR / video.name).unlink(missing_ok=True)
    drive.log(f"--- {video.name}: {state}. {message.splitlines()[0]}")
    return state


# ---------------------------------------------------------------- the loop

def games_to_do(drive, attempted):
    """The inbox videos that want work: not done, not waiting for the court, not failed in this run."""
    todo = []
    for video in drive.videos():
        state = read_status(drive, video.stem).get("state")
        if state == "done":
            continue
        if state == "needs_court" and not drive.court_files_ready(video.stem):
            continue
        if attempted.get(video.stem) == "failed":
            continue
        todo.append(video)
    return todo


def run(drive_root, once=False, poll_seconds=60, no_videos=False, idle_minutes=15):
    drive = Drive(drive_root)
    drive.make_folders()
    # (Which device the models run on is printed by main.py when the detection starts.)
    drive.log(f"Worker started. Drive folder: {drive.root}")
    attempted = {}      # stem -> the state it ended in, in this run of the program
    idle_since = None   # when the inbox last had nothing to do
    while True:
        todo = games_to_do(drive, attempted)
        for video in todo:
            attempted[video.stem] = process_game(drive, video, no_videos)
        if once:
            drive.log(f"{len(todo)} game(s) processed; stopping (--once).")
            return
        if todo:
            idle_since = None
        else:
            idle_since = idle_since or time.time()
            waited = (time.time() - idle_since) / 60
            if idle_minutes and waited >= idle_minutes:
                # Stopping by itself: a Colab session that waits all night costs compute units for nothing.
                drive.log(f"Nothing to do for {idle_minutes} minutes; stopping. Run the notebook again when a game is in the inbox.")
                return
            left = f"{max(1, round(idle_minutes - waited))} min" if idle_minutes else "never"
            print(f"Nothing to do. Looking again in {poll_seconds} seconds (stops by itself in {left}; "
                  "Ctrl+C or the stop button stops it now).", flush=True)
        time.sleep(poll_seconds)


if __name__ == "__main__":
    args = parse_args()
    try:
        run(args.drive_root, args.once, args.poll_seconds, args.no_videos, args.idle_minutes)
    except KeyboardInterrupt:
        print("\nWorker stopped. Start it again and it carries on with whatever was not finished.")
